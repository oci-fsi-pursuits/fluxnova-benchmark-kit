import copy
import json
from pathlib import Path
import unittest

from benchlib.w02_mixed_cloud import resolve_cloud_corpus,mixed_measurement_plan,summarize_mixed_pairs
from benchlib.analysis import distribution
from benchlib.w02_cloud import assess_cloud
from benchlib.workflow_corpus import mixed_case
from services.workflow_checks import fingerprint
import test_w02_cloud as cloud_fixtures
import test_w02_load as load_fixtures

ROOT=Path(__file__).resolve().parents[1]


class MixedCloudTests(unittest.TestCase):
    def plan(self):
        read=lambda name:json.loads((ROOT/'workloads/w02'/name).read_text(encoding='utf-8'))
        corpus=resolve_cloud_corpus(read('sensitivity-protocol.json'),read('cases.json'))
        plan=mixed_measurement_plan('a'*32,1,corpus);plan['source_commit']='fixture'
        return plan,corpus

    def records(self,plan):
        rows=[]
        for i,pair in enumerate(plan['pairs'],1):
            for j,arm in enumerate(pair,1):
                outcomes={k:{'expected':n,'verified':n,'within_deadline':n,
                    'observed_latency_s':distribution([1.0]*n)} for k,n in [('settled',240),('rejected',240),('blocked',120)]}
                # Equal total on-time outcomes, different settlement performance.
                outcomes['settled']['within_deadline']=180 if arm=='sequential' else 210
                outcomes['rejected']['within_deadline']=240 if arm=='sequential' else 210
                m={'offered':600,'correct':600,'on_time_correct':540,'dropped':0,'invalid_or_unknown':[],
                   'outcomes':outcomes,'on_time_settlements_per_s':999}
                rows.append({'folder':f'p{i}-{j}-{arm}','assessment':{'arm':arm,'offered_rate':1,'case_mode':'mixed_sensitivity',
                    'measurement_valid':True,'issues':[],'cohorts':{'measurement':m,'warmup':{'offered':100,'correct':100}}}})
        return rows

    def test_frozen_design_covers_whole_cycles_and_balanced_pairs(self):
        plan,corpus=self.plan()
        self.assertEqual(plan['warmup_s'],100);self.assertEqual(plan['measurement_s'],600)
        self.assertEqual(plan['outcomes_per_cycle'],dict(settled=20,rejected=20,blocked=10))
        self.assertEqual(sum(p[0]=='sequential' for p in plan['pairs']),3)
        for rate in (.5,1):
            current=mixed_measurement_plan('a'*32,rate,corpus)
            for seconds in (current['warmup_s'],current['measurement_s'],current['qualification_measurement_s']):
                self.assertEqual((seconds*rate)%current['corpus_size'],0)
        plan['qualification_lanes'].clear()
        self.assertEqual(len(self.plan()[0]['qualification_lanes']),4)
        with self.assertRaises(ValueError):mixed_measurement_plan('a'*32,True,corpus)
        bad=copy.deepcopy(corpus);bad['cases'].pop()
        with self.assertRaises(ValueError):mixed_measurement_plan('a'*32,1,bad)

    def test_primary_metric_counts_settlements_only_and_reports_regression(self):
        plan,_=self.plan();rows=self.records(plan);result=summarize_mixed_pairs(plan,rows)
        self.assertEqual(result['status'],'complete')
        self.assertAlmostEqual(result['comparison']['median_paired_on_time_settlement_change_per_s'],.05)
        self.assertAlmostEqual(result['comparison']['arms']['parallel']['on_time_settlements_per_s'],.35)
        self.assertEqual(result['comparison']['paired_runs'][0]['outcomes']['rejected']['parallel_on_time_fraction_change_percentage_points'],-12.5)
        self.assertIsNone(summarize_mixed_pairs(plan,rows[:-1])['comparison'])
        rows[0]['assessment']['measurement_valid']=False
        self.assertIsNone(summarize_mixed_pairs(plan,rows)['comparison'])

    def test_changed_denominators_duplicate_arms_or_false_validity_fail(self):
        plan,_=self.plan()
        for mutation in ('count','duplicate','false_valid'):
            rows=self.records(plan)
            if mutation=='count':rows[0]['assessment']['cohorts']['measurement']['outcomes']['blocked']['expected']=121
            elif mutation=='duplicate':rows.append(copy.deepcopy(rows[0]))
            else:rows[0]['assessment']['cohorts']['measurement']['dropped']=1
            with self.assertRaises(ValueError):summarize_mixed_pairs(plan,rows)

    def test_cloud_oracle_keeps_mixed_deadline_misses_without_weakening_legacy_gate(self):
        events,summary,final=load_fixtures.W02AssessmentTests().healthy()
        for event in events:
            if event['event']=='completed':event['latency_s']+=3;event['elapsed_s']+=3
        telemetry,execution=cloud_fixtures.W02CloudTests().telemetry();execution['returncode']=0
        reset={'verified':True,'expected_fingerprint':'fixture','restored_fingerprint':'fixture'}
        calibration={'valid':True,'requested_http_rate':50}
        legacy=assess_cloud(events,summary,execution,calibration,final,telemetry,reset)
        self.assertIn('measurement_deadline_miss',legacy['issues'])
        corpus={'cases':[load_fixtures.expected_case(0,'measurement')]}
        summary['w02'].update(case_mode='mixed_sensitivity',corpus_fingerprint=fingerprint(corpus),corpus_size=1)
        for event in events:
            if event['event']!='completed':continue
            index=int(event['logical_request_id'].rsplit(':',1)[1]);case=mixed_case(index,'measurement',corpus)
            data=event['details'];data['original']=case;data['backend']['input_sha256']=fingerprint(case)
            for receipt in data['backend']['receipts'].values():receipt['input_sha256']=fingerprint(case)
            data['backend']['effects'][0]['payment_id']=case['payment']['payment_id']
        result=assess_cloud(events,summary,execution,calibration,final,telemetry,reset,corpus=corpus)
        self.assertEqual(result['status'],'passed',result['issues'])
        self.assertEqual(result['cohorts']['measurement']['on_time_correct'],0)
        bad=copy.deepcopy(events)
        next(e for e in bad if e['event']=='completed')['details']['backend']['effects'][0]['amount_minor']+=1
        self.assertFalse(assess_cloud(bad,summary,execution,calibration,final,telemetry,reset,corpus=corpus)['measurement_valid'])


if __name__=='__main__':unittest.main()
