#!/usr/bin/env python3
"""Run W03's synthetic review/task-count comparison in a fresh local engine.

No OCI credentials or live models. Reviews use an independently authenticated
engine principal. Scripted pauses are not human handling-time measurements.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from benchlib.fluxnova import FluxnovaClient
from benchlib.review_routing_report import audit_record
from services.worker import Client
from services.payment_approval import typed
from services.review_routing import DEFINITIONS,TOPIC,RoutingStore,snapshot,fingerprint
from scripts.run_w02_functional import distribution_inventory,sha,save,require
from scripts.run_approval_role_checks import READS


def run(args):
    output=args.output.resolve();runtime=args.runtime.resolve();java=args.java.resolve()
    if output.exists():raise ValueError('Use a fresh output directory; retain failed attempts.')
    inventory=distribution_inventory(runtime)
    with socket.socket() as probe:probe.bind(('127.0.0.1',args.port))
    output.mkdir(parents=True);(output/'empty-deployments').mkdir()
    sources=['services/review_routing.py','benchlib/review_routing_report.py','scripts/run_w03_functional.py',
             'services/worker.py','services/payment_approval.py','benchlib/fluxnova.py',
             'scripts/run_w02_functional.py','scripts/run_approval_role_checks.py',
             'workloads/w03/cases.json','workloads/w03/all_review.bpmn','workloads/w03/policy_routing.bpmn']
    for relative in sources:
        target=output/'inputs'/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/relative).read_bytes())
    corpus=json.loads((output/'inputs/workloads/w03/cases.json').read_text())
    username='w03admin';password=secrets.token_urlsafe(32);run_id=uuid.uuid4().hex
    config=output/'engine.local.yml'
    config.write_text('''server:
  address: 127.0.0.1
  port: PORT
fluxnova.bpm:
  admin-user:
    id: w03admin
    password: PASSWORD
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
  url: DATABASE
  driver-class-name: org.h2.Driver
  username: sa
  password: sa
spring.web.resources:
  static-locations: NULL
'''.replace('PORT',str(args.port)).replace('PASSWORD',json.dumps(password)).replace('DATABASE',json.dumps('jdbc:h2:'+(output/'engine-db').as_posix()+';DB_CLOSE_ON_EXIT=FALSE')),encoding='utf-8')
    command=[str(java),'-Xms256m','-Xmx1024m',
        '-Dloader.path='+','.join(str(runtime/p) for p in ('internal/rest','internal/webapps','configuration/userlib')),
        '-Dcamunda.deploymentDir='+str(output/'empty-deployments'),'-jar',str(runtime/'internal/fluxnova-bpm-run-core.jar'),
        '--spring.config.location='+config.as_uri()]
    manifest={'scope':'Synthetic functional review/task-count study; not human productivity or OCI performance',
        'run_id':run_id,'runtime':inventory,'java':subprocess.run([str(java),'-version'],capture_output=True,text=True).stderr.strip(),
        'java_sha256':sha(java),'host_os':sys.platform,'database':'embedded H2','case_order':[c['case_id'] for c in corpus['cases']],
        'arm_order':'Alternate first arm across the ten fixed cases','scripted_review_pause_s':corpus['scripted_review_pause_s'],
        'source_sha256':{name:sha(output/'inputs'/name) for name in sources},'native_model_calls':0,'cloud_resources_accessed':False}
    save(output/'manifest.json',manifest)
    engine_url=f'http://127.0.0.1:{args.port}/engine-rest'
    engine=FluxnovaClient(engine_url,username,password)
    summary={'scope':manifest['scope'],'status':'running','cases':[],'denials':[],'backend_probes':[],
        'human_time_saved':None,'live_model_calls':0,'cloud_resources_accessed':False}
    process=None;log=(output/'engine.log').open('wb');users=[];grants=[];owned_pids=[]
    def progress():save(output/'progress.json',summary)
    def deny(name,client,method,path,body):
        try:client.request(method,path,body)
        except urllib.error.HTTPError as error:
            status=error.code;error.read();error.close();assert status==403
            summary['denials'].append({'probe':name,'http_status':status});progress()
        else:raise AssertionError('forbidden_request_succeeded:'+name)
    def refused(name,call,key):
        try:call()
        except ValueError as error:
            summary['backend_probes'].append({'probe':name,'reason':str(error),'effects':len(store.state(key)['effects'])})
            assert not store.state(key)['effects'];progress()
        else:raise AssertionError('unsafe_backend_request_succeeded:'+name)
    worker_id='w03-trusted-worker-'+run_id[:12]
    def do_external(task):
        op,var={'route':('route','w03Route'),'verify_settle':('settle','w03Outcome')}[task['activityId']]
        receipt=store.execute(task['variables']['studyCaseKey']['value'],task['processInstanceId'],op)
        engine.request('POST','/external-task/'+task['id']+'/complete',{'workerId':worker_id,'variables':typed({var:receipt['outcome']})})
    def fetch():
        return engine.request('POST','/external-task/fetchAndLock',{'workerId':worker_id,'maxTasks':1,
            'topics':[{'topicName':TOPIC,'lockDuration':60000,'variables':['studyCaseKey']}]})
    try:
        process=subprocess.Popen(command,cwd=output,stdout=log,stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+180
        while True:
            assert process.poll() is None
            try:version=engine.readiness();break
            except (OSError,RuntimeError):
                assert time.monotonic()<deadline;time.sleep(.5)
        assert version['version']=='3.0.1';save(output/'engine-version.json',version)
        save(output/'deployment.json',engine.deploy([output/'inputs/workloads/w03'/(arm+'.bpmn') for arm in DEFINITIONS]))
        roles={}
        for role in ('agent','reviewer'):
            user='w03'+role+run_id[:10];secret=secrets.token_urlsafe(24)
            engine.request('POST','/user/create',{'profile':{'id':user,'firstName':'Synthetic','lastName':role},'credentials':{'password':secret}})
            users.append(user)
            roles[role]=(user,Client(engine_url,'Basic '+base64.b64encode((user+':'+secret).encode()).decode()))
            for definition in DEFINITIONS.values():
                grants.append(engine.request('POST','/authorization/create',{'type':1,'userId':user,'resourceType':6,
                    'resourceId':definition,'permissions':READS+(['CREATE_INSTANCE'] if role=='agent' else ['TASK_WORK'])})['id'])
            if role=='agent':
                grants.append(engine.request('POST','/authorization/create',{'type':1,'userId':user,'resourceType':8,'resourceId':'*','permissions':['CREATE']})['id'])
        agent=roles['agent'][1];reviewer_name,reviewer=roles['reviewer']
        store=RoutingStore(output/'study.sqlite',engine)
        for index,case in enumerate(corpus['cases']):
            arms=list(DEFINITIONS) if index%2==0 else list(reversed(DEFINITIONS))
            for arm in arms:
                key='w03-'+run_id[:12]+'-'+arm+'-'+case['case_id'];begin=time.monotonic()
                started=agent.request('POST','/process-definition/key/'+DEFINITIONS[arm]+'/start',
                    {'businessKey':key,'variables':typed(snapshot(case['payment'],key))})
                pid=started['id'];owned_pids.append(pid)
                store.register(key,pid,arm,case['payment'],reviewer_name)
                refused('settlement_before_policy',lambda:store.execute(key,pid,'settle'),key)
                query='?processInstanceId='+pid;reviewed=False;deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    for task in fetch():do_external(task)
                    tasks=engine.request('GET','/task'+query)
                    if tasks:
                        assert len(tasks)==1 and tasks[0]['taskDefinitionKey']=='payment_review' and not reviewed
                        task=tasks[0];complete={'variables':typed({'reviewerDecision':case['scripted_reviewer_decision'],
                            'reviewedPaymentSha256':fingerprint(case['payment'])})}
                        refused('settlement_before_completed_review',lambda:store.execute(key,pid,'settle'),key)
                        if case['case_id']=='eligible_small':
                            deny('agent-self-approval',agent,'POST','/task/'+task['id']+'/complete',complete)
                            deny('agent-change-original',agent,'PUT','/process-instance/'+pid+'/variables/payment_amount_minor',{'value':1,'type':'Long'})
                            deny('agent-skip-review',agent,'POST','/process-instance/'+pid+'/modification',{'instructions':[{'type':'cancel','activityId':'payment_review'},{'type':'startBeforeActivity','activityId':'verify_settle'}]})
                            deny('reviewer-start-payment',reviewer,'POST','/process-definition/key/'+DEFINITIONS[arm]+'/start',{'businessKey':'denied-'+key})
                        time.sleep(corpus['scripted_review_pause_s'])
                        reviewer.request('POST','/task/'+task['id']+'/complete',complete);reviewed=True
                    h=engine.request('GET','/history/process-instance/'+pid)
                    if h.get('endTime'):break
                    time.sleep(.025)
                else:raise TimeoutError('case_did_not_complete')
                observed=engine.observe(pid,timeout_s=3,poll_s=.1)
                record={'case':case,'arm':arm,'process_id':pid,'key':key,'reviewer':reviewer_name,
                    'history':observed,'backend':store.state(key),'tasks':engine.request('GET','/history/task'+query),
                    'operations':engine.request('GET','/history/user-operation'+query),
                    'activities':engine.request('GET','/history/activity-instance'+query),
                    'observed_case_duration_s':time.monotonic()-begin}
                checked=audit_record(record);save(output/(arm+'-'+case['case_id']+'.raw.json'),record)
                before=store.state(key)
                for operation in ('route','settle'):
                    for _ in range(3):assert store.execute(key,pid,operation)['replayed']
                after=store.state(key);assert before['receipts']==after['receipts'] and before['effects']==after['effects']
                try:store.execute(key,'different-process','settle')
                except ValueError as error:assert str(error)=='process_binding_mismatch'
                else:raise AssertionError('cross_process_replay_accepted')
                save(output/(arm+'-'+case['case_id']+'.replay.json'),{'before':before,'after':after,'repeated_calls':6,'cross_process_replay':'rejected'})
                summary['cases'].append(checked);progress();print(json.dumps(checked),flush=True)
        # Exercise a genuine wrong-actor completion, not a forged actor variable.
        probe_case=next(c for c in corpus['cases'] if c['case_id']=='over_limit_approve')
        for arm in DEFINITIONS:
            key='w03-actor-probe-'+run_id[:12]+'-'+arm
            pid=agent.request('POST','/process-definition/key/'+DEFINITIONS[arm]+'/start',{'businessKey':key,'variables':typed(snapshot(probe_case['payment'],key))})['id']
            owned_pids.append(pid);store.register(key,pid,arm,probe_case['payment'],reviewer_name)
            tasks=fetch();assert len(tasks)==1;do_external(tasks[0])
            task=engine.request('GET','/task?processInstanceId='+pid)[0]
            engine.request('POST','/task/'+task['id']+'/complete',{'variables':typed({'reviewerDecision':'approve','reviewerIdentity':reviewer_name,'reviewedPaymentSha256':fingerprint(probe_case['payment'])})})
            refused('completed_by_wrong_authenticated_actor',lambda:store.execute(key,pid,'settle'),key)
            save(output/(arm+'-wrong-actor.json'),{'backend':store.state(key),'operations':engine.request('GET','/history/user-operation?processInstanceId='+pid)})
            engine.request('DELETE','/process-instance/'+pid+'?skipCustomListeners=true&skipIoMappings=true')
        for path in ('/process-instance/count','/external-task/count','/incident/count'):
            assert engine.request('GET',path)['count']==0
        with store.connect() as db:
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            rows=db.execute('SELECT key,payment_id,amount_minor,beneficiary FROM effects ORDER BY key').fetchall()
        expected=[]
        for record_path in output.glob('*.raw.json'):
            r=json.loads(record_path.read_text());audit_record(r)
            if r['case']['expected_outcome']=='settled':
                p=r['case']['payment'];expected.append((r['key'],p['payment_id'],p['amount_minor'],p['beneficiary']))
        assert rows==sorted(expected)
        assert all(sha(ROOT/name)==digest for name,digest in manifest['source_sha256'].items())
        summary.update(status='passed',functional_cases_passed=len(summary['cases']),expected_effects=len(expected),
            review_tasks={arm:sum(r['review_tasks'] for r in summary['cases'] if r['arm']==arm) for arm in DEFINITIONS},
            replay_calls=120,cross_process_replays_rejected=20,active_instances=0,external_tasks=0,incidents=0)
    except BaseException as error:
        summary.update(status='failed',error_type=type(error).__name__,error=str(error));save(output/'failure.json',summary);raise
    finally:
        errors=[]
        if process and process.poll() is None:
            for grant in grants:
                try:engine.request('DELETE','/authorization/'+grant)
                except Exception as e:errors.append({'authorization':grant,'error_type':type(e).__name__})
            for user in users:
                try:engine.request('DELETE','/user/'+user)
                except Exception as e:errors.append({'user':user,'error_type':type(e).__name__})
            process.terminate()
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
        log.close();config.write_text(config.read_text().replace(json.dumps(password),'"REDACTED_AFTER_SHUTDOWN"'))
        summary.update(owned_engine_stopped=bool(process and process.poll() is not None),cleanup_errors=errors,
            temporary_principals_removed=not errors)
        if errors:summary['status']='failed'
        save(output/'summary.json',summary)
    assert not errors
    save(output/'checksums.json',{'algorithm':'sha256','files':{p.relative_to(output).as_posix():sha(p) for p in output.rglob('*') if p.is_file() and p.name!='checksums.json'}})
    print(json.dumps({'status':summary['status'],'cases':len(summary['cases']),'review_tasks':summary['review_tasks'],'effects':summary['expected_effects']}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--runtime',type=Path,default=ROOT/'.tools/fluxnova-3.0.1')
    parser.add_argument('--java',type=Path,default=ROOT/'.tools/java21/jdk-21.0.12.1+1-jre/bin/java.exe')
    parser.add_argument('--port',type=int,default=28480)
    run(parser.parse_args())


if __name__=='__main__':main()
