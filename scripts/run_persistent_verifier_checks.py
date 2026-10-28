#!/usr/bin/env python3
"""Persistent restart/SIGKILL fixtures or a separate bounded native smoke.

Requires the explicitly installed recovery-test systemd override. Management
credentials come from the pinned Vault bundle into memory; test users are removed.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from services.bound_payment import BoundReview
from services.live_recoverable_agent import identity
from services.payment_approval import clients,typed
from services.recoverable_agent import atomic_json,digest,owner
from services.worker import Client

READS=['READ','READ_INSTANCE','READ_HISTORY','READ_TASK','READ_INSTANCE_VARIABLE','READ_HISTORY_VARIABLE','READ_TASK_VARIABLE']
CASES=('pending_restart','kill_before_send','kill_after_commit_expired','reject','tampered_amount')


def run(args):
    import oci
    output=args.output;output.mkdir(parents=True,exist_ok=False)
    config=json.loads(args.verifier_config.read_text());cfg,signer,_=identity(args.profile)
    vault=oci.secrets.SecretsClient(cfg,signer=signer,timeout=(10,30),retry_strategy=oci.retry.NoneRetryStrategy())
    values=json.loads(base64.b64decode(vault.get_secret_bundle(config['secret_id'],version_number=config['secret_version'],retry_strategy=oci.retry.NoneRetryStrategy()).data.secret_bundle_content.content))
    authority=Client(args.authority_url,'Bearer '+values['management_token'])
    health_client=Client(args.authority_url)
    engine,support=clients()
    names={role:'persist'+role+uuid.uuid4().hex[:8] for role in ('agent','reviewer')}
    passwords={role:secrets.token_urlsafe(32) for role in names}
    def role_client(role):return Client(engine.base,'Basic '+base64.b64encode((names[role]+':'+passwords[role]).encode()).decode())
    agent,reviewer=role_client('agent'),role_client('reviewer')
    users=[];grants=[];results=[];started=[]
    cases=CASES if args.lane=='fixtures' else ('native_approve',)
    atomic_json(output/'plan.json',{'cases':cases,'model':'deterministic fixture' if args.lane=='fixtures' else args.model_id,
        'native_requests_max':0 if args.lane=='fixtures' else 2,'read_timeout_s':40,'wall_s':90,'automatic_retry':False,
        'users':names,'vault_version':config['secret_version'],'service':'fluxnova-bound-verifier','service_identity':values['engine_username']})

    def remote(code):
        result=subprocess.run(['ssh','-F',str(args.ssh_config),args.ssh_host,'sudo python3 -'],input=code.replace('\r\n','\n').encode(),capture_output=True,timeout=30)
        if result.returncode:
            (output/'remote-error.local.log').write_bytes(result.stdout+result.stderr)
            raise RuntimeError('remote_verifier_control_failed')
        return json.loads(result.stdout)

    def health(deadline_s=90,old_pid=None):
        deadline=time.monotonic()+deadline_s
        while time.monotonic()<deadline:
            try:
                r=health_client.request('GET','/health')
                if r['ready'] and r['vault_version']==config['secret_version'] and (old_pid is None or r['pid']!=old_pid):return r
            except (urllib.error.URLError,TimeoutError,ConnectionError):pass
            time.sleep(.5)
        raise TimeoutError('verifier_health_not_restored')

    def control(action):
        if action not in ('restart','kill'):raise ValueError('unknown_control')
        command=(['systemctl','restart','fluxnova-bound-verifier'] if action=='restart' else
                 ['systemctl','kill','--signal=SIGKILL','--kill-whom=main','fluxnova-bound-verifier'])
        return remote('import subprocess,json\nsubprocess.run('+repr(command)+',check=True)\nprint(json.dumps({"action":'+repr(action)+'}))')

    def checkpoint(event,arm=False):
        if event not in ('before_ledger_send','after_ledger_commit'):raise ValueError('unknown_checkpoint')
        if arm:
            code="from pathlib import Path\nimport json\np=Path('/var/lib/fluxnova-bound-verifier/faults')\nmarker=p/"+repr(event+'.reached.json')+"\nif marker.exists():marker.unlink()\n(p/"+repr(event+'.arm')+").write_text('armed')\nprint(json.dumps({'armed':True}))"
            return remote(code)
        code="from pathlib import Path\nimport json\np=Path('/var/lib/fluxnova-bound-verifier/faults')/"+repr(event+'.reached.json')+"\nprint(p.read_text() if p.exists() else 'null')"
        return remote(code)

    def invoke(folder,case,intent_file,resume=False):
        env={**os.environ,'BENCH_USERNAME':names['agent'],'BENCH_PASSWORD':passwords['agent'],'PYTHONPATH':str(ROOT)}
        module='services.approval_agent' if args.lane=='fixtures' else 'services.live_recoverable_agent'
        env['OCI_HEADER_PARSING_ERROR_MAX_RETRIES']='0'
        command=[sys.executable,'-m',module,'--output',str(folder),'--case',case,'--bound-intent',str(intent_file),'--wall-s','90']
        if args.lane=='native':command.extend(['--model-id',args.model_id,'--model-region',args.model_region,
                                              '--compartment-id',args.compartment_id,'--profile',args.profile,'--read-timeout-s','40'])
        if resume:command.append('--resume')
        r=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,timeout=120)
        folder.mkdir(parents=True,exist_ok=True);n=len(list(folder.glob('driver-*.log')))+1
        (folder/('driver-'+str(n)+'.log')).write_bytes(r.stdout+r.stderr)
        if r.returncode:raise RuntimeError('fixture_agent_failed')
        return json.loads((folder/'report.json').read_text())

    def finish(pid):
        deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            result=engine.request('GET','/history/process-instance/'+pid)
            if result.get('endTime'):
                if result['state']!='COMPLETED':raise ValueError('unexpected_terminal_state')
                return result
            time.sleep(.5)
        raise TimeoutError('persistent_payment_completion_timeout')

    def save():atomic_json(output/'progress.json',{'users':users,'grants':grants,'results':results,'started':started})
    def deny(name,client,method,path,body):
        try:client.request(method,path,body)
        except urllib.error.HTTPError as error:
            status=error.code;(output/(name+'.json')).write_bytes(error.read());error.close()
            if status!=401:raise ValueError('management_auth_not_enforced')
            return status
        raise ValueError('management_request_was_not_denied')
    try:
        before_health=health();atomic_json(output/'health-before.json',before_health)
        deny('unauthenticated-issuance',Client(args.authority_url),'POST','/intents',{})
        deny('wrong-token-issuance',Client(args.authority_url,'Bearer invalid'),'POST','/intents',{})
        for role,user in names.items():
            engine.request('POST','/user/create',{'profile':{'id':user,'firstName':'Persistent','lastName':role},'credentials':{'password':passwords[role]}})
            users.append(user);save()
            definitions=('bench_agent_screen','bench_agent_direct','bench_bound_payment_approval') if role=='agent' else ('bench_bound_payment_approval',)
            for definition in definitions:
                grants.append(engine.request('POST','/authorization/create',{'type':1,'userId':user,'resourceType':6,'resourceId':definition,'permissions':READS+(['CREATE_INSTANCE'] if role=='agent' else ['TASK_WORK'])}));save()
            if role=='agent':
                grants.append(engine.request('POST','/authorization/create',{'type':1,'userId':user,'resourceType':8,'resourceId':'*','permissions':['CREATE']}));save()
        for kind in cases:
            folder=output/kind;folder.mkdir();payment={'amount_minor':2000,'limit_minor':1000,'authorized':True,'sanctions_match':False}
            issue={'request_id':'persistent-'+uuid.uuid4().hex,'payment':payment,'reviewer':names['reviewer'],'ttl_s':20 if kind=='kill_after_commit_expired' else 3600}
            atomic_json(folder/'issuance-request.json',issue)
            intent=authority.request('POST','/intents',issue);intent_file=folder/'intent.local.json';atomic_json(intent_file,intent)
            if authority.request('POST','/intents',issue)!=intent:raise ValueError('issuance_replay_created_new_intent')
            driver=folder/'agent';initial=invoke(driver,'over_limit',intent_file)
            if initial['status']!='pending_review':raise ValueError('persistent_review_not_pending')
            j=json.loads((driver/'checkpoint.json').read_text());review_dir=driver/'review'
            pending=BoundReview(review_dir,agent,support,intent).status();pid=pending['process_id']
            started.append({'pid':pid,'key':intent['key']});save()
            binding={'process_id':pid,'screening_id':j['workflows']['kyc']['process_instance_id'],'screening_key':j['request_id']+':kyc'}
            authority.request('POST','/intents/'+intent['key']+'/bind',binding)
            bound_before=authority.request('GET','/intents/'+intent['key']);record={'case':kind,'initial':initial,'before_health':health(),'binding':bound_before}
            if kind=='pending_restart':
                old=record['before_health']['pid'];control('restart');record['after_restart']=health(old_pid=old)
                if authority.request('POST','/intents',issue)!=intent:raise ValueError('restart_lost_issuance_identity')
                if authority.request('GET','/intents/'+intent['key'])!=bound_before:raise ValueError('restart_lost_binding')
                if BoundReview(review_dir,agent,support,intent).status()!=pending:raise ValueError('restart_changed_pending_review')
            event='before_ledger_send' if kind=='kill_before_send' else 'after_ledger_commit' if kind=='kill_after_commit_expired' else None
            if event:checkpoint(event,arm=True)
            decision='reject' if kind=='reject' else 'approve'
            changes={'reviewerDecision':decision,'reviewerIdentity':names['reviewer'],'reviewedPaymentSha256':digest(payment)}
            if kind=='tampered_amount':changes['paymentAmountMinor']=3000
            reviewer.request('POST','/task/'+pending['task_id']+'/complete',{'variables':typed(changes)})
            if event:
                deadline=time.monotonic()+45;marker=None
                while time.monotonic()<deadline and marker is None:marker=checkpoint(event);time.sleep(.3)
                if marker is None or marker['process_id']!=pid:raise ValueError('persistent_checkpoint_not_reached')
                ledger_before=support.request('GET','/state/'+intent['key']);expected_effect=1 if event=='after_ledger_commit' else 0
                if ledger_before['effect_count']!=expected_effect:raise ValueError('checkpoint_effect_mismatch')
                record['checkpoint']={'marker':marker,'ledger_before':ledger_before}
                control('kill');record['after_restart']=health(old_pid=marker['pid'])
            history=finish(pid)
            with owner(review_dir):verified=BoundReview(review_dir,engine,support,intent).status()
            expected='review_rejected' if kind=='reject' else 'integrity_rejected' if kind=='tampered_amount' else 'settled'
            if verified['outcome']!=expected or verified['effect_count']!=int(expected=='settled'):raise ValueError('persistent_outcome_mismatch')
            ledger=support.request('GET','/state/'+intent['key'])
            if kind=='kill_after_commit_expired':
                if time.time()<bound_before['expires_utc_s'] or ledger!=record['checkpoint']['ledger_before']:raise ValueError('expired_committed_payment_not_reconciled')
                record['reconciled_after_intent_expiry']=True;record['ledger_unchanged_after_kill']=True
            final=invoke(driver,'over_limit',intent_file,resume=True);replay=invoke(driver,'over_limit',intent_file,resume=True)
            counter='model_fixture_requests_total' if args.lane=='fixtures' else 'native_requests_reserved'
            if final['status']!=expected or replay['status']!=expected or final[counter]!=2 or replay[counter]!=2:raise ValueError('persistent_agent_replay_mismatch')
            if authority.request('GET','/intents/'+intent['key'])!=bound_before:raise ValueError('persistent_binding_changed')
            record.update(status='pass',history=history,verified=verified,ledger=ledger,final=final,replay=replay,
                          screening_process_id=binding['screening_id'],screening_key=binding['screening_key'])
            results.append(record);atomic_json(folder/'result.json',record);save()
            print(json.dumps({'case':kind,'status':'pass','outcome':expected,'effects':verified['effect_count']}),flush=True)
        queues={k:engine.request('GET',p)['count'] for k,p in [('active_instances','/process-instance/count'),('external_tasks','/external-task/count'),('incidents','/incident/count')]}
        if any(queues.values()):raise ValueError('persistent_queues_not_empty')
        atomic_json(output/'summary.json',{'status':'pass','results':results,'queues':queues,'health_after':health(),'native_requests':0 if args.lane=='fixtures' else 2})
    except Exception as error:
        atomic_json(output/'failure.json',{'error_type':type(error).__name__,'message':str(error) if isinstance(error,ValueError) else None});raise
    finally:
        cleanup={'users_deleted':[],'grants_deleted':[],'closed_pending':[],'failures':[]}
        for row in started:
            for task in engine.request('GET','/task?'+urllib.parse.urlencode({'processInstanceId':row['pid']})):
                if task['taskDefinitionKey']=='payment_review':
                    engine.request('POST','/task/'+task['id']+'/complete',{'variables':typed({'reviewerDecision':'reject'})});cleanup['closed_pending'].append(row['pid'])
        for grant in reversed(grants):
            try:engine.request('DELETE','/authorization/'+grant['id']);cleanup['grants_deleted'].append(grant['id'])
            except Exception as error:cleanup['failures'].append(type(error).__name__)
        for user in reversed(users):
            try:engine.request('DELETE','/user/'+user);cleanup['users_deleted'].append(user)
            except Exception as error:cleanup['failures'].append(type(error).__name__)
        atomic_json(output/'cleanup.json',cleanup)
        if cleanup['failures']:raise RuntimeError('persistent_test_identity_cleanup_failed')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--verifier-config',type=Path,required=True)
    parser.add_argument('--ssh-config',type=Path,required=True);parser.add_argument('--ssh-host',default='fluxnova-support')
    parser.add_argument('--authority-url',default='http://127.0.0.1:19082');parser.add_argument('--profile',default='BENCHMARK')
    parser.add_argument('--lane',choices=('fixtures','native'),default='fixtures')
    parser.add_argument('--model-id');parser.add_argument('--model-region',default='us-chicago-1');parser.add_argument('--compartment-id')
    args=parser.parse_args()
    if args.lane=='native' and not (args.model_id and args.compartment_id):parser.error('native requires model-id and compartment-id')
    run(args)
