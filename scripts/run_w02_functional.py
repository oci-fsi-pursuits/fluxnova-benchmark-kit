#!/usr/bin/env python3
"""Run W02 against an owned local Fluxnova/H2 process, then stop owned services.

This qualifies workflow behavior, not OCI performance. Uses a fresh output folder,
two worker slots in both arms, a 100 ms completion observer and no model calls.
The original reference cell and its credentials are never accessed.
"""
import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchlib.fluxnova import FluxnovaClient
from benchlib.workflow_corpus import resolve_sensitivity
from services.worker import Client
from services.workflow_checks import StudyStore, fingerprint, make_server
from services.workflow_checks_worker import WorkerPool

LABEL = 'LOCAL FUNCTIONAL VALIDATION - NOT OCI PERFORMANCE'
DEFINITIONS = {'sequential':'bench_w02_sequential', 'parallel':'bench_w02_parallel'}


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require(condition, reason):
    if not condition:
        raise AssertionError(reason)


def require_outcome(case, state, observed, activities):
    """Independent assertions against a prespecified corpus, not engine success."""
    require(observed is not None, 'no_durable_completion')
    expected = case['expected_outcome']
    values = observed['variables']
    require(values.get('w02Outcome') == expected, 'wrong_workflow_outcome')
    require(values.get('w02Kyc') == case['expected_checks']['kyc'], 'wrong_kyc_variable')
    require(values.get('w02Risk') == case['expected_checks']['risk'], 'wrong_risk_variable')
    require(state['input_sha256'] == fingerprint(case), 'input_mutation')
    require(set(state['receipts']) == {'kyc','risk','settle'}, 'incomplete_receipts')
    for operation, receipt in state['receipts'].items():
        require(receipt['input_sha256'] == fingerprint(case), 'receipt_input_mismatch')
        require(receipt['process_id'] == observed['instance']['id'], 'receipt_process_mismatch')
        require(receipt['outcome'] == (expected if operation == 'settle' else case['expected_checks'][operation]), 'receipt_outcome_mismatch')
    p = case['payment']
    wanted_effects = ([{name:p[name] for name in ('payment_id','amount_minor','beneficiary')}]
                      if expected == 'settled' else [])
    require(state['effects'] == wanted_effects, 'wrong_or_duplicate_payment_effect')
    ends = [a['activityId'] for a in activities if a['activityType'].lower().endswith('endevent') and a.get('endTime')]
    require(ends == [expected], 'wrong_or_multiple_end_events')
    require(state['receipts']['settle']['started_ns'] >= max(state['receipts'][op]['finished_ns'] for op in ('kyc','risk')), 'settlement_before_both_receipts')


def distribution_inventory(runtime):
    archive = ROOT / '.tools/fluxnova-3.0.1.zip'
    expected = 'ced4311c541faf1a7248d88e37ba53bd8c659fefe8387d2f78c21c30a117d7cf'
    require(archive.is_file() and sha(archive) == expected, 'distribution_archive_not_verified')
    jars = [runtime/'internal/fluxnova-bpm-run-core.jar']
    for folder in ('internal/rest','internal/webapps','configuration/userlib'):
        jars += sorted((runtime/folder).glob('*.jar'))
    rows = []
    with zipfile.ZipFile(archive) as zipped:
        for jar in jars:
            relative = jar.relative_to(runtime).as_posix()
            matches = [name for name in zipped.namelist() if name == relative or name.endswith('/'+relative)]
            require(len(matches) == 1, 'runtime_member_not_found')
            digest = sha(jar)
            with zipped.open(matches[0]) as original:
                require(hashlib.file_digest(original,'sha256').hexdigest() == digest, 'runtime_modified_since_distribution')
            rows.append({'path':relative,'sha256':digest})
    return {'release':'3.0.1','distribution_sha256':expected,'loaded_jars':rows}


