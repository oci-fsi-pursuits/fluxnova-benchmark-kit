import copy
import hashlib
import json
import unittest

from benchlib.w01_pacing import audit, payload


def fixture(latency=.25):
    run='w01-fixture';arm='idle_only';events=[]
    settings={'run_id':run,'arm':arm,'burst_rate':20,'burst_duration_s':1,'period_s':10,
        'deadline_s':2,'poll_interval_ms':100,'fixed_vus_per_burst':32,
        'warmup_bursts':1,'measurement_bursts':1,'warmup_s':10,
        'scenarios':{'warmup_0':{'phase':'warmup','dropped':0},
                     'measurement_0':{'phase':'measurement','dropped':0}}}
    fp=hashlib.sha256(json.dumps({'operation':'settle','payload':payload()},
        sort_keys=True,separators=(',',':')).encode()).hexdigest()
    for phase,offset in [('warmup',-10),('measurement',0)]:
        for i in range(20):
            key=f'{run}:{phase}:0:{i}';pid=phase+'-'+str(i);scheduled=offset+i/20
            common={'run_id':run,'arm':arm,'phase':phase,'logical_request_id':key,
                    'scheduled_elapsed_s':scheduled}
            events.append({**common,'event':'started','elapsed_s':scheduled+.001,'details':{},'status':'unknown'})
            events.append({**common,'event':'accepted','elapsed_s':scheduled+.01,
                           'details':{'process_id':pid},'status':'unknown'})
            variables={**payload(),'scenario':'settlement','logical_request_id':key,
                       'fault':'none','delay_ms':0,'workerResult':'settled'}
            state={'idempotency_key':key,'fingerprint':fp,
                   'result':{'idempotency_key':key,'operation':'settle','outcome':'settled'},
                   'effect_count':1,'violations':[],
                   'events':[{'event':'policy_decision','details':{'allowed':True,'authorized':True,
                             'approved':False,'approval_required':False}},
                             {'event':'payment_effect','details':{'amount_minor':125,'beneficiary':'synthetic-beneficiary'}}]}
            events.append({**common,'event':'completed','elapsed_s':scheduled+latency,'latency_s':latency,
                'status':'correct','details':{'process_id':pid,'history':{'id':pid,'businessKey':key,'state':'COMPLETED'},
                    'variables':[{'name':k,'value':v} for k,v in variables.items()],
                    'activities':[{'activityId':'settled','activityType':'noneEndEvent','endTime':'done'}],
                    'backend':state}})
    state={'active_instances':0,'external_tasks':0,'incidents':0,'completed_instances':40,'effects':40,'receipts':40}
    return events,{'w01':settings},state,0


class PacingAuditTests(unittest.TestCase):
    def test_complete_accounting_and_exact_effects_pass(self):
        result=audit(*fixture())
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['cohorts']['measurement']['on_time_correct'],20)

    def test_late_correct_payments_remain_outcomes_not_excluded_runs(self):
        result=audit(*fixture(3))
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['cohorts']['measurement']['deadline_misses'],20)
        self.assertEqual(result['cohorts']['measurement']['on_time_correct'],0)

    def test_wrong_original_effect_or_variable_is_rejected(self):
        for target in ('effect','variable'):
            args=list(fixture())
            done=args[0][-1]
            if target=='effect':done['details']['backend']['events'][-1]['details']['amount_minor']=126
            else:next(v for v in done['details']['variables'] if v['name']=='amount_minor')['value']=126
            self.assertEqual(audit(*args)['status'],'failed')

    def test_missing_arrival_and_drop_cannot_improve_result(self):
        args=list(fixture());args[0]=args[0][:-3]
        args[1]['w01']['scenarios']['measurement_0']['dropped']=1
        result=audit(*args)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['cohorts']['measurement']['offered'],20)
        self.assertEqual(result['cohorts']['measurement']['correct'],19)

    def test_mixed_arm_and_duplicate_process_rejected(self):
        args=list(fixture());args[0][-1]['arm']='success_delay_100ms'
        self.assertIn('mixed_run_or_arm',audit(*args)['issues'])
        args=list(fixture())
        first=args[0][-4]['details']['process_id']
        args[0][-2]['details']['process_id']=first
        args[0][-1]['details']['process_id']=first
        args[0][-1]['details']['history']['id']=first
        self.assertIn('process_reused_across_arrivals',audit(*args)['issues'])

    def test_clock_inversion_and_duplicate_history_are_rejected(self):
        for target in ('clock','variable'):
            args=list(fixture());done=args[0][-1]
            if target=='clock':args[0][-2]['elapsed_s']=done['elapsed_s']+1
            else:done['details']['variables'].append(copy.deepcopy(done['details']['variables'][0]))
            self.assertEqual(audit(*args)['status'],'failed')


if __name__=='__main__':
    unittest.main()
