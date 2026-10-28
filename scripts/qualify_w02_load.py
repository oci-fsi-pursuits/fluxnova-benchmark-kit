#!/usr/bin/env python3
"""Qualify the W02 k6 driver on fresh owned local engines; never accesses OCI."""
import argparse
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchlib.evidence import calibrate_generator
from benchlib.fluxnova import FluxnovaClient
from benchlib.runner import extract_event, normalize_event
from benchlib.w02_qualification import assess
from benchlib.workflow_corpus import resolve_mixed_load
from scripts.run_w02_functional import distribution_inventory, require, save, sha
from services.workflow_checks import StudyStore, make_server
from services.workflow_checks_worker import WorkerPool
from services.worker import Client

SCOPE = 'LOCAL LOAD-DRIVER QUALIFICATION - NOT OCI PERFORMANCE'
SOURCES = ['scripts/qualify_w02_load.py','scripts/run_w02_functional.py',
           'load/workflow_checks.js','benchlib/w02_qualification.py','benchlib/runner.py',
           'benchlib/evidence.py','benchlib/analysis.py','benchlib/contracts.py','benchlib/util.py',
           'benchlib/fluxnova.py','services/workflow_checks.py','services/workflow_checks_worker.py',
           'services/worker.py','services/http_support.py','workloads/w02/cases.json',
           'workloads/w02/sequential.bpmn','workloads/w02/parallel.bpmn','benchlib/workflow_corpus.py',
           'workloads/w02/sensitivity-protocol.json']


def verify_k6(binary):
    archive = ROOT/'.tools/k6.zip'
    require(sha(archive) == '112276d495e5741c968e2bc09ea6196099c1275bd6db9ee0875d173c7148ce43', 'k6_archive_not_verified')
    with zipfile.ZipFile(archive) as zipped:
        members = [name for name in zipped.namelist() if name.endswith('/k6.exe') or name == 'k6.exe']
        require(len(members) == 1, 'k6_member_not_found')
        import hashlib
        require(hashlib.sha256(zipped.read(members[0])).hexdigest() == sha(binary), 'k6_binary_modified')
    return {'version':subprocess.run([str(binary),'version'],capture_output=True,text=True,check=True).stdout.strip(),
            'binary_sha256':sha(binary),'archive_sha256':sha(archive)}


@contextmanager
def local_stack(folder, args, inputs):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',args.port))
    username, password = 'w02local', secrets.token_urlsafe(24)
    config = folder/'engine.local.yml'
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
  url: '''+json.dumps('jdbc:h2:'+str(folder/'engine-db').replace('\\','/')+';DB_CLOSE_ON_EXIT=FALSE')+'''
  driver-class-name: org.h2.Driver
  username: sa
  password: sa
spring.web.resources:
  static-locations: NULL
