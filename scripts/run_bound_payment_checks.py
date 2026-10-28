#!/usr/bin/env python3
"""Bounded signed-intent review checks; fixture lane first, separate native lane.

The runner owns the short-lived operator signer/worker. It keeps signing keys
and account passwords in memory only, and preserves failed trials without retry.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from benchlib.fluxnova import FluxnovaClient
from services.bound_payment import IntentRegistry,BoundWorker,BoundReview,DEFINITION
from services.payment_approval import clients,typed,snapshot
from services.recoverable_agent import atomic_json,digest,owner
from services.worker import Client

FIXTURES=('approve','unauthorized','reject','amount','authority','beneficiary','hash','key','signature','token_swap','actor','screen_swap','expiry')
READS=['READ','READ_INSTANCE','READ_HISTORY','READ_TASK','READ_INSTANCE_VARIABLE','READ_HISTORY_VARIABLE','READ_TASK_VARIABLE']


def run(args):
    output=args.output;output.mkdir(parents=True,exist_ok=False)
    engine,support=clients()
    names={role:'bound'+role+uuid.uuid4().hex[:8] for role in ('agent','reviewer','other')}
    passwords={role:secrets.token_urlsafe(32) for role in names}
    def as_role(role):return Client(engine.base,'Basic '+base64.b64encode((names[role]+':'+passwords[role]).encode()).decode())
    reviewer=as_role('reviewer');other=as_role('other');agent=as_role('agent')
    registry=IntentRegistry(output/'intents.sqlite',secrets.token_bytes(32))
    users=[];grants=[];results=[];worker_events=[];started=[]
    cases=list(FIXTURES) if args.lane=='fixtures' else ['approve','reject']
    plan={'lane':args.lane,'cases':cases,'max_native_requests':0 if args.lane=='fixtures' else 4,
          'max_requests_per_live_journal':2,'read_timeout_s':40,'wall_s':90,'live_retry':False,
          'model_id':args.model_id,'model_region':args.model_region,'users':names,'signing_key_persisted':False}
    atomic_json(output/'plan.json',plan)
    sources=output/'sources';sources.mkdir()
    paths=['services/bound_payment.py','services/approval_agent.py','services/live_recoverable_agent.py','services/review_payment.py',
           'services/payment_approval.py','services/payment_agent.py','services/recoverable_agent.py','services/worker.py',
           'benchlib/oci_model.py','scripts/run_bound_payment_checks.py','workloads/bound-payment-approval.bpmn','workloads/agent-screen.bpmn']
    for name in paths:
        path=sources/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,path)
    def emit(row):worker_events.append(row);atomic_json(output/'worker-events.json',worker_events)
    worker=BoundWorker(engine,support,registry,emit)
    def save():atomic_json(output/'progress.json',{'users':users,'grants':grants,'results':results,'started':started})

    def invoke(folder,case,intent_file,resume=False):
        env={**os.environ,'BENCH_USERNAME':names['agent'],'BENCH_PASSWORD':passwords['agent'],
             'PYTHONPATH':str(ROOT),'OCI_HEADER_PARSING_ERROR_MAX_RETRIES':'0'}
        module='services.approval_agent' if args.lane=='fixtures' else 'services.live_recoverable_agent'
        command=[sys.executable,'-m',module,'--output',str(folder),'--case',case,'--bound-intent',str(intent_file),'--wall-s','90']
        if args.lane=='live':command.extend(['--model-id',args.model_id,'--model-region',args.model_region,
                                            '--compartment-id',args.compartment_id,'--profile',args.profile,'--read-timeout-s','40'])
        if resume:command.append('--resume')
        result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,timeout=145)
        folder.mkdir(parents=True,exist_ok=True)
        attempt=len(list(folder.glob('driver-*.log')))+1
        (folder/('driver-'+str(attempt)+'.log')).write_bytes(result.stdout+result.stderr)
        report=json.loads((folder/'report.json').read_text()) if (folder/'report.json').exists() else None
        if result.returncode or report is None:raise RuntimeError('agent_attempt_failed_no_retry')
        return report

    def finish(pid):
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            history=engine.request('GET','/history/process-instance/'+pid)
            if history.get('endTime'):
                if history['state']!='COMPLETED':raise ValueError('unexpected_terminal_state')
                return history
            worker.poll();time.sleep(.1)
        raise TimeoutError('bound_completion_not_observed')

    def start_raw(intent,key=None):
        key=key or intent['key']
        response=agent.request('POST','/process-definition/key/'+DEFINITION+'/start',{'businessKey':key,'variables':typed({
            **snapshot(intent['payment'],intent['key']),'paymentBeneficiary':'synthetic-beneficiary',
            'boundIntent':intent['token'],'reviewerDecision':'pending'})})
        started.append({'id':response['id'],'key':key,'raw':True});save();return response['id']

    try:
        deployment=FluxnovaClient(engine.base,os.environ['BENCH_USERNAME'],os.environ['BENCH_PASSWORD']).deploy(
            [ROOT/'workloads/bound-payment-approval.bpmn',ROOT/'workloads/agent-screen.bpmn',ROOT/'workloads/agent-direct.bpmn'])
        atomic_json(output/'deployment.json',deployment)
        for role,user in names.items():
            engine.request('POST','/user/create',{'profile':{'id':user,'firstName':'Bound','lastName':role},'credentials':{'password':passwords[role]}})
            users.append(user);save()
            definitions=('bench_agent_screen','bench_agent_direct',DEFINITION) if role=='agent' else (DEFINITION,)
            for definition in definitions:
                grants.append(engine.request('POST','/authorization/create',{'type':1,'userId':user,'resourceType':6,
                    'resourceId':definition,'permissions':READS+(['CREATE_INSTANCE'] if role=='agent' else ['TASK_WORK'])}));save()
            if role=='agent':
                grants.append(engine.request('POST','/authorization/create',{'type':1,'userId':user,'resourceType':8,'resourceId':'*','permissions':['CREATE']}));save()

        first_intent=None;foreign_screen=None
        for kind in cases:
            case='over_limit_unauthorized' if kind in ('authority','unauthorized') else 'over_limit'
            if args.lane=='live':case='over_limit'
            payment={'amount_minor':2000,'limit_minor':1000,'authorized':case!='over_limit_unauthorized','sanctions_match':False}
            intent=registry.issue(payment,names['reviewer'],ttl_s=1 if kind=='expiry' else 3600)
            case_folder=output/kind;case_folder.mkdir();intent_file=case_folder/'intent.local.json';atomic_json(intent_file,intent)
            driver=case_folder/'agent'
            initial=invoke(driver,case,intent_file)
            if initial['status']!='pending_review':raise ValueError('native_or_fixture_did_not_reach_review')
            journal=json.loads((driver/'checkpoint.json').read_text());screen=journal['workflows']['kyc']
            review_folder=driver/'review';review=BoundReview(review_folder,agent,support,intent);pending=review.status();pid=pending['process_id']
            started.append({'id':pid,'key':intent['key'],'kind':kind});save()
            screen_key=journal['request_id']+':kyc';screen_id=screen['process_instance_id']
            if kind=='unauthorized':foreign_screen=(screen_id,screen_key)
            if kind=='screen_swap':
                if foreign_screen is None:raise ValueError('foreign_screen_fixture_missing')
                screen_id,screen_key=foreign_screen
            registry.bind(intent['key'],pid,screen_id,screen_key)
            decision='reject' if kind=='reject' else 'approve'
            changes={'reviewerDecision':decision,'reviewerIdentity':names['reviewer'],'reviewedPaymentSha256':digest(payment)}
            mutations={'amount':{'paymentAmountMinor':3000},'authority':{'paymentAuthorized':True},
                       'beneficiary':{'paymentBeneficiary':'other-beneficiary'},'hash':{'paymentSha256':'forged'},
                       'key':{'paymentKey':'other-key'}}
            changes.update(mutations.get(kind,{}))
            if kind=='signature':changes['boundIntent']=intent['token'][:-1]+('0' if intent['token'][-1]!='0' else '1')
            if kind=='token_swap':changes['boundIntent']=registry.issue(payment,names['reviewer'])['token']
            if kind=='expiry':
                expires=registry.load(intent['key'])['body']['expires_utc_s']
                while time.time()<expires:time.sleep(.1)
            if kind=='approve':
                # Same task completion body through the agent account must fail.
                try:agent.request('POST','/task/'+pending['task_id']+'/complete',{'variables':typed(changes)})
                except urllib.error.HTTPError as error:
                    code=error.code;(case_folder/'self-approval-denial.json').write_bytes(error.read());error.close()
                    if code!=403:raise ValueError('self_approval_denial_not_403')
                else:raise ValueError('agent_self_approval_succeeded')
            actor=other if kind=='actor' else reviewer
            actor.request('POST','/task/'+pending['task_id']+'/complete',{'variables':typed(changes)})
            history=finish(pid)
            with owner(review_folder):verified=BoundReview(review_folder,engine,support,intent).status()
            expected='settled' if kind=='approve' else 'rejected' if kind=='unauthorized' else 'review_rejected' if kind=='reject' else 'integrity_rejected'
            if verified['outcome']!=expected or verified['effect_count']!=int(expected=='settled'):raise ValueError('bound_case_outcome_mismatch')
            final=invoke(driver,case,intent_file,resume=True);replay=invoke(driver,case,intent_file,resume=True)
            counter='model_fixture_requests_total' if args.lane=='fixtures' else 'native_requests_reserved'
            if final['status']!=expected or replay['status']!=expected or final[counter]!=2 or replay[counter]!=2:
                raise ValueError('bound_agent_replay_mismatch')
            ledger=support.request('GET','/state/'+intent['key'])
            operations=engine.request('GET','/history/user-operation?'+urllib.parse.urlencode({'processInstanceId':pid}))
            atomic_json(case_folder/'operation-log.json',operations)
            result={'case':kind,'status':'pass','initial':initial,'final':final,'replay':replay,'verified':verified,'history':history,'ledger':ledger,
                    'screening_process_id':screen['process_instance_id'],'screening_key':journal['request_id']+':kyc'}
            results.append(result);atomic_json(case_folder/'result.json',result);save()
            if first_intent is None:first_intent=intent
            print(json.dumps({'lane':args.lane,'case':kind,'outcome':verified['outcome'],'effects':verified['effect_count'],'status':'pass'}),flush=True)

        duplicate=None
        if args.lane=='fixtures':
            before=support.request('GET','/state/'+first_intent['key'])
            pid=start_raw(first_intent)
            tasks=engine.request('GET','/task?'+urllib.parse.urlencode({'processInstanceId':pid}))
            reviewer.request('POST','/task/'+tasks[0]['id']+'/complete',{'variables':typed({'reviewerDecision':'approve','reviewedPaymentSha256':digest(first_intent['payment'])})})
            finish(pid)
            variables={v['name']:v.get('value') for v in engine.request('GET','/history/variable-instance?'+urllib.parse.urlencode({'processInstanceId':pid}))}
            after=support.request('GET','/state/'+first_intent['key'])
            if variables.get('workerResult')!='integrity_rejected' or before!=after:raise ValueError('cross_process_replay_not_blocked')
            duplicate={'outcome':variables['workerResult'],'reason':variables.get('integrityReason'),'original_ledger_unchanged':True,'process_id':pid}
        queues={k:engine.request('GET',p)['count'] for k,p in [('active_instances','/process-instance/count'),('external_tasks','/external-task/count'),('incidents','/incident/count')]}
        if any(queues.values()):raise ValueError('bound_queues_not_empty')
        atomic_json(output/'summary.json',{'status':'pass','lane':args.lane,'results':results,'cross_process_replay':duplicate,'queues':queues,'worker_events':worker_events})
    except Exception as error:
        failure={'error_type':type(error).__name__,'message':str(error) if isinstance(error,ValueError) else None}
        if isinstance(error,urllib.error.HTTPError):
            raw=error.read().decode(errors='replace');error.close()
            for password in passwords.values():raw=raw.replace(password,'[REDACTED]')
            failure['response']=raw[:4096]
        atomic_json(output/'failure.json',failure);raise
    finally:
        cleanup={'closed_pending':[],'users_deleted':[],'grants_deleted':[],'failures':[]}
        # Close only this runner's known pending fixtures after a failed lane.
        for row in started:
            tasks=engine.request('GET','/task?'+urllib.parse.urlencode({'processInstanceId':row['id']}))
            for task in tasks:
                if task['taskDefinitionKey']!='payment_review':continue
                engine.request('POST','/task/'+task['id']+'/complete',{'variables':typed({'reviewerDecision':'reject'})})
                cleanup['closed_pending'].append(row['id'])
        for grant in reversed(grants):
            try:engine.request('DELETE','/authorization/'+grant['id']);cleanup['grants_deleted'].append(grant['id'])
            except Exception as error:cleanup['failures'].append(type(error).__name__)
        for user in reversed(users):
            try:engine.request('DELETE','/user/'+user);cleanup['users_deleted'].append(user)
            except Exception as error:cleanup['failures'].append(type(error).__name__)
        for user in users:
            if engine.request('GET','/user?'+urllib.parse.urlencode({'id':user})):cleanup['failures'].append('temporary_user_still_exists')
        atomic_json(output/'cleanup.json',cleanup)
        if cleanup['failures']:raise RuntimeError('bound_cleanup_failed')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--lane',choices=('fixtures','live'),required=True)
    parser.add_argument('--model-id');parser.add_argument('--model-region',default='us-chicago-1')
    parser.add_argument('--compartment-id');parser.add_argument('--profile',default='BENCHMARK')
    args=parser.parse_args()
    if args.lane=='live' and not (args.model_id and args.compartment_id):parser.error('live requires model-id and compartment-id')
    run(args)
