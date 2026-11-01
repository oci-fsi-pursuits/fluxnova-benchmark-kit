import copy
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from benchlib.fnv06_load_qualification import parse_actions
from scripts.audit_fnv06_load_qualification import check_driver,check_observer,expected_requests

ROOT=Path(__file__).resolve().parents[1]


def driver_fixture(kind='message',population=4,scale=False):
    spec={'kind':kind,'population':population}
    processes={'m'+str(i).zfill(4):{'id':'pid-'+str(i)} for i in range(population)}
    wanted=expected_requests(spec,processes,'fixture')
    fixture={'study_id':'FNV-06-LQ-SCALE' if scale else 'FNV-06-LQ','run_id':'a'*32,'observation_id':'fixture','kind':kind,
             'population':population,'vus':min(4,population) if kind=='message' else 1,
             'release_epoch_ms':1000,'requests':wanted}
    actions=[{'study_id':fixture['study_id'],'run_id':fixture['run_id'],'observation_id':'fixture','index':i,
              'vu':i%fixture['vus']+1,'request':r,'status':204 if kind=='signal' else 200,'error_code':0,
              'started_epoch_ms':1010+i*10,'finished_epoch_ms':1015+i*10,'elapsed_ms':5,'scheduled_epoch_ms':1000}
              for i,r in enumerate(wanted)]
    summary={'engine_url':'http://127.0.0.1:8080/engine-rest','run_id':fixture['run_id'],'observation_id':'fixture',
             'fixed_vus':fixture['vus'],'planned_actions':len(wanted),
             'metrics':{k:{'values':{'count':len(wanted)}} for k in ('iterations','http_reqs')}}
    return fixture,actions,summary,wanted


def observer_fixture():
    release=1000000
    path='/history/process-instance/count?processDefinitionId=definition&finished=true'
    requests=[];polls=[]
    for i,(count,start) in enumerate([(0,-.1),(2,.1),(4,.2)]):
        finish=start+.02
        stamp=lambda s:datetime.fromtimestamp(release/1000+s,timezone.utc).isoformat()
        requests.append({'index':i,'method':'GET','path':path,'status':200,'response':{'count':count},
                         'started_at':stamp(start),'finished_at':stamp(finish)})
        polls.append({'request_index':i,'count':count,'start_elapsed_s':start,'finish_elapsed_s':finish})
    return {'exit_code':0,'owned_generator_stopped':True,'release_epoch_ms':release,'poll_path':path,'polls':polls},requests


class Fnv06LoadAuditTests(unittest.TestCase):
    def test_cloud_script_bootstraps_imports_from_an_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            result=subprocess.run([sys.executable,str(ROOT/'scripts/run_fnv06_load_cloud.py'),'invalid'],
                cwd=directory,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,1)
        self.assertEqual(result.stderr.strip(),
            'Use run from an owned stage, or job inside the prepared private container.')

    def driver(self, fixture,actions,summary,wanted):
        return check_driver(fixture,actions,summary,expected=wanted,engine_url='http://127.0.0.1:8080/engine-rest')

    def observe(self,observer,requests):
        return check_observer(observer,requests,population=4,definition_id='definition',release_epoch_ms=1000000,first_action_ms=1000010)

    def test_all_three_request_semantics_qualify(self):
        for kind in ('message','signal','batch'):
            with self.subTest(kind=kind):self.driver(*driver_fixture(kind))

    def test_missing_duplicate_or_failed_action_cannot_hide_behind_k6_success(self):
        for change in ('missing','duplicate','failure','transport','body','iteration_count','extra_http'):
            f,a,s,w=driver_fixture()
            if change=='missing':a.pop()
            if change=='duplicate':a[1]['index']=a[0]['index']
            if change=='failure':a[0]['status']=500
            if change=='transport':a[0]['error_code']=1200
            if change=='body':a[0]['request']=dict(a[0]['request'],body={})
            if change=='iteration_count':s['metrics']['iterations']['values']['count']-=1
            if change=='extra_http':s['metrics']['http_reqs']['values']['count']+=1
            with self.subTest(change=change),self.assertRaises(AssertionError):self.driver(f,a,s,w)

    def test_clock_jump_early_dispatch_and_late_generator_are_rejected(self):
        for change in ('clock','early','late','nan','wrong_run'):
            f,a,s,w=driver_fixture()
            if change=='clock':a[0]['elapsed_ms']+=1000
            if change=='early':a[0]['started_epoch_ms']=999
            if change=='late':
                for r in a:r['started_epoch_ms']+=2000;r['finished_epoch_ms']+=2000
            if change=='nan':a[0]['elapsed_ms']=float('nan')
            if change=='wrong_run':a[0]['run_id']='other'
            with self.subTest(change=change),self.assertRaises(AssertionError):self.driver(f,a,s,w)

    def test_observer_brackets_completion_without_using_batch_ack(self):
        result=self.observe(*observer_fixture());self.assertAlmostEqual(result['all_recipients_completion_interval_s'][0],.1)
        self.assertAlmostEqual(result['completion_observed_s'],.22)

    def test_missing_unbound_regressing_or_unfinished_completion_is_rejected(self):
        for change in ('endpoint','body','clock','missing','regression','already_done','failed_generator'):
            o,r=observer_fixture()
            if change=='endpoint':r[-1]['path']='/batch/count'
            if change=='body':r[-1]['response']={'count':3}
            if change=='clock':o['polls'][0]['start_elapsed_s']-=2
            if change=='missing':o['polls'].pop()
            if change=='regression':o['polls'][0]['count']=3;r[0]['response']={'count':3}
            if change=='already_done':o['polls'][0]['count']=4;r[0]['response']={'count':4}
            if change=='failed_generator':o['exit_code']=1
            with self.subTest(change=change),self.assertRaises(AssertionError):self.observe(o,r)