''',encoding='utf-8')
    empty = folder/'empty-deployments'; empty.mkdir()
    command = [str(args.java),'-Xms256m','-Xmx1024m',
               '-Dloader.path='+','.join(str(args.runtime/p) for p in ('internal/rest','internal/webapps','configuration/userlib')),
               '-Dcamunda.deploymentDir='+str(empty),'-jar',str(args.runtime/'internal/fluxnova-bpm-run-core.jar'),
               '--spring.config.location='+config.as_uri()]
    log = (folder/'engine.log').open('wb')
    process = server = pool = None
    errors = []
    def cleanup(name, action):
        try: action()
        except Exception as error: errors.append({'component':name,'error_type':type(error).__name__})
    try:
        process = subprocess.Popen(command,cwd=folder,stdout=log,stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        base = f'http://127.0.0.1:{args.port}/engine-rest'
        engine = FluxnovaClient(base,username,password)
        deadline = time.monotonic()+180
        while True:
            require(process.poll() is None,'engine_exited_during_startup')
            try:
                save(folder/'engine-version.json',engine.readiness()); break
            except (OSError,RuntimeError):
                require(time.monotonic()<deadline,'engine_start_timeout'); time.sleep(.5)
        save(folder/'deployment.json',engine.deploy([inputs/'workloads/w02'/ (arm+'.bpmn') for arm in ('sequential','parallel')]))
        store = StudyStore(folder/'study.sqlite')
        worker_token, registrar_token = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        server = make_server('127.0.0.1',0,store,worker_token,registrar_token)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        support = f'http://127.0.0.1:{server.server_port}'
        auth = 'Basic '+base64.b64encode((username+':'+password).encode()).decode()
        pool = WorkerPool(lambda:Client(base,auth),lambda:Client(support,'Bearer '+worker_token),slots=2,idle_s=.1).start()
        yield engine, store, pool, {'W02_ENGINE_URL':base,'W02_SUPPORT_URL':support,
                                   'W02_USERNAME':username,'W02_PASSWORD':password,'W02_REGISTRATION_TOKEN':registrar_token}
    finally:
        if pool:
            cleanup('workers',pool.stop)
            save(folder/'worker-records.json',pool.records)
        if server:
            cleanup('support_shutdown',server.shutdown)
            cleanup('support_close',server.server_close)
        if process and process.poll() is None:
            def stop():
                process.terminate()
                try: process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=10)
            cleanup('engine',stop)
        cleanup('log',log.close)
        def redact():
            text = config.read_text(encoding='utf-8').replace(json.dumps(password),'"REDACTED_AFTER_SHUTDOWN"')
            config.write_text(text,encoding='utf-8')
        cleanup('config_redaction',redact)
        save(folder/'cleanup.json',{'owned_engine_stopped':bool(process and process.poll() is not None),'errors':errors})
        require(not errors,'owned_stack_cleanup_incomplete')


def execute_lane(folder, args, inputs, lane, calibration):
    events = []
    with local_stack(folder,args,inputs) as (engine,store,pool,credentials):
        run_id = 'w02-'+uuid.uuid4().hex[:12]+'-'+lane['arm']
        env = {key:value for key,value in os.environ.items() if not key.startswith(('W02_','BENCH_'))}
        env.update(credentials)
        env.update({key:str(value) for key,value in {'W02_ARM':lane['arm'],'W02_RATE':lane['rate'],
            'W02_WARMUP_S':args.warmup,'W02_DURATION_S':args.duration,'W02_DRAIN_S':15,
            'W02_VUS':16,'W02_OUTPUT_DIR':folder,'W02_RUN_ID':run_id}.items()})
        corpus_path=inputs/'workloads/w02/mixed-corpus.json'
        corpus=json.loads(corpus_path.read_text(encoding='utf-8')) if corpus_path.exists() else None
        if corpus is not None:
            env.update(W02_CORPUS_FILE=str(corpus_path),W02_CORPUS_SHA256=sha(corpus_path))
        save(folder/'run.json',{'run_id':run_id,'scope':SCOPE,**lane,'worker_slots':2,'fixed_vus':16,
                              'warmup_s':args.warmup,'duration_s':args.duration,'drain_s':15,
                              'deadline_s':2,'completion_poll_ms':100,'fresh_engine_and_backend':True,
                              'case_mode':'mixed_sensitivity' if corpus is not None else 'approved',
                              'corpus_sha256':sha(corpus_path) if corpus is not None else None,
                              'deadline_miss_action':'report' if corpus is not None else 'fail_qualification'})
        command = [str(args.k6),'run','--log-format','json','--no-color',str(inputs/'load/workflow_checks.js')]
        with (folder/'k6.log').open('w',encoding='utf-8') as log:
            child = subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            try:
                returncode = child.wait(timeout=args.warmup+args.duration+60)
            except BaseException:
                child.kill(); child.wait(timeout=10); raise
        save(folder/'load-process.json',{'returncode':returncode})
        malformed = 0
        with (folder/'events.ndjson').open('w',encoding='utf-8') as stream:
            for line in (folder/'k6.log').read_text(encoding='utf-8',errors='replace').splitlines():
                try:
                    event = extract_event(line)
                    if event:
                        event = normalize_event(event); events.append(event)
                        stream.write(json.dumps(event,allow_nan=False)+'\n')
                except (ValueError,TypeError): malformed += 1
        require(malformed == 0, 'malformed_load_events')
        pool.stop()
        state = {key:engine.request('GET',path)['count'] for key,path in {
            'active_instances':'/process-instance/count','external_tasks':'/external-task/count',
            'incidents':'/incident/count','completed_instances':'/history/process-instance/count?finished=true'}.items()}
        with store.connect() as db:
            for key,table in (('effects','effects'),('registrations','cases'),('receipts','receipts')):
                state[key] = db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
        save(folder/'final-state.json',state)
        raw = json.loads((folder/'k6-summary.json').read_text(encoding='utf-8'))
        if corpus is not None:
            require(raw['w02']['corpus_sha256']==sha(corpus_path),'mixed_corpus_hash_mismatch')
        for key,value in {'run_id':run_id,'arm':lane['arm'],'rate':lane['rate'],
                          'duration_s':args.duration,'warmup_s':args.warmup,'fixed_vus':16,
                          'drain_s':15,'deadline_s':2,'poll_interval_ms':100}.items():
            require(raw['w02'][key]==value,'load_settings_differ_from_frozen_lane')
        result = assess(events,raw,returncode,calibration,state,corpus=corpus,require_deadlines=corpus is None)
        if any(record['status']=='fatal_worker_error' for record in pool.records):
            result['issues'].append('fatal_worker_error'); result['status']='failed'
        save(folder/'assessment.json',result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--runtime',type=Path,default=ROOT/'.tools/fluxnova-3.0.1')
    parser.add_argument('--java',type=Path,default=ROOT/'.tools/java21/jdk-21.0.12.1+1-jre/bin/java.exe')
    parser.add_argument('--k6',type=Path,default=ROOT/'.tools/k6/k6-v2.3.0-windows-amd64/k6.exe')
    parser.add_argument('--port',type=int,default=28280)
    parser.add_argument('--warmup',type=int)
    parser.add_argument('--duration',type=int)
    parser.add_argument('--mixed-protocol',type=Path,
                        help='Prespecified mixed qualification; deadline misses are reported separately from validity.')
    args=parser.parse_args()
    mixed=None;protocol_bytes=None
    if args.mixed_protocol is not None:
        protocol_bytes=args.mixed_protocol.read_bytes()
        mixed=resolve_mixed_load(json.loads(protocol_bytes),
              json.loads((ROOT/'workloads/w02/sensitivity-protocol.json').read_text(encoding='utf-8')),
              json.loads((ROOT/'workloads/w02/cases.json').read_text(encoding='utf-8')))
        for option,field in (('warmup','warmup_s'),('duration','duration_s')):
            if getattr(args,option) is not None and getattr(args,option)!=mixed[field]:
                parser.error('Explicit timing differs from the frozen mixed protocol.')
            setattr(args,option,mixed[field])
    else:
        args.warmup=20 if args.warmup is None else args.warmup
        args.duration=60 if args.duration is None else args.duration
    if args.warmup < 2 or args.duration < 20 or args.warmup%2 or args.duration%2:
        parser.error('Use an even warmup >=2 seconds and even duration >=20 seconds.')
    for key in ('output','runtime','java','k6'): setattr(args,key,getattr(args,key).resolve())
    if args.output.exists(): parser.error('Use a fresh output directory; retain all earlier evidence.')
    runtime = distribution_inventory(args.runtime)
    generator = verify_k6(args.k6)
    args.output.mkdir(parents=True)
    inputs=args.output/'inputs'
    for relative in SOURCES:
        target=inputs/relative; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes((ROOT/relative).read_bytes())
    lanes=[{'arm':arm,'rate':rate} for rate,arms in ((.5,('sequential','parallel')),(1,('parallel','sequential'))) for arm in arms]
    if mixed is not None:
        (inputs/'workloads/w02/mixed-load-protocol.json').write_bytes(protocol_bytes)
        save(inputs/'workloads/w02/mixed-corpus.json',mixed['corpus'])
        lanes=mixed['lanes']
    save(args.output/'manifest.json',{'scope':SCOPE,'lanes':lanes,'warmup_s':args.warmup,'duration_s':args.duration,
        'runtime':runtime,'k6':generator,'java_executable_sha256':sha(args.java),
        'java_version':subprocess.run([str(args.java),'-version'],capture_output=True,text=True).stderr.strip(),
        'source_sha256':{relative:sha(inputs/relative) for relative in SOURCES},
        'case_mode':'mixed_sensitivity' if mixed is not None else 'approved',
        'study_id':'w02_m1' if mixed is not None else 'w02_load_qualification',
        'protocol_sha256':sha(inputs/'workloads/w02/mixed-load-protocol.json') if mixed is not None else None,
        'corpus_sha256':sha(inputs/'workloads/w02/mixed-corpus.json') if mixed is not None else None,
        'deadline_miss_action':'report' if mixed is not None else 'fail_qualification',
        'design':'Fixed local qualification order, fresh H2 and backend per lane; not randomized matched performance pairs.',
        'cloud_resources_accessed':False,'native_model_calls':0})
    results=[]; status='failed'
    try:
        config={'workload':{'rate':1},'execution':{'k6_binary':str(args.k6)},
                'evidence':{'calibrate_on_nonlinux':True,'estimated_http_calls_per_workflow':25,'calibration_duration_s':10}}
        print(json.dumps({'stage':'loopback_calibration','scope':SCOPE}),flush=True)
        calibration=calibrate_generator(config,args.output.name,args.output)
        require(calibration['valid'],'loopback_calibration_failed')
        for index,lane in enumerate(lanes,1):
            folder=args.output/f'{index:02d}-{lane["arm"]}-{lane["rate"]}'; folder.mkdir()
            print(json.dumps({'stage':'starting_lane',**lane}),flush=True)
            result=execute_lane(folder,args,inputs,lane,calibration)
            results.append({'folder':folder.name,**result})
            save(args.output/'progress.json',{'scope':SCOPE,'lanes':results})
            print(json.dumps({'stage':'finished_lane',**lane,'status':result['status'],'correct':result['cohorts']['measurement']['correct'],'issues':result['issues']}),flush=True)
            require(result['status']=='passed','qualification_lane_failed')
        require(all(sha(ROOT/p)==sha(inputs/p) for p in SOURCES),'source_changed_during_qualification')
        status='passed'
    finally:
        save(args.output/'summary.json',{'scope':SCOPE,'status':status,'lanes':results,
             'performance_claim':None,'cloud_resources_accessed':False,'native_model_calls':0})
        files=sorted(p for p in args.output.rglob('*') if p.is_file() and p!=args.output/'checksums.json')
        save(args.output/'checksums.json',{'algorithm':'sha256','files':{p.relative_to(args.output).as_posix():sha(p) for p in files}})
    return 0


if __name__=='__main__': raise SystemExit(main())
