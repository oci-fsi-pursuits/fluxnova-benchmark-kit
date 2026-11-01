#!/usr/bin/env python3
"""Qualify message bursts, signal fan-out and asynchronous batch observation in fresh local H2."""
import argparse
import json
from pathlib import Path
import platform
import secrets
import socket
import subprocess
import sys
import time
import uuid
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchlib.fluxnova import FluxnovaClient
from benchlib.fnv06_functional import Recorder, save, sha
from benchlib.fnv06_load_qualification import exercise
from benchlib.fnv06_pagination import PaginatedRecorder
from scripts.run_w02_functional import distribution_inventory
from scripts.ci_tools import ASSETS

SOURCES = ['benchlib/fnv06_functional.py', 'benchlib/fnv06_load_qualification.py', 'benchlib/fluxnova.py',
           'scripts/run_fnv06_load_qualification.py', 'scripts/audit_fnv06_load_qualification.py',
           'scripts/audit_fnv06_functional.py', 'scripts/run_w02_functional.py', 'scripts/ci_tools.py', 'load/fnv06_events.js',
           'workloads/fnv06/load-qualification.json', 'workloads/fnv06/message.bpmn', 'workloads/fnv06/signal.bpmn']


def k6_inventory(binary, archive):
    name, digest, member = ASSETS[platform.system()]
    if sha(archive) != digest: raise ValueError('Pinned k6 archive checksum mismatch')
    if name.endswith('.zip'):
        with zipfile.ZipFile(archive) as z: raw = z.read(member)
    else:
        with tarfile.open(archive) as t: raw = t.extractfile(member).read()
    if binary.read_bytes() != raw: raise ValueError('k6 binary differs from the pinned archive')
    return {'version':subprocess.check_output([str(binary), 'version'],text=True).strip(),
            'archive':name,'archive_sha256':digest,'executable_sha256':sha(binary)}


