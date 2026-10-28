"""Actual verifier-process/HTTP/SQLite lifecycle against a local engine fixture.

This is not a managed Vault or live Fluxnova validation.
"""
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import parse_qs,urlsplit

from benchlib.recovery import registry_check
from services.bound_payment import IntentRegistry,payment_request
from services.key_rotation import rotate_keys
from services.mock_service import Ledger,make_server
from services.payment_approval import snapshot,typed
from services.recoverable_agent import digest
from services.signing_keys import SigningKeys
from services.worker import Client

PAYMENT={'amount_minor':2000,'limit_minor':1000,'authorized':True,'sanctions_match':False}


class RotationProcessFixtureTests(unittest.TestCase):
    def test_pending_old_new_expired_and_committed_intents_across_actual_process_restarts(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name)
        state=root/'state';state.mkdir();ledger=Ledger(root/'ledger.sqlite')
        support=make_server('127.0.0.1',0,ledger,'support-token')
        original_error=support.handle_error
        def expected_disconnect(request,address):
            if isinstance(sys.exc_info()[1],(ConnectionResetError,BrokenPipeError)):return
            original_error(request,address)
        support.handle_error=expected_disconnect
        jobs={};lock=threading.Lock()
        authorization='Basic '+base64.b64encode(b'verifier:engine-password').decode()
        class Engine(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def handle(self):
                try:super().handle()
                except (ConnectionResetError,BrokenPipeError):pass
            def reply(self,status,value):
                raw=json.dumps(value).encode();self.send_response(status)
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def handle_request(self,method):
                if self.headers.get('Authorization')!=authorization:return self.reply(401,{})
                parsed=urlsplit(self.path);path=parsed.path
                body=json.loads(self.rfile.read(int(self.headers['Content-Length']))) if method=='POST' else None
                with lock:
                    if path=='/external-task/fetchAndLock':
                        ready=next((x for x in jobs.values() if x['ready'] and not x['done']),None)
                        return self.reply(200,[ready['task']] if ready else [])
                    if path.startswith('/external-task/') and path.endswith('/complete'):
                        job=jobs[path.split('/')[2]]
                        if job['hold_completion']:return self.reply(503,{'error':'fixture_response_loss_after_ledger_commit'})
                        job.update(done=True,result=body['variables']);return self.reply(200,{})
                    if path.startswith('/history/process-instance/'):
                        pid=path.rsplit('/',1)[1]
                        for job in jobs.values():
                            if pid==job['pid']:return self.reply(200,{'businessKey':job['key'],'processDefinitionKey':'bench_bound_payment_approval'})
                            if pid==job['sid']:return self.reply(200,{'businessKey':job['skey'],'processDefinitionKey':'bench_agent_screen','state':'COMPLETED'})
                    pid=parse_qs(parsed.query).get('processInstanceId',[None])[0]
                    job=next((j for j in jobs.values() if j['pid']==pid),None)
                    if job and path=='/history/task':return self.reply(200,[{'taskDefinitionKey':'payment_review','endTime':'completed','id':'review-'+pid}])
                    if job and path=='/history/user-operation':return self.reply(200,[{'taskId':'review-'+pid,'operationType':'Complete','userId':'reviewer'}])
                    return self.reply(404,{})
            def do_GET(self):self.handle_request('GET')
            def do_POST(self):self.handle_request('POST')
        engine=ThreadingHTTPServer(('127.0.0.1',0),Engine)
        for server in [engine,support]:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            self.addCleanup(lambda s=server,t=thread:(s.shutdown(),s.server_close(),t.join()))
        old=b'o'*32;new=b'n'*32
        values={'version':1,'signing_key_b64':base64.b64encode(old).decode(),'management_token':'m'*32,
                'engine_username':'verifier','engine_password':'engine-password','support_token':'support-token'}
        v2={k:v for k,v in values.items() if k!='signing_key_b64'}
        v2.update(version=2,signing_keys_b64={'old':base64.b64encode(old).decode(),'new':base64.b64encode(new).decode()},
                  active_signing_key_id='new',legacy_signing_key_id='old')
        config={'region':'fixture','secret_id':'fixture','secret_version':1,'state_directory':str(state),
                'engine_url':'http://127.0.0.1:'+str(engine.server_port),
                'support_url':'http://127.0.0.1:'+str(support.server_port),'port':0}
        processes=[]
        def stop(process):
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        self.addCleanup(lambda:[stop(p) for p in processes])
        def wait(predicate,timeout=15):
            end=time.monotonic()+timeout
            while time.monotonic()<end:
                if predicate():return
                time.sleep(.03)
            self.fail('fixture_condition_timeout')
        def start(bundle,version):
            index=len(processes);cfg=root/f'config-{index}.json';secret=root/f'synthetic-secret-{index}.json';log=root/f'process-{index}.log'
            cfg.write_text(json.dumps({**config,'secret_version':version}));secret.write_text(json.dumps(bundle))
            code="import json,sys;from pathlib import Path;from services.verifier_service import serve,decode_secret;serve(json.loads(Path(sys.argv[1]).read_text()),secret_loader=lambda c:decode_secret(json.loads(Path(sys.argv[2]).read_text())))"
            with log.open('wb') as stream:
                p=subprocess.Popen([sys.executable,'-u','-c',code,str(cfg),str(secret)],
                    cwd=Path(__file__).resolve().parents[1],stdout=stream,stderr=stream,stdin=subprocess.DEVNULL)
            processes.append(p)
            wait(lambda:p.poll() is not None or 'verifier_started' in log.read_text())
            self.assertIsNone(p.poll(),log.read_text())
            row=next(json.loads(line) for line in log.read_text().splitlines() if 'verifier_started' in line)
            base='http://127.0.0.1:'+str(row['port'])
            health=Client(base).request('GET','/health');self.assertEqual(health['vault_version'],version)
            return p,Client(base,'Bearer '+values['management_token']),health
        def issue(api,name,ttl=3600):
            return api.request('POST','/intents',{'request_id':name,'payment':PAYMENT,'reviewer':'reviewer','ttl_s':ttl})
        def prepare(api,intent,ready=False,hold=False,tampered=False):
            index=len(jobs);pid='process-'+str(index);sid='screen-'+str(index);skey='screen-key-'+str(index);tid='task-'+str(index)
            body=IntentRegistry(state/'intents.sqlite',old if len(processes)==1 else ring).load(intent['key'])['body']
            self.assertEqual(ledger.execute(payment_request(body,'kyc',skey))[0],200)
            api.request('POST','/intents/'+intent['key']+'/bind',{'process_id':pid,'screening_id':sid,'screening_key':skey})
            token=intent['token'][:-1]+('1' if intent['token'][-1]!='1' else '2') if tampered else intent['token']
            variables={**snapshot(PAYMENT,intent['key']),'paymentBeneficiary':'synthetic-beneficiary','boundIntent':token,
                       'reviewedPaymentSha256':digest(PAYMENT),'reviewerDecision':'approve'}
            job={'pid':pid,'sid':sid,'skey':skey,'key':intent['key'],'ready':ready,'done':False,'hold_completion':hold,
                 'task':{'id':tid,'businessKey':intent['key'],'processInstanceId':pid,'variables':typed(variables)}}
            with lock:jobs[tid]=job
            return job
        p,api,_=start(values,1)
        pending=issue(api,'pending-old');pending_job=prepare(api,pending)
        committed=issue(api,'committed-old',ttl=5);committed_job=prepare(api,committed,ready=True,hold=True)
        expired=issue(api,'expired-old',ttl=1);expired_job=prepare(api,expired)
        ring=SigningKeys({'old':old,'new':new},'new','old')
        with self.assertRaises(OSError):rotate_keys(state,old,ring,apply=True)
        wait(lambda:ledger.state(committed['key'])['effect_count']==1);stop(p)
        with lock:committed_job['ready']=False
        rotate_keys(state,old,ring,apply=True)
        p,api,health=start(v2,2);self.assertEqual(health['active_signing_key_id'],'new')
        self.assertEqual(issue(api,'pending-old'),pending)
        fresh=issue(api,'new-current');fresh_job=prepare(api,fresh)
        forged=issue(api,'tampered-current');forged_job=prepare(api,forged,tampered=True)
        wait(lambda:time.time()>max(IntentRegistry(state/'intents.sqlite',ring).load(i['key'])['body']['expires_utc_s']
                                  for i in [expired,committed]))
        with lock:
            for job in jobs.values():job.update(ready=True,hold_completion=False)
        wait(lambda:all(j['done'] for j in jobs.values()))
        for intent in [pending,committed,fresh]:self.assertEqual(ledger.state(intent['key'])['effect_count'],1)
        for intent in [expired,forged]:self.assertEqual(ledger.state(intent['key'])['effect_count'],0)
        self.assertEqual(expired_job['result']['integrityReason']['value'],'intent_expired')
        self.assertEqual(forged_job['result']['workerResult']['value'],'integrity_rejected')
        old_pid=health['pid'];stop(p);p,api,health=start(v2,2)
        self.assertNotEqual(old_pid,health['pid']);self.assertEqual(issue(api,'new-current'),fresh)
        self.assertEqual(issue(api,'pending-old'),pending);stop(p)
        check=registry_check(state/'intents.sqlite',ring)
        self.assertEqual(check['intents'],5);self.assertEqual(check['issuance_requests'],5)
        for intent in [pending,committed,fresh]:self.assertEqual(ledger.state(intent['key'])['effect_count'],1)


if __name__=='__main__':unittest.main()