def run(args):
    output = args.output.resolve()
    if output.exists():
        raise ValueError('Use a new output directory; retain failed attempts.')
    base_corpus = json.loads((ROOT/'workloads/w02/cases.json').read_text(encoding='utf-8'))
    protocol_path = getattr(args, 'sensitivity_protocol', None)
    protocol_bytes = protocol_path.read_bytes() if protocol_path else None
    corpus = resolve_sensitivity(json.loads(protocol_bytes), base_corpus) if protocol_path is not None else base_corpus
    runtime, java = args.runtime.resolve(), args.java.resolve()
    if not java.is_file() or not runtime.is_dir():
        raise ValueError('Pinned local Java and Fluxnova distribution are required.')
    inventory = distribution_inventory(runtime)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    output.mkdir(parents=True)
    (output/'empty-deployments').mkdir()
    tracked = ['services/workflow_checks.py','services/workflow_checks_worker.py',
               'services/http_support.py','services/worker.py','benchlib/fluxnova.py',
               'scripts/run_w02_functional.py','benchlib/workflow_checks_report.py','benchlib/workflow_corpus.py',
               'workloads/w02/cases.json','workloads/w02/sequential.bpmn','workloads/w02/parallel.bpmn']
    for relative in tracked:
        snapshot = output/'inputs'/relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes((ROOT/relative).read_bytes())
    if protocol_bytes:
        protocol_relative = 'inputs/workloads/w02/sensitivity-protocol.json'
        (output/protocol_relative).write_bytes(protocol_bytes)
        case_file = 'inputs/workloads/w02/resolved-cases.json'
        save(output/case_file, corpus)
    else:
        case_file = 'inputs/workloads/w02/cases.json'
    cases = corpus['cases']
    arm_order_by_case = corpus.get('arm_order_by_case', {c['case_id']:list(DEFINITIONS) for c in cases})
    run_id = uuid.uuid4().hex
    password = secrets.token_urlsafe(24)
    username = 'w02local'
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
  url: '''+json.dumps('jdbc:h2:'+str(output/'engine-db').replace('\\','/')+';DB_CLOSE_ON_EXIT=FALSE')+'''
  driver-class-name: org.h2.Driver
  username: sa
  password: sa
spring.web.resources:
  static-locations: NULL
