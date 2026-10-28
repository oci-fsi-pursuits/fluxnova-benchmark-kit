import copy
import unittest

from benchlib.w01_cloud import assess_cloud, measurement_plan, require_matched_controls, require_owned_database
from tests.test_w01_pacing import fixture
import tests.test_w02_cloud as w02_tests


class W01CloudTests(unittest.TestCase):
    def test_only_explicit_success_delay_may_change(self):
        left={'containers':{'worker':{'arguments':['python','-m','services.worker','--success-delay-s','0.1'],
                                     'environment_sha256':'fixed','memory_bytes':256}}}
        right=copy.deepcopy(left);right['containers']['worker']['arguments'][-1]='0'
        require_matched_controls(left,right)
        right['containers']['worker']['memory_bytes']=512
        with self.assertRaises(ValueError):require_matched_controls(left,right)
        right=copy.deepcopy(left);right['containers']['worker']['arguments'][-1]='0.2'
        with self.assertRaises(ValueError):require_matched_controls(left,right)

    def test_owned_database_must_match_lane_and_retained_identity(self):
        run='a'*32
        target={'id':'target','display_name':'fluxnova-w01-'+run[:12],'instance_count':1,
                'source':{'source_type':'NONE'},'freeform_tags':{'managed_by':'fluxnova-benchmarks',
                'w01_run_id':run,'temporary_comparison':'true','comparison_lane':'w01'}}
        kwargs={'source_id':'source','target_id':'target','run_id':run}
        require_owned_database(target,**kwargs)
        for altered in ({**target,'id':'source'},{**target,'instance_count':2},
                        {**target,'freeform_tags':{**target['freeform_tags'],'comparison_lane':'w02'}}):
            with self.assertRaises(ValueError):require_owned_database(altered,**kwargs)
        with self.assertRaises(ValueError):require_owned_database(target,**{**kwargs,'source_id':'target'})

    def test_five_pairs_have_fixed_denominators_and_balanced_order(self):
        plan=measurement_plan('b'*32)
        self.assertEqual(plan,measurement_plan('b'*32))
        self.assertEqual(len(plan['pairs']),5)
        self.assertEqual(sum(p[0]=='success_delay_100ms' for p in plan['pairs']),3)
        self.assertTrue(all(set(p)=={'success_delay_100ms','idle_only'} for p in plan['pairs']))
        self.assertEqual(plan['measurement_bursts']*plan['starts_per_burst']*10,2400)

    def assessment(self,latency=.25):
        events,summary,state,code=fixture(latency)
        rows,execution=w02_tests.W02CloudTests().telemetry();execution['returncode']=code
        return [events,summary,execution,{'valid':True,'requested_http_rate':600},state,rows,
                {'verified':True,'expected_fingerprint':'fixed','restored_fingerprint':'fixed'},
                [{'elapsed_s':i/100} for i in range(100)]]

    def test_late_correct_outcomes_remain_valid_with_cloud_guards(self):
        result=assess_cloud(*self.assessment(3))
        self.assertTrue(result['measurement_valid'])
        self.assertEqual(result['cohorts']['measurement']['deadline_misses'],20)
        self.assertEqual(result['cohorts']['measurement']['max_observed_outstanding'],20)

    def test_invalid_reset_headroom_and_exact_effect_are_rejected(self):
        for which in ('reset','calibration','effect','telemetry'):
            args=self.assessment()
            if which=='reset':args[6]['restored_fingerprint']='different'
            elif which=='calibration':args[3]['requested_http_rate']=199
            elif which=='effect':args[0][-1]['details']['backend']['events'][-1]['details']['amount_minor']=126
            else:args[5][1]['containers']['worker']['oom_killed']=True
            self.assertFalse(assess_cloud(*args)['measurement_valid'])


if __name__=='__main__':unittest.main()
