import copy
import unittest
from benchlib.w02_cloud import measurement_plan
from benchlib.w02_results import summarize_pairs


class W02ResultsTests(unittest.TestCase):
    def data(self):
        plan=measurement_plan('a'*32,1);plan['source_commit']='test'
        records=[]
        for i,pair in enumerate(plan['pairs'],1):
            for j,arm in enumerate(pair,1):
                p95=1.5 if arm=='sequential' else 1.
                measurement={'offered':600,'correct':600,'on_time_correct':600,'dropped':0,'invalid_or_unknown':[],
                             'observed_latency_s':{'p95':p95,'p99':p95+.1}}
                records.append({'folder':f'p{i}-{j}-{arm}','assessment':{'arm':arm,'offered_rate':1,'measurement_valid':True,'issues':[],
                     'cohorts':{'measurement':measurement,'warmup':{'correct':120,'offered':120}}}})
        return plan,records

    def test_pair_statistic_keeps_warmup_out_of_denominator(self):
        plan,records=self.data();value=summarize_pairs(plan,records)
        self.assertEqual(value['status'],'complete')
        self.assertAlmostEqual(value['comparison']['median_paired_p95_change_percent'],-100/3)
        self.assertEqual(value['comparison']['arms']['parallel']['offered'],3000)
        self.assertEqual(value['comparison']['arms']['parallel']['deadline_goodput_per_s'],1)

    def test_missing_invalid_or_duplicate_arm_cannot_support_claim(self):
        plan,records=self.data()
        self.assertIsNone(summarize_pairs(plan,records[:-1])['comparison'])
        altered=copy.deepcopy(records);altered[0]['assessment']['measurement_valid']=False
        self.assertIsNone(summarize_pairs(plan,altered)['comparison'])
        with self.assertRaises(ValueError):summarize_pairs(plan,records+[records[0]])

    def test_deadline_regression_is_reported_without_excluding_arm(self):
        plan,records=self.data()
        for record in records:
            a=record['assessment']
            if a['arm']=='parallel':
                a['issues']=['measurement_deadline_miss'];m=a['cohorts']['measurement'];m['on_time_correct']=300
                m['observed_latency_s']={'p95':2.5,'p99':2.6}
        value=summarize_pairs(plan,records)
        self.assertEqual(value['status'],'complete')
        self.assertAlmostEqual(value['comparison']['median_paired_p95_change_percent'],200/3)
        self.assertEqual(value['comparison']['arms']['parallel']['deadline_goodput_per_s'],.5)


if __name__=='__main__':unittest.main()