def run(args):
    output, runtime, java = args.output.resolve(), args.runtime.resolve(), args.java.resolve()
    if output.exists(): raise ValueError('Use a fresh output directory; preserve all earlier attempts.')
    if not java.is_file() or not runtime.is_dir(): raise ValueError('Verified Fluxnova and Java 21 are required.')
    inventory = distribution_inventory(runtime)
    generator = k6_inventory(args.k6.resolve(), args.k6_archive.resolve())
    with socket.socket() as probe: probe.bind(('127.0.0.1', args.port))
    output.mkdir(parents=True); (output/'empty-deployments').mkdir()
    sources = [*SOURCES, 'benchlib/fnv06_pagination.py', 'workloads/fnv06/scale-qualification.json']
    for name in sources:
        target = output/'inputs'/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes((ROOT/name).read_bytes())
    corpus_name = 'scale-qualification.json' if args.scale else 'load-qualification.json'
    corpus = json.loads((output/'inputs/workloads/fnv06'/corpus_name).read_text())
    run_id = uuid.uuid4().hex; username, password = 'fnv06local', secrets.token_urlsafe(24)
    manifest = {'study_id':corpus['study_id'], 'run_id':run_id, 'scope':corpus['reporting']['claim'], 'runtime':inventory,
        'java':subprocess.run([str(java), '-version'], capture_output=True, text=True, check=True).stderr.strip(),
        'java_executable_sha256':sha(java), 'database':'fresh embedded H2', 'host_os':sys.platform,
        'source_sha256':{name:sha(output/'inputs'/name) for name in sources}, 'generator':generator,
        'engine_url':'http://127.0.0.1:'+str(args.port)+'/engine-rest',
        'functional_batch_wait_timeout_s':corpus['batch_terminal_timeout_s'], 'batch_poll_interval_s':.5,
        'cloud_resources_accessed':False, 'model_calls':0, 'performance_comparison_complete':False}
    save(output/'manifest.json', manifest)
    config = output/'engine.local.yml'
    config.write_text('''server:
  address: 127.0.0.1
  port: '''+str(args.port)+'''
fluxnova.bpm:
  admin-user:
    id: '''+username+'''
    password: '''+json.dumps(password)+'''
  history-level: full
  authorization:
    enabled: true
  run:
    auth:
      enabled: true
    cors:
      enabled: false
    example:
      enabled: false
spring.datasource:
  url: '''+json.dumps('jdbc:h2:'+str(output/'engine-db').replace('\\','/')+';DB_CLOSE_DELAY=-1;DB_CLOSE_ON_EXIT=FALSE')+'''
  driver-class-name: org.h2.Driver
  username: sa
  password: sa
spring.web.resources:
  static-locations: NULL
''', encoding='utf-8')
    command = [str(java), '-Xms256m', '-Xmx1024m',
        '-Dloader.path='+','.join(str(runtime/p) for p in ('internal/rest','internal/webapps','configuration/userlib')),
        '-Dcamunda.deploymentDir='+str(output/'empty-deployments'), '-jar', str(runtime/'internal/fluxnova-bpm-run-core.jar'),
        '--spring.config.location='+config.as_uri()]
    engine = FluxnovaClient('http://127.0.0.1:'+str(args.port)+'/engine-rest', username, password)
    api = (PaginatedRecorder if args.scale else Recorder)(engine, output)
    summary = {'study_id':corpus['study_id'],'run_id':run_id,'status':'running','cases_collected':[],
        'owned_process_ids':[], 'owned_batch_ids':[], 'cloud_resources_accessed':False, 'model_calls':0,
        'performance_comparison_complete':False}
    process = None; ready = False; log = (output/'engine.log').open('wb')
    try:
        process = subprocess.Popen(command, cwd=output, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic()+180
        while True:
            if process.poll() is not None: raise RuntimeError('Owned engine exited during startup')
            try: version = engine.readiness(); break
            except (OSError, RuntimeError):
                if time.monotonic() >= deadline: raise TimeoutError('Owned engine startup timed out')
                time.sleep(.5)
        if version['version'] != '3.0.1': raise RuntimeError('Engine release mismatch')
        save(output/'engine-version.json', version); ready = True
        exercise(api, corpus, run_id, username, summary, args.k6.resolve())
        summary['status'] = 'collected_pending_independent_audit'
    except BaseException as error:
        summary.update(status='failed', error_type=type(error).__name__, error=str(error))
        save(output/'failure.json', summary)
        raise
    finally:
        cleanup_errors = []
        def cleanup(component, action):
            try: action()
            except Exception as error: cleanup_errors.append({'component':component,'error_type':type(error).__name__})
        if ready and process and process.poll() is None:
            def stop_workflows():
                batches = api.collection('/batch')['rows'] if args.scale else api.get('/batch')['response']
                if not {b['id'] for b in batches} <= set(summary['owned_batch_ids']): raise RuntimeError('Unexpected batch owner')
                for batch in batches:
                    row = api.call('DELETE','/batch/'+batch['id']+'?cascade=false')
                    if row['status'] != 204: raise RuntimeError('Owned batch cleanup failed')
                active = api.collection('/process-instance')['rows'] if args.scale else api.get('/process-instance')['response']
                if not {p['id'] for p in active} <= set(summary['owned_process_ids']): raise RuntimeError('Unexpected process owner')
                for item in active:
                    row = api.call('DELETE','/process-instance/'+item['id']+'?deleteReason=fnv06-owned-cleanup')
                    if row['status'] != 204: raise RuntimeError('Owned process cleanup failed')
                counts = {path:api.get(path+'/count')['response']['count'] for path in
                          ('/process-instance','/event-subscription','/incident','/external-task','/batch')}
                save(output/'final-engine-counts.json', counts)
                if any(counts.values()): raise RuntimeError('Owned workflow cleanup incomplete')
            cleanup('owned_workflows', stop_workflows)
        if process and process.poll() is None:
            def stop_engine():
                process.terminate()
                try: process.wait(timeout=20)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
            cleanup('owned_engine', stop_engine)
        cleanup('log', log.close)
        cleanup('ephemeral_configuration', lambda: config.write_text(config.read_text().replace(json.dumps(password), '"REDACTED_AFTER_SHUTDOWN"'), encoding='utf-8'))
        summary.update(owned_engine_stopped=bool(process and process.poll() is not None), cleanup_errors=cleanup_errors)
        if cleanup_errors: summary['status']='failed'
        save(output/'summary.json', summary)
        save(output/'checksums.json', {'algorithm':'sha256','files':{p.relative_to(output).as_posix():sha(p)
            for p in sorted(output.rglob('*')) if p.is_file() and p.name!='checksums.json'}})
    if cleanup_errors: raise RuntimeError('Cleanup requires investigation')
    print(json.dumps({'status':summary['status'],'case_families':len(summary['cases_collected']),
                      'process_instances':len(summary['owned_process_ids']), 'batches':len(summary['owned_batch_ids'])}))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--runtime', type=Path, default=ROOT/'.tools/fluxnova-3.0.1')
    p.add_argument('--java', type=Path, default=ROOT/'.tools/java21/jdk-21.0.12.1+1-jre/bin/java.exe')
    p.add_argument('--port', type=int, default=29380)
    p.add_argument('--scale', action='store_true', help='Qualify fixed populations 10/100/1000 with counted pagination; no performance claim.')
    asset = ASSETS[platform.system()]
    p.add_argument('--k6', type=Path, default=ROOT/'.tools/ci'/asset[2])
    p.add_argument('--k6-archive', type=Path, default=ROOT/'.tools/ci'/asset[0])
    run(p.parse_args())


if __name__=='__main__': main()