@unittest.skipUnless(os.environ.get('BENCH_TEST_K6_BINARY'),'requires an explicit real k6 executable')
class Fnv06NativeDriverTests(unittest.TestCase):
    def exercise(self,kind,population=4,fail=False,scale=False,campaign=False):
        fixture,_,_,wanted=driver_fixture(kind,population,scale);seen=[]
        if campaign:fixture.update(study_id='FNV-06-SCALING-1',observation_id='C001M')
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));seen.append((self.path,body))
                status=500 if fail else 204 if kind=='signal' else 200
                value=None if kind=='signal' else {'id':'batch'} if kind=='batch' else [{'resultType':'Execution','execution':{'processInstanceId':body['businessKey']}}]
                raw=b'' if status==204 else json.dumps(value).encode()
                self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                folder=Path(directory);fixture['release_epoch_ms']=int(time.time()*1000)+750
                source=folder/'input.json';source.write_text(json.dumps(fixture));target=folder/'summary.json'
                base='http://127.0.0.1:'+str(server.server_port)
                env=dict(os.environ,FNV06_CASE_FILE=str(source),FNV06_CASE_SHA256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    FNV06_ENGINE_URL=base,FNV06_AUTH='Basic test-fixture',FNV06_SUMMARY=str(target),K6_NO_USAGE_REPORT='true')
                process=subprocess.run([os.environ['BENCH_TEST_K6_BINARY'],'run','--log-format','json','--no-color',str(ROOT/'load/fnv06_events.js')],
                    env=env,capture_output=True,text=True,timeout=20)
                self.assertEqual(process.returncode,0,process.stderr)
                actions=parse_actions(process.stdout+'\n'+process.stderr);summary=json.loads(target.read_text())
                self.assertEqual(len(seen),len(wanted));self.assertEqual(summary['fixture_sha256'],hashlib.sha256(source.read_bytes()).hexdigest())
                return fixture,actions,summary,wanted,base
        finally:server.shutdown();server.server_close();thread.join(timeout=2)

    def test_real_shared_burst_accounts_for_all_requests_and_one_recipient(self):
        for population in (1,16):
            with self.subTest(population=population):
                f,a,s,w,b=self.exercise('message',population);check_driver(f,a,s,expected=w,engine_url=b)

    def test_real_signal_and_batch_are_single_requests(self):
        for kind in ('signal','batch'):
            with self.subTest(kind=kind):
                f,a,s,w,b=self.exercise(kind);check_driver(f,a,s,expected=w,engine_url=b)

    def test_real_http_failure_is_retained_and_rejected_despite_zero_exit(self):
        f,a,s,w,b=self.exercise('message',fail=True)
        with self.assertRaises(AssertionError):check_driver(f,a,s,expected=w,engine_url=b)

    def test_real_large_protocol_accounts_for_one_thousand_requests_or_recipients(self):
        for kind in ('message','signal','batch'):
            with self.subTest(kind=kind):
                f,a,s,w,b=self.exercise(kind,1000,scale=True)
                check_driver(f,a,s,expected=w,engine_url=b)

    def test_real_campaign_requests_preserve_success_and_failure(self):
        for kind,fail in [('message',False),('signal',True),('batch',False)]:
            with self.subTest(kind=kind):
                f,a,s,w,b=self.exercise(kind,100,scale=True,campaign=True,fail=fail)
                result=check_driver(f,a,s,expected=w,engine_url=b,account_failures=True)
                self.assertEqual(result['request_accounting']['failed'],1 if fail else 0)


if __name__=='__main__':unittest.main()
