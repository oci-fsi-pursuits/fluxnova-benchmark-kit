import copy
import json
from pathlib import Path
import unittest
import tempfile
from types import SimpleNamespace
from benchlib.workflow_corpus import resolve_sensitivity,resolve_mixed_load,mixed_case

ROOT=Path(__file__).resolve().parents[1]


class WorkflowCorpusTests(unittest.TestCase):
    def setUp(self):
        self.base=json.loads((ROOT/'workloads/w02/cases.json').read_text())
        self.protocol=json.loads((ROOT/'workloads/w02/sensitivity-protocol.json').read_text())

    def test_complete_grid_preserves_policy_and_faults_without_mutating_base(self):
        before=copy.deepcopy(self.base)
        result=resolve_sensitivity(self.protocol,self.base)
        self.assertEqual(self.base,before)
        self.assertEqual(len(result['cases']),50)
        self.assertEqual(len({c['payment']['payment_id'] for c in result['cases']}),50)
        originals={c['case_id']:c for c in self.base['cases']}
        for case in result['cases']:
            original=originals[case['source_case_id']]
            self.assertEqual(case['expected_outcome'],original['expected_outcome'])
            self.assertEqual(case['expected_checks'],original['expected_checks'])
            self.assertEqual({k:v for k,v in case['payment'].items() if k!='payment_id'},
                             {k:v for k,v in original['payment'].items() if k!='payment_id'})
            for op in ('kyc','risk'):
                self.assertEqual(case['schedule'][op]['fault'],original['schedule'][op]['fault'])
        self.assertEqual(sum(c['expected_outcome']=='settled' for c in result['cases']),20)

    def test_seed_reproduces_order_and_balanced_arm_plan(self):
        a=resolve_sensitivity(self.protocol,self.base)
        self.assertEqual(a,resolve_sensitivity(self.protocol,self.base))
        self.assertEqual(sum(v[0]=='parallel' for v in a['arm_order_by_case'].values()),25)
        self.protocol['seed']+=1
        b=resolve_sensitivity(self.protocol,self.base)
        self.assertNotEqual([c['case_id'] for c in a['cases']],[c['case_id'] for c in b['cases']])
        self.assertEqual({c['case_id'] for c in a['cases']},{c['case_id'] for c in b['cases']})

    def test_unsafe_or_duplicate_ids_and_delays_fail_before_execution(self):
        for field,value in [('id','../outside'),('kyc_ms',True),('risk_ms',5001),('kyc_ms',-1)]:
            p=copy.deepcopy(self.protocol);p['profiles'][0][field]=value
            with self.assertRaises(ValueError):resolve_sensitivity(p,self.base)
        self.protocol['profiles'].append(copy.deepcopy(self.protocol['profiles'][0]))
        with self.assertRaises(ValueError):resolve_sensitivity(self.protocol,self.base)

    def test_missing_or_changed_case_inventory_fails(self):
        self.base['cases'].pop()
        with self.assertRaises(ValueError):resolve_sensitivity(self.protocol,self.base)

    def test_empty_requested_protocol_fails_before_creating_output_or_starting_engine(self):
        from scripts.run_w02_functional import run
        with tempfile.TemporaryDirectory() as folder:
            empty=Path(folder)/'empty.json';empty.write_bytes(b'')
            output=Path(folder)/'run'
            # No runtime arguments are needed: this must fail before runtime access.
            with self.assertRaises(json.JSONDecodeError):
                run(SimpleNamespace(output=output,sensitivity_protocol=empty))
            self.assertFalse(output.exists())

    def test_overlap_is_only_required_for_declared_slow_probes(self):
        cases=resolve_sensitivity(self.protocol,self.base)['cases']
        required=[c for c in cases if c['require_parallel_overlap']]
        self.assertEqual(len(required),4)
        self.assertTrue(all(c['source_case_id'] in ('approve','concurrent_completion') and
                            min(s['delay_ms'] for s in c['schedule'].values())>=400 for c in required))

    def test_mixed_plan_covers_whole_case_cycles_at_both_rates(self):
        protocol=json.loads((ROOT/'workloads/w02/mixed-load-protocol.json').read_text(encoding='utf-8'))
        plan=resolve_mixed_load(protocol,self.protocol,self.base)
        self.assertEqual(sum(plan['duration_s']*lane['rate'] for lane in plan['lanes']),300)
        for lane in plan['lanes']:
            arrivals=[mixed_case(i,'measurement',plan['corpus']) for i in range(int(plan['duration_s']*lane['rate']))]
            self.assertEqual(sum(c['expected_outcome']=='settled' for c in arrivals),len(arrivals)*.4)
            self.assertEqual(sum(c['expected_outcome']=='blocked' for c in arrivals),len(arrivals)*.2)
        before=copy.deepcopy(plan['corpus'])
        a=mixed_case(0,'warmup',plan['corpus']);b=mixed_case(50,'measurement',plan['corpus'])
        self.assertEqual(a['template_case_id'],b['template_case_id'])
        self.assertNotEqual(a['payment']['payment_id'],b['payment']['payment_id'])
        self.assertEqual(plan['corpus'],before)

    def test_mixed_partial_cycle_or_changed_controls_are_rejected(self):
        protocol=json.loads((ROOT/'workloads/w02/mixed-load-protocol.json').read_text(encoding='utf-8'))
        for field,value in [('duration_s',60),('worker_slots',4),('deadline_miss_action','discard')]:
            changed=copy.deepcopy(protocol);changed[field]=value
            with self.assertRaises(ValueError):resolve_mixed_load(changed,self.protocol,self.base)
        protocol['lanes'][0]=copy.deepcopy(protocol['lanes'][1])
        with self.assertRaises(ValueError):resolve_mixed_load(protocol,self.protocol,self.base)


if __name__=='__main__':unittest.main()
