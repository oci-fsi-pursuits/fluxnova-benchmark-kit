"""W02 arrival accounting and oracle checks; HTTP fixtures are not Fluxnova results."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, unquote, urlsplit

from benchlib.runner import extract_event, normalize_event
from benchlib.w02_qualification import assess, expected_case
from services.workflow_checks import fingerprint

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('BENCH_TEST_K6_BINARY'), 'requires an explicit k6 executable')
class W02DriverIntegrationTests(unittest.TestCase):
    def exercise(self, mode='healthy', extra=None, corpus=None):
        cases, keys = {}, {}
        def outcome(case):
            p,s=case['payment'],case['schedule']
            kyc='unknown' if s['kyc']['fault']=='unknown' else 'rejected' if p['sanctions_match'] else 'review' if p['high_risk'] else 'approved'
            risk='unknown' if s['risk']['fault']=='unknown' else 'rejected' if p['stale_market'] or p['amount_minor']>p['limit_minor'] else 'accepted'
            end='blocked' if 'unknown' in (kyc,risk) else 'settled' if p['authorized'] and kyc=='approved' and risk=='accepted' else 'rejected'
            return kyc,risk,end
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def reply(self, status, body):
                raw=json.dumps(body).encode(); self.send_response(status)
                self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw)))
                self.end_headers()
                try: self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError): pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path.endswith('/start'):
                    key=body['businessKey']; keys[key]=key
                    return self.reply(200,{'id':key})
                if self.path=='/cases/register':
                    if mode=='registration_unknown': return self.reply(503,{'error':'not_confirmed'})
                    cases[body['case_key']]=body['case']
                    return self.reply(200,{'case_key':body['case_key'],'process_id':body['process_id'],'input_sha256':fingerprint(body['case'])})
                self.reply(404,{})
            def do_GET(self):
                path=urlsplit(self.path); query=parse_qs(path.query)
                if path.path.startswith('/history/process-instance/'):
                    key=unquote(path.path.rsplit('/',1)[-1])
                    return self.reply(200,{'id':key,'businessKey':key,'state':'COMPLETED',
                        'endTime':None if mode=='never_completes' else '2026-10-27T00:00:00Z'})
                if path.path=='/history/variable-instance':
                    if mode=='verification_delayed': time.sleep(.35)
                    if mode=='verification_unavailable': return self.reply(503,{})
                    values=outcome(cases[query['processInstanceId'][0]])
                    return self.reply(200,[{'name':name,'value':value} for name,value in
                        zip(('w02Kyc','w02Risk','w02Outcome'),values)])
                if path.path=='/history/activity-instance':
                    return self.reply(200,[{'activityType':'noneEndEvent','activityId':outcome(cases[query['processInstanceId'][0]])[2],'endTime':'2026-10-27T00:00:00Z'}])
                if path.path.startswith('/state/'):
                    key=unquote(path.path[len('/state/'):]); case=cases[key]; digest=fingerprint(case)
                    receipts={op:{'case_key':key,'process_id':key,'operation':op,'input_sha256':digest,
                                  'outcome':result,'started_ns':100+i*100,'finished_ns':200+i*100}
                              for i,(op,result) in enumerate(zip(('kyc','risk','settle'),outcome(case)))}
                    effect={name:case['payment'][name] for name in ('payment_id','amount_minor','beneficiary')}
                    if mode=='wrong_effect': effect['amount_minor']+=1
                    if mode=='cross_process_receipt': receipts['risk']['process_id']='another-process'
                    return self.reply(200,{'case_key':key,'process_id':key,'input_sha256':digest,
                                          'receipts':receipts,'effects':[effect] if outcome(case)[2]=='settled' or mode=='wrong_effect' else [],'audit':[]})
                self.reply(404,{})
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                env={key:value for key,value in os.environ.items() if not key.startswith('W02_')}
                env.update(W02_ENGINE_URL=f'http://127.0.0.1:{server.server_port}',
                           W02_SUPPORT_URL=f'http://127.0.0.1:{server.server_port}',
                           W02_RUN_ID='synthetic-driver-test',W02_ARM='sequential',W02_USERNAME='fixture',
                           W02_PASSWORD='fixture',W02_REGISTRATION_TOKEN='fixture',W02_RATE='1',
                           W02_WARMUP_S='0',W02_DURATION_S='2',W02_DRAIN_S='2',W02_VUS='4',W02_OUTPUT_DIR=folder)
                env.update(extra or {})
                if corpus is not None:
                    corpus_file=Path(folder)/'mixed-corpus.json'
                    corpus_file.write_text(json.dumps(corpus),encoding='utf-8')
                    env.update(W02_CORPUS_FILE=str(corpus_file),W02_CORPUS_SHA256=hashlib.sha256(corpus_file.read_bytes()).hexdigest())
                proc=subprocess.run([os.environ['BENCH_TEST_K6_BINARY'],'run','--log-format','json','--no-color',
                                     str(ROOT/'load/workflow_checks.js')],env=env,capture_output=True,text=True,timeout=25)
                events=[]
                for line in (proc.stdout+'\n'+proc.stderr).splitlines():
                    item=extract_event(line)
                    if item: events.append(normalize_event(item))
                path=Path(folder)/'k6-summary.json'
                self.assertTrue(path.is_file(),proc.stderr)
                return proc.returncode,events,json.loads(path.read_text())
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_half_rate_counts_exact_offered_arrivals(self):
        code,events,summary=self.exercise(extra={'W02_RATE':'0.5','W02_DURATION_S':'4'})
        self.assertEqual(code,0)
        self.assertEqual(summary['w02']['measurement'],{'scheduled':2,'started':2,'completed':2,'dropped':0})
        self.assertEqual([e['elapsed_s'] for e in events if e['event']=='scheduled'],[0,2])
        self.assertTrue(all(e['status']=='correct' for e in events if e['event']=='completed'))

    def test_wrong_effect_and_cross_process_receipt_are_not_success(self):
        for mode in ('wrong_effect','cross_process_receipt'):
            with self.subTest(mode=mode):
                _,events,_=self.exercise(mode)
                done=[e for e in events if e['event']=='completed']
                self.assertEqual(len(done),2)
                self.assertTrue(all(e['status']=='incorrect' for e in done))
                if mode=='wrong_effect': self.assertTrue(all(e['details']['safety_violation'] for e in done))

    def test_verification_wait_is_separate_from_observed_completion(self):
        _,events,_=self.exercise('verification_delayed')
        done=[e for e in events if e['event']=='completed']
        self.assertEqual(len(done),2)
        self.assertTrue(all(e['details']['validation_elapsed_s']>=.35 for e in done))
        self.assertTrue(all(e['latency_s']<e['details']['validation_elapsed_s'] for e in done))
        self.assertTrue(all(e['at']==e['details']['observed_complete_at'] for e in done))

    def test_missing_verification_stays_unknown(self):
        _,events,_=self.exercise('verification_unavailable')
        self.assertTrue(all(e['status']=='unknown' for e in events if e['event']=='completed'))
        self.assertEqual(sum(e['event']=='completed' for e in events),2)

    def test_unknown_registration_aborts_warmup_before_measurement(self):
        code,events,_=self.exercise('registration_unknown',{'W02_WARMUP_S':'6'})
        self.assertEqual(code,108)
        self.assertTrue(any(e['details'].get('reason')=='registration_not_confirmed' for e in events))
        self.assertFalse(any(e['phase']=='measurement' for e in events))

    def test_dropped_starts_remain_in_offered_denominator(self):
        _,events,summary=self.exercise('never_completes',{'W02_DURATION_S':'4','W02_DRAIN_S':'3','W02_VUS':'1'})
        self.assertEqual(summary['w02']['measurement']['scheduled'],4)
        self.assertGreater(summary['w02']['measurement']['dropped'],0)
        self.assertLess(summary['w02']['measurement']['started'],4)
        self.assertFalse(any(e['event']=='completed' for e in events))

    def mixed_fixture(self):
        base=json.loads((ROOT/'workloads/w02/cases.json').read_text(encoding='utf-8'))['cases']
        return {'cases':[base[0],base[3],base[5]]}

    def test_mixed_settlements_refusals_and_safe_stops_remain_separate(self):
        corpus=self.mixed_fixture()
        code,events,summary=self.exercise(extra={'W02_DURATION_S':'6','W02_DRAIN_S':'5'},corpus=corpus)
        final={'active_instances':0,'external_tasks':0,'incidents':0,'completed_instances':6,'effects':2,'registrations':6,'receipts':18}
        calibration={'valid':True,'requested_http_rate':50}
        result=assess(events,summary,code,calibration,final,corpus=corpus,require_deadlines=False)
        self.assertEqual(result['status'],'passed',result['issues'])
        for kind in ('settled','rejected','blocked'):
            self.assertEqual(result['cohorts']['measurement']['outcomes'][kind]['verified'],2)
        self.assertAlmostEqual(result['cohorts']['measurement']['on_time_settlements_per_s'],2/6)
        missing=assess(events,summary,code,calibration,final)
        self.assertIn('mixed_corpus_required',missing['issues'])
        # The live driver evidence above checks actual clocks and outcomes.
        # Use declared synthetic clocks for the deadline-policy assertions:
        # adding three seconds to six seconds of live arrivals made the
        # backlog startup comparison depend on millisecond scheduling jitter.
        for arrival_lag in (0, .05):
            with self.subTest(synthetic_arrival_lag=arrival_lag):
                late_events=copy.deepcopy(events)
                for event in late_events:
                    planned=int(event['logical_request_id'].rsplit(':',1)[1])
                    if event['event']=='started':
                        event['elapsed_s']=planned+arrival_lag
                    elif event['event']=='accepted':
                        event['elapsed_s']=planned+arrival_lag+.01
                    elif event['event']=='completed':
                        event['latency_s']=2.5
                        event['elapsed_s']=planned+2.5
                        event['details']['observed_complete_elapsed_s']=event['elapsed_s']
                late=assess(late_events,summary,code,calibration,final,corpus=corpus,require_deadlines=False)
                self.assertEqual(late['status'],'passed',late['issues'])
                self.assertTrue(late['observed_backlog']['bounded_under_declared_guard'])
                self.assertEqual(late['cohorts']['measurement']['on_time_correct'],0)
                strict=assess(late_events,summary,code,calibration,final,corpus=corpus)
                self.assertIn('measurement_deadline_miss',strict['issues'])

    def test_mixed_unexpected_effect_is_unsafe_even_when_refusal_was_expected(self):
        _,events,_=self.exercise('wrong_effect',{'W02_DURATION_S':'3'},corpus=self.mixed_fixture())
        done=[e for e in events if e['event']=='completed']
        self.assertEqual(len(done),3)
        self.assertTrue(all(e['status']=='incorrect' and e['details']['safety_violation'] for e in done))


class W02AssessmentTests(unittest.TestCase):
    def healthy(self):
        events=[]
        for i in range(20):
            key=f'test:measurement:{i}'; case=expected_case(i,'measurement'); digest=fingerprint(case)
            receipts={op:{'case_key':key,'process_id':key,'operation':op,'input_sha256':digest,
                          'outcome':outcome,'started_ns':100+n*100,'finished_ns':200+n*100}
                      for n,(op,outcome) in enumerate([('kyc','approved'),('risk','accepted'),('settle','settled')])}
            data={'original':case,'process_instance_id':key,'backend':{'case_key':key,'process_id':key,
                  'input_sha256':digest,'receipts':receipts,'effects':[{name:case['payment'][name]
                  for name in ('payment_id','amount_minor','beneficiary')}]},
                  'history':{'instance':{'id':key,'businessKey':key,'state':'COMPLETED'},
                             'variables':{'w02Outcome':'settled','w02Kyc':'approved','w02Risk':'accepted'}},
                  'activities':[{'activityType':'noneEndEvent','activityId':'settled','endTime':'done'}]}
            for kind,offset in [('scheduled',0),('started',.01),('accepted',.02),('completed',1.2)]:
                events.append({'event':kind,'logical_request_id':key,'phase':'measurement','elapsed_s':i+offset,
                               'status':'correct' if kind=='completed' else 'unknown',
                               'details':copy.deepcopy(data) if kind=='completed' else {'arrival_lag_s':.01},'latency_s':1.2})
        summary={'w02':{'run_id':'test','arm':'sequential','rate':1,'duration_s':20,'warmup_s':0,'deadline_s':2,
                        'warmup':{'scheduled':0,'started':0,'dropped':0},
                        'measurement':{'scheduled':20,'started':20,'dropped':0}},
                 'metrics':{'http_reqs':{'values':{'rate':10}}}}
        final={'active_instances':0,'external_tasks':0,'incidents':0,'completed_instances':20,
               'effects':20,'registrations':20,'receipts':60}
        return events,summary,final

    def test_independent_audit_rejects_self_reported_success_after_effect_mutation(self):
        events,summary,final=self.healthy()
        calibration={'valid':True,'requested_http_rate':50}
        self.assertEqual(assess(events,summary,0,calibration,final)['status'],'passed')
        done=next(e for e in events if e['event']=='completed')
        done['details']['backend']['effects'][0]['amount_minor']=999
        result=assess(events,summary,0,calibration,final)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['cohorts']['measurement']['correct'],19)

    def test_arrival_lag_is_computed_from_clock_not_claimed_flag(self):
        events,summary,final=self.healthy()
        for e in events:
            if e['event']=='started': e['elapsed_s']+=.5
        result=assess(events,summary,0,{'valid':True,'requested_http_rate':50},final)
        self.assertIn('measurement_arrival_lag_exceeds_100ms',result['issues'])

    def test_empty_or_aborted_run_cannot_pass(self):
        summary={'w02':{'arm':'sequential','rate':1,'duration_s':20,'deadline_s':2,
                        'warmup':{'scheduled':0,'started':0,'dropped':0},
                        'measurement':{'scheduled':20,'started':0,'dropped':0}},'metrics':{}}
        result=assess([],summary,108,{'valid':True,'requested_http_rate':50},{})
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['cohorts']['measurement']['offered'],20)
        self.assertIn('measurement_counts_or_outcomes_failed',result['issues'])
        self.assertIn('load_process_failed',result['issues'])
        self.assertFalse(result['cloud_generator_headroom_validated'])
        self.assertIsNone(result['performance_claim'])

    def test_reversed_receipt_or_lifecycle_clock_cannot_pass(self):
        for target in ('receipt','lifecycle'):
            with self.subTest(target=target):
                events,summary,final=self.healthy()
                if target=='receipt':
                    next(e for e in events if e['event']=='completed')['details']['backend']['receipts']['risk']['finished_ns']=1
                else:
                    next(e for e in events if e['event']=='accepted')['elapsed_s']=3
                result=assess(events,summary,0,{'valid':True,'requested_http_rate':50},final)
                self.assertEqual(result['status'],'failed')
                self.assertEqual(result['cohorts']['measurement']['correct'],19)


if __name__=='__main__': unittest.main()
