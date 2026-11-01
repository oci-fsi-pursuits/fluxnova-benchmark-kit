#!/usr/bin/env python3
"""Run both PAY-05 BPMNs in an owned local Fluxnova/H2 cell and seal evidence."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchlib.fluxnova import FluxnovaClient
from benchlib.pay05_functional import exercise
from scripts.run_w02_functional import distribution_inventory, sha, save, require

SCOPE = 'LOCAL FUNCTIONAL VALIDATION - NOT OCI PERFORMANCE'


def run(args):
    output, runtime, java = args.output.resolve(), args.runtime.resolve(), args.java.resolve()
    if output.exists(): raise ValueError('Use a fresh output directory; retain failed attempts.')
    if not java.is_file() or not runtime.is_dir(): raise ValueError('Local Java and verified Fluxnova distribution required.')
    inventory = distribution_inventory(runtime)
    with socket.socket() as probe: probe.bind(('127.0.0.1', args.port))
    output.mkdir(parents=True); (output / 'empty-deployments').mkdir()
    sources = ['benchlib/pay05_functional.py', 'benchlib/fx_quotes.py', 'benchlib/fluxnova.py', 'benchlib/workflow_corpus.py',
               'services/fx_quotes.py', 'services/workflow_checks.py', 'services/workflow_checks_worker.py',
               'services/worker.py', 'services/http_support.py', 'scripts/run_pay05_functional.py',
               'scripts/audit_pay05_functional.py', 'scripts/run_w02_functional.py',
               'workloads/pay05/cases.json', 'workloads/pay05/sequential.bpmn', 'workloads/pay05/parallel.bpmn']
    for name in sources:
        p = output / 'inputs' / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes((ROOT / name).read_bytes())
    corpus = json.loads((output / 'inputs/workloads/pay05/cases.json').read_text())
    run_id = uuid.uuid4().hex
    order = {c['case_id']: (['sequential', 'parallel'] if i % 2 == 0 else ['parallel', 'sequential'])
             for i, c in enumerate(corpus['cases'])}
    manifest = {'study_id': 'PAY-05', 'scope': SCOPE, 'run_id': run_id, 'runtime': inventory,
                'java': subprocess.run([str(java), '-version'], capture_output=True, text=True, check=True).stderr.strip(),
                'java_executable_sha256': sha(java), 'host_os': sys.platform, 'database': 'embedded H2',
                'worker_slots': 3, 'worker_idle_s': .1, 'completion_poll_ms': 100, 'arm_order_by_case': order,
                'worker_clients_preinitialized_before_fetch': True,
                'source_sha256': {n: sha(output / 'inputs' / n) for n in sources},
                'design': 'Fixed functional cases; no warmup/load/performance comparison. Quote validity uses the trusted registration clock.',
                'authority_scope': 'Immutable registrar-bound input; not signed-intent or external FX-provider integration.',
                'native_model_calls': 0, 'cloud_resources_accessed': False}
    save(output / 'manifest.json', manifest)
    username, password = 'pay05local', secrets.token_urlsafe(24)
    config = output / 'engine.local.yml'
    config.write_text('''server:
  address: 127.0.0.1
  port: ''' + str(args.port) + '''
fluxnova.bpm:
  admin-user:
    id: ''' + username + '''
    password: ''' + json.dumps(password) + '''
  history-level: full
  authorization:
    enabled: true
  filter:
    create: All tasks
  run:
    auth:
      enabled: true
    cors:
      enabled: false
    example:
      enabled: false
spring.datasource:
  url: ''' + json.dumps('jdbc:h2:' + str(output / 'engine-db').replace('\\', '/') + ';DB_CLOSE_DELAY=-1;DB_CLOSE_ON_EXIT=FALSE') + '''
  driver-class-name: org.h2.Driver
  username: sa
  password: sa
spring.web.resources:
  static-locations: NULL
''', encoding='utf-8')
    command = [str(java), '-Xms256m', '-Xmx1024m',
               '-Dloader.path=' + ','.join(str(runtime / p) for p in ('internal/rest', 'internal/webapps', 'configuration/userlib')),
               '-Dcamunda.deploymentDir=' + str(output / 'empty-deployments'),
               '-jar', str(runtime / 'internal/fluxnova-bpm-run-core.jar'), '--spring.config.location=' + config.as_uri()]
    summary = {'study_id': 'PAY-05', 'scope': SCOPE, 'status': 'running', 'run_id': run_id, 'cases': [],
               'replay_probes': [], 'performance_comparison_complete': False, 'model_calls': 0, 'cloud_resources_accessed': False}
    process = server = pool = None
    log = (output / 'engine.log').open('wb')
    url = 'http://127.0.0.1:' + str(args.port) + '/engine-rest'
    engine = FluxnovaClient(url, username, password)
    authorization = 'Basic ' + base64.b64encode((username + ':' + password).encode()).decode()
    try:
        save(output / 'progress.json', summary)
        process = subprocess.Popen(command, cwd=output, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 180
        while True:
            require(process.poll() is None, 'engine_exited_during_startup')
            try: version = engine.readiness(); break
            except (OSError, RuntimeError):
                require(time.monotonic() < deadline, 'engine_start_timeout'); time.sleep(.5)
        save(output / 'engine-version.json', version)
        save(output / 'deployment.json', engine.deploy([output / 'inputs/workloads/pay05' / (a + '.bpmn') for a in ('sequential', 'parallel')]))
        exercise(engine, output, corpus, run_id, order, summary, url, authorization)
    except BaseException as error:
        summary.update(status='failed', error_type=type(error).__name__); save(output / 'failure.json', summary)
        raise
    finally:
        cleanup_errors = []
        def cleanup(name, action):
            try: action()
            except Exception as error: cleanup_errors.append({'component': name, 'error_type': type(error).__name__})
        if pool: cleanup('workers', pool.stop)
        if server: cleanup('support_shutdown', server.shutdown); cleanup('support_close', server.server_close)
        if process and process.poll() is None:
            def stop_engine():
                process.terminate()
                try: process.wait(timeout=20)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
            cleanup('engine', stop_engine)
        cleanup('log', log.close)
        cleanup('ephemeral_config', lambda: config.write_text(config.read_text().replace(json.dumps(password), '"REDACTED_AFTER_SHUTDOWN"'), encoding='utf-8'))
        summary.update(owned_engine_stopped=bool(process and process.poll() is not None), cleanup_errors=cleanup_errors)
        if cleanup_errors: summary['status'] = 'failed'
        save(output / 'summary.json', summary)
        save(output / 'checksums.json', {'algorithm': 'sha256', 'files': {p.relative_to(output).as_posix(): sha(p)
             for p in sorted(output.rglob('*')) if p.is_file() and p.name != 'checksums.json'}})
    require(not cleanup_errors, 'cleanup_incomplete')
    print(json.dumps({'status': summary['status'], 'functional_executions': len(summary['cases']), 'scope': SCOPE}), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--runtime', type=Path, default=ROOT / '.tools/fluxnova-3.0.1')
    p.add_argument('--java', type=Path, default=ROOT / '.tools/java21/jdk-21.0.12.1+1-jre/bin/java.exe')
    p.add_argument('--port', type=int, default=29180)
    run(p.parse_args())


if __name__ == '__main__': main()