''', encoding='utf-8')
    manifest = {'scope':LABEL,'run_id':run_id,'runtime':inventory,
                'java':subprocess.run([str(java),'-version'],capture_output=True,text=True).stderr.strip(),
                'java_executable_sha256':sha(java),'host_os':sys.platform,'database':'embedded H2',
                'worker_slots':2,'worker_idle_s':.1,'completion_poll_ms':100,
                'design':'Functional cases; workers start after each registration. No steady-state load, warmup or performance comparison.',
                'case_order':[c['case_id'] for c in cases], 'arm_order':['sequential','parallel'],
                'source_sha256':{p:sha(output/'inputs'/p) for p in tracked},
                'native_model_calls':0,'cloud_resources_accessed':False}
    manifest.update(study_id=corpus.get('study_id','w02'), case_file=case_file,
                    case_file_sha256=sha(output/case_file), arm_order_by_case=arm_order_by_case)
    if protocol_bytes:
        manifest.update(title=corpus['title'], corpus_scope=corpus['scope'],
                        profiles=corpus['profiles'], arm_order='predeclared_per_case',
                        protocol_file=protocol_relative, protocol_sha256=sha(output/protocol_relative))
    save(output/'manifest.json', manifest)
    command = [str(java),'-Xms256m','-Xmx1024m',
               '-Dloader.path='+','.join(str(runtime/p) for p in ('internal/rest','internal/webapps','configuration/userlib')),
               '-Dcamunda.deploymentDir='+str(output/'empty-deployments'),
               '-jar',str(runtime/'internal/fluxnova-bpm-run-core.jar'),
               '--spring.config.location='+config.as_uri()]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    log = (output/'engine.log').open('wb')
    process = None
    server = None
    pool = None
    summary = {'scope':LABEL,'run_id':run_id,'status':'running','cases':[],'replay_probes':[],
               'performance_claim':None,'cloud_resources_accessed':False,'native_model_calls':0}
    save(output/'progress.json',summary)
    engine_url = f'http://127.0.0.1:{args.port}/engine-rest'
    engine = FluxnovaClient(engine_url,username,password)
    authorization = 'Basic '+base64.b64encode((username+':'+password).encode()).decode()
    try:
        print(json.dumps({'stage':'starting_owned_local_engine','scope':LABEL}),flush=True)
        process = subprocess.Popen(command,cwd=output,stdout=log,stderr=subprocess.STDOUT,creationflags=creationflags)
        startup_deadline = time.monotonic()+180
        while True:
            require(process.poll() is None,'engine_exited_during_startup')
            try:
                version = engine.readiness()
                break
            except (OSError,RuntimeError):
                require(time.monotonic()<startup_deadline,'engine_start_timeout')
                time.sleep(.5)
        save(output/'engine-version.json',version)
        deployed = engine.deploy([output/'inputs/workloads/w02'/ (arm+'.bpmn') for arm in DEFINITIONS])
        save(output/'deployment.json',deployed)
        store = StudyStore(output/'study.sqlite')
        worker_token, registrar_token = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        server = make_server('127.0.0.1',0,store,worker_token,registrar_token)
        server_thread = threading.Thread(target=server.serve_forever,daemon=True)
        server_thread.start()
        support_url = 'http://127.0.0.1:'+str(server.server_port)
        registrar = Client(support_url,'Bearer '+registrar_token)
        support = Client(support_url,'Bearer '+worker_token)

        def workers():
            return WorkerPool(lambda:Client(engine_url,authorization),
                              lambda:Client(support_url,'Bearer '+worker_token),slots=2,idle_s=.1).start()

        approved = {}
        for case in cases:
            for arm in arm_order_by_case[case['case_id']]:
                definition = DEFINITIONS[arm]
                key = 'w02-'+run_id[:12]+'-'+arm+'-'+case['case_id']
                started = engine.start(definition,key,{'studyCaseKey':{'value':key,'type':'String'}})
                pid = started['id']
                registrar.request('POST','/cases/register',{'case_key':key,'process_id':pid,'case':case})
                # Before any worker starts, an effect must be refused.
                try:
                    support.request('POST','/tools/execute',{'case_key':key,'process_id':pid,'operation':'settle'})
                    raise AssertionError('premature_settlement_was_accepted')
                except urllib.error.HTTPError as error:
                    require(error.code==409,'wrong_premature_settlement_response')
                pool = workers()
                observed = engine.observe(pid,timeout_s=20,poll_s=.1)
                pool.stop()
                records = pool.records
                pool = None
                state = registrar.request('GET','/state/'+urllib.parse.quote(key,safe=''))
                activities = engine.request('GET','/history/activity-instance?processInstanceId='+urllib.parse.quote(pid,safe=''))
                save(output/(arm+'-'+case['case_id']+'.raw.json'),{
                    'case':case,'history':observed,'activities':activities,
                    'backend':state,'worker_records':records})
                require_outcome(case,state,observed,activities)
                kyc,risk = state['receipts']['kyc'],state['receipts']['risk']
                overlap_ns = min(kyc['finished_ns'],risk['finished_ns'])-max(kyc['started_ns'],risk['started_ns'])
                if arm=='sequential':
                    require(risk['started_ns']>=kyc['finished_ns'],'sequential_checks_overlapped')
                if arm=='parallel' and case.get('require_parallel_overlap', case['case_id'] in ('approve','concurrent_completion')):
                    require(overlap_ns>0,'parallel_checks_did_not_overlap')
                for operation, schedule in case['schedule'].items():
                    event = {'before_once':'failure_before_receipt','after_once':'response_lost_after_receipt'}.get(schedule['fault'])
                    if event:
                        require(any(a['operation']==operation and a['event']==event for a in state['audit']),'fault_not_exercised')
                # Repeating all three operations preserves the original receipts/effects.
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                    repeated = list(executor.map(lambda op:support.request('POST','/tools/execute',
                        {'case_key':key,'process_id':pid,'operation':op}),['kyc','risk','settle']*2))
                require(all(r['replayed'] for r in repeated),'replay_reexecuted_operation')
                after = registrar.request('GET','/state/'+urllib.parse.quote(key,safe=''))
                require(after['effects']==state['effects'] and after['receipts']==state['receipts'],'replay_changed_original_result')
                row = {'case_id':case['case_id'],'arm':arm,'process_id':pid,'case_key':key,'status':'passed',
                       'outcome':case['expected_outcome'],'effects':len(state['effects']),
                       'input_sha256':fingerprint(case),'checks_overlap':overlap_ns>0,
                       'replay_calls':len(repeated),'premature_effect_refused':True}
                save(output/(arm+'-'+case['case_id']+'.json'),{'case':case,'result':row,
                     'history':observed,'activities':activities,'backend_before_replay':state,
                     'backend_after_replay':after,'worker_records':records})
                summary['cases'].append(row)
                save(output/'progress.json',summary)
                print(json.dumps({'stage':'functional_case','arm':arm,'case':case['case_id'],'status':'passed'}),flush=True)
                if case['expected_outcome']=='settled' and arm not in approved:
                    approved[arm] = (key,state)

        for arm,(key,original) in approved.items():
            replay = engine.start(DEFINITIONS[arm],key,{'studyCaseKey':{'value':key,'type':'String'}})
            pid = replay['id']
            pool = workers()
            deadline = time.monotonic()+10
            while True:
                tasks = engine.request('GET','/external-task?processInstanceId='+pid)
                if tasks and all(t.get('retries')==0 for t in tasks):
                    break
                require(time.monotonic()<deadline,'cross_process_replay_not_blocked')
                time.sleep(.1)
            pool.stop()
            pool = None
            after = registrar.request('GET','/state/'+urllib.parse.quote(key,safe=''))
            require(after['effects']==original['effects'] and after['receipts']==original['receipts'],'cross_process_replay_changed_effect')
            incidents = engine.request('GET','/incident?processInstanceId='+pid)
            require(len(incidents)==len(tasks),'missing_replay_incident')
            engine.request('DELETE','/process-instance/'+pid+'?skipCustomListeners=true&skipIoMappings=true')
            probe = {'arm':arm,'process_id':pid,'status':'blocked_as_expected',
                     'incidents_before_cleanup':len(incidents),'original_effects_unchanged':True,'cleanup':'deleted_owned_replay_instance'}
            summary['replay_probes'].append(probe)
            save(output/'progress.json',summary)

        final_counts = {name:engine.request('GET',path)['count'] for name,path in (
            ('active_instances','/process-instance/count'),('external_tasks','/external-task/count'),('incidents','/incident/count'))}
        save(output/'final-engine-counts.json',final_counts)
        require(all(n==0 for n in final_counts.values()),'owned_engine_not_drained')
        summary.update(status='passed',functional_cases_passed=len(summary['cases']),
                       expected_effects=sum(r['effects'] for r in summary['cases']),
                       active_instances=0,external_tasks=0,incidents=0)
    except Exception as error:
        summary.update(status='failed',error_type=type(error).__name__)
        save(output/'failure.json',summary)
        raise
    finally:
        cleanup_errors = []

        def cleanup(name, action):
            try:
                action()
            except Exception as error:
                cleanup_errors.append({'component':name,'error_type':type(error).__name__})

        if pool:
            cleanup('workers', pool.stop)
        if server:
            cleanup('support_shutdown', server.shutdown)
            cleanup('support_close', server.server_close)
        if process and process.poll() is None:
            def stop_engine():
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            cleanup('engine', stop_engine)
        cleanup('engine_log', log.close)
        summary['owned_engine_stopped'] = bool(process and process.poll() is not None)
        # Local ephemeral credentials are not needed to reproduce saved evidence.
        # Replace just the generated configuration's password; never touch others.
        def redact_config():
            text = config.read_text(encoding='utf-8').replace(json.dumps(password),'"REDACTED_AFTER_SHUTDOWN"')
            config.write_text(text,encoding='utf-8')
        cleanup('ephemeral_config', redact_config)
        summary['cleanup_errors'] = cleanup_errors
        if cleanup_errors:
            summary['status'] = 'failed'
            save(output/'failure.json',summary)
        save(output/'summary.json',summary)
    require(not cleanup_errors, 'owned_service_cleanup_incomplete')
    from benchlib.workflow_checks_report import write_report
    write_report(output)
    files = sorted(p for p in output.rglob('*') if p.is_file() and p != output/'checksums.json')
    save(output/'checksums.json',{'algorithm':'sha256','files':{p.relative_to(output).as_posix():sha(p) for p in files}})
    print(json.dumps({'status':summary['status'],'functional_cases_passed':summary['functional_cases_passed'],
                      'report':str(output/'report.html'),'scope':LABEL}),flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--runtime',type=Path,default=ROOT/'.tools/fluxnova-3.0.1')
    parser.add_argument('--java',type=Path,default=ROOT/'.tools/java21/jdk-21.0.12.1+1-jre/bin/java.exe')
    parser.add_argument('--port',type=int,default=28180)
    parser.add_argument('--sensitivity-protocol',type=Path,
                        help='Optional prespecified dependency grid; remains local functional qualification.')
    args = parser.parse_args()
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
