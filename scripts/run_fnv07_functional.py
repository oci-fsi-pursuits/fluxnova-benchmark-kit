#!/usr/bin/env python3
"""Run the FNV-07 retry/effect corpus on one owned local H2 engine.

H2 is used only for this single-engine functional gate. PostgreSQL clustering,
job distribution, load, resource headroom and performance remain separate gates.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchlib.fluxnova import FluxnovaClient
from benchlib.fnv06_functional import Recorder, save
from benchlib.fnv07_functional import collect, snapshot, ProbeClient
from scripts.run_w02_functional import distribution_inventory
from services.cluster_probe import ProbeStore, make_server

SOURCES = ('benchlib/__init__.py', 'benchlib/fluxnova.py', 'benchlib/fnv06_functional.py', 'benchlib/fnv07_functional.py',
           'benchlib/workflow_corpus.py', 'services/worker.py', 'services/http_support.py',
           'services/cluster_probe.py', 'services/transport_diagnostics.py', 'services/workflow_checks.py', 'services/fnv07/RecordingDelegate.java',
           'scripts/build_fnv07_delegate.py', 'scripts/run_w02_functional.py', 'scripts/run_fnv07_functional.py',
           'workloads/fnv07/cases.json', 'workloads/fnv07/cluster-jobs.bpmn')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    runtime, java, build, output = (getattr(args, name).resolve() for name in ('runtime', 'java', 'build', 'output'))
    if output.exists() or not java.is_file():
        raise ValueError('Use a fresh output folder and an explicit verified Java executable')
    inventory = distribution_inventory(runtime)
    receipt = json.loads((build / 'build.json').read_text())
    if receipt['artifact_sha256'] != sha(build / 'fnv07-delegate.jar') or receipt['source_sha256'] != sha(ROOT / 'services/fnv07/RecordingDelegate.java'):
        raise ValueError('Delegate build does not match the reviewed source')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', args.port))
    output.mkdir(parents=True)
    (output / 'empty-deployments').mkdir()
    (output / 'userlib').mkdir()
    (output / 'userlib/fnv07-delegate.jar').write_bytes((build / 'fnv07-delegate.jar').read_bytes())
    save(output / 'delegate-build.json', receipt)
    timing = getattr(args, 'timing_qualification', False)
    sources = SOURCES + (('benchlib/fnv07_load.py', 'workloads/fnv07/load-qualification.json') if timing else ())
    collector = collect
    if timing:
        from benchlib.fnv07_load import collect as collector
    for name in sources:
        target = output / 'inputs' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    corpus = json.loads((output / ('inputs/workloads/fnv07/load-qualification.json' if timing else 'inputs/workloads/fnv07/cases.json')).read_text())
    run_id = uuid.uuid4().hex
    user, password = 'fnv07local', secrets.token_urlsafe(24)
    admin_token, execution_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    store = ProbeStore(output / 'probe.sqlite', run_id, ['n1'])
    server = make_server(store, admin_token=admin_token, execute_token=execution_token,
                         diagnostics_path=output / 'transport.ndjson' if args.transport_diagnostics else None,
                         connection_close=not args.legacy_probe_connection)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    config = output / 'engine.local.yml'
    config.write_text('''server:
  address: 127.0.0.1
  port: ''' + str(args.port) + '''
fluxnova.bpm:
  admin-user:
    id: ''' + user + '''
    password: ''' + json.dumps(password) + '''
  history-level: full
  authorization:
    enabled: true
  job-execution:
    enabled: true
    deployment-aware: false
    core-pool-size: 2
    max-pool-size: 2
    queue-capacity: 2
    max-jobs-per-acquisition: 2
    wait-time-in-millis: 100
    max-wait: 500
    backoff-time-in-millis: 100
    max-backoff: 500
    lock-time-in-millis: 30000
  run:
    auth:
      enabled: true
    cors:
      enabled: false
    example:
      enabled: false
spring.datasource:
  url: ''' + json.dumps('jdbc:h2:' + (output / 'engine-db').as_posix() + ';DB_CLOSE_DELAY=-1;DB_CLOSE_ON_EXIT=FALSE') + '''
  driver-class-name: org.h2.Driver
  username: sa
  password: sa
spring.web.resources:
  static-locations: NULL
''', encoding='utf-8')
    manifest = {
        'study_id': corpus['study_id'], 'run_id': run_id, 'runtime': inventory,
        'java_version': subprocess.run([str(java), '-version'], capture_output=True, text=True, check=True).stderr.strip(),
        'java_executable_sha256': sha(java), 'delegate_sha256': receipt['artifact_sha256'],
        'source_sha256': {name: sha(output / 'inputs' / name) for name in sources},
        'database': 'fresh embedded H2', 'nodes': ['n1'], 'http_timeout_s': 10,
        'intended_cases': 6, 'expected_completed_instances': 3, 'expected_blocked_instances': 2,
        'expected_waiting_controls': 1, 'expected_external_effects': 12,
        'cloud_resources_accessed': False, 'performance_comparison_complete': False,
        'engine_sql_reconciled': False,
        'transport_diagnostics_enabled': args.transport_diagnostics,
        'probe_connection_close_experiment': args.probe_connection_close,
        'probe_connection_close': not args.legacy_probe_connection,
        'scope': 'Single-engine local functional qualification; no PostgreSQL, cluster, capacity or exactly-once network guarantee.',
    }
    if timing:
        manifest.update(intended_cases=corpus['rate_per_s'] * corpus['arrival_seconds'], qualification_only=True,
                        expected_completed_instances=corpus['rate_per_s'] * corpus['arrival_seconds'],
                        expected_blocked_instances=0, expected_waiting_controls=0,
                        expected_external_effects=3 * corpus['rate_per_s'] * corpus['arrival_seconds'],
                        scope='One local H2 engine, fixed-arrival timing qualification. No resource, cluster, capacity or performance-comparison claim.')
    save(output / 'manifest.json', manifest)
    command = [str(java), '-Xms256m', '-Xmx1024m', '-Duser.timezone=UTC', '-Djdk.httpclient.disableRetryConnect=true',
        '-Dloader.path=' + ','.join(str(p) for p in [runtime / 'internal/rest', runtime / 'internal/webapps', runtime / 'configuration/userlib', output / 'userlib']),
        '-Dcamunda.deploymentDir=' + str(output / 'empty-deployments'),
        '-jar', str(runtime / 'internal/fluxnova-bpm-run-core.jar'), '--spring.config.location=' + config.as_uri()]
    env = dict(os.environ, FNV07_NODE_ID='n1', FNV07_PROBE_URL=f'http://127.0.0.1:{server.server_port}', FNV07_EXECUTE_TOKEN=execution_token)
    engine = FluxnovaClient(f'http://127.0.0.1:{args.port}/engine-rest', user, password)
    api = Recorder(engine, output)
    summary = {'study_id': corpus['study_id'], 'run_id': run_id, 'status': 'started', 'instances': []}
    process, ready, failure = None, False, None
    with (output / 'engine-n1.log').open('wb') as log:
        try:
            process = subprocess.Popen(command, cwd=output, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            deadline = time.monotonic() + 180
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Owned engine exited during startup')
                try:
                    version = engine.readiness()
                    break
                except (OSError, RuntimeError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Owned engine readiness timeout')
                    time.sleep(.5)
            if version['version'] != '3.0.1':
                raise RuntimeError('Wrong pinned engine release')
            save(output / 'engine-version.json', version)
            ready = True
            probe = ProbeClient(f'http://127.0.0.1:{server.server_port}', admin_token) if timing else store
            collector(api, probe, corpus, output / 'inputs/workloads/fnv07/cluster-jobs.bpmn', output, run_id, summary)
        except BaseException as error:
            summary.update(status='failed', error_type=type(error).__name__, reason=str(error)[:300])
            if ready:
                try:
                    save(output / 'failure-snapshot.json', snapshot(api))
                except Exception as capture_error:
                    summary['failure_capture_error'] = type(capture_error).__name__
            save(output / 'failure-probe.json', store.snapshot())
            failure = error
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
            summary.update(owned_engine_stopped=process is None or process.poll() is not None,
                           owned_probe_stopped=not worker.is_alive(), cloud_resources_accessed=False)
            save(output / 'collection.json', summary)
            with sqlite3.connect(store.path) as original, sqlite3.connect(output / 'probe-snapshot.sqlite') as backup:
                original.backup(backup)
    sealed = [p for p in output.rglob('*') if p.is_file() and (p.name in {
        'manifest.json', 'delegate-build.json', 'collection.json', 'requests.ndjson', 'engine-n1.log',
        'engine-version.json', 'initial-snapshot.json', 'initial-probe.json', 'final-snapshot.json',
        'final-probe.json', 'failure-snapshot.json', 'failure-probe.json', 'probe-snapshot.sqlite', 'fnv07-delegate.jar',
        'load-plan.json', 'load-collection.json', 'load-requests.ndjson', 'transport.ndjson'} or 'inputs' in p.relative_to(output).parts)]
    save(output / 'checksums.json', {'files': {p.relative_to(output).as_posix(): sha(p) for p in sealed}})
    if failure is not None:
        raise failure
    print(json.dumps({k: summary[k] for k in ('status', 'owned_engine_stopped', 'owned_probe_stopped', 'cloud_resources_accessed')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('runtime', 'java', 'build', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--port', type=int, default=18978)
    parser.add_argument('--timing-qualification', action='store_true',
                        help='Run the bounded normal-case open-loop timing qualification instead of the six fault cases.')
    parser.add_argument('--transport-diagnostics', action='store_true',
                        help='Record metadata-only receipt-server trace; adds diagnostic overhead, not a performance comparison.')
    connection = parser.add_mutually_exclusive_group()
    connection.add_argument('--probe-connection-close', action='store_true',
                            help='Explicit connection-close policy (now the default); accepted for compatibility.')
    connection.add_argument('--legacy-probe-connection', action='store_true',
                            help='Diagnostic baseline only: reproduce the old response without an explicit close header.')
    run(parser.parse_args())
