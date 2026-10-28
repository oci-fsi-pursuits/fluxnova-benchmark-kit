"""The mixed-load audit checks original SQLite records, not reported totals."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.audit_w02_mixed import audit_store
from services.workflow_checks import StudyStore

ROOT=Path(__file__).resolve().parents[1]


class MixedStoreAuditTests(unittest.TestCase):
    def fixture(self,folder):
        store=StudyStore(Path(folder)/'study.sqlite');events=[]
        cases=json.loads((ROOT/'workloads/w02/cases.json').read_text(encoding='utf-8'))['cases']
        for i,original in enumerate(cases):
            case=copy.deepcopy(original);case['dependency_profile']='fixture'
            for schedule in case['schedule'].values():schedule['delay_ms']=0
            key,pid=f'fixture:measurement:{i}',f'process-{i}'
            store.register(key,pid,case)
            for op in ('kyc','risk','settle'):
                request={'case_key':key,'process_id':pid,'operation':op}
                status,_=store.execute(request)
                if status==503 and op not in store.state(key)['receipts']:
                    self.assertEqual(store.execute(request)[0],200)
            events.append({'event':'completed','logical_request_id':key,'phase':'measurement',
                           'latency_s':1,'deadline_s':2,'details':{'original':case,
                           'process_instance_id':pid,'backend':store.state(key)}})
        return store,events

    def test_audit_reconciles_original_store_and_injected_faults(self):
        with tempfile.TemporaryDirectory() as folder:
            store,events=self.fixture(folder)
            result=audit_store(Path(store.path).resolve(),events)
            self.assertEqual((result['cases'],result['effects'],result['receipts'],result['injected_once_faults']),
                             (10,4,30,2))
            self.assertEqual(result['measurement_profiles']['fixture']['outcomes'],
                             {'settled':4,'rejected':4,'blocked':2})

    def test_changed_effect_or_missing_fault_cannot_pass(self):
        for target in ('effect','fault'):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as folder:
                store,events=self.fixture(folder)
                with store.connect() as db:
                    if target=='effect':db.execute('UPDATE effects SET amount_minor=amount_minor+1')
                    else:db.execute('DELETE FROM faults')
                with self.assertRaisesRegex(AssertionError,'original_store_effects_changed|injected_fault_inventory_mismatch'):
                    audit_store(Path(store.path).resolve(),events)


if __name__=='__main__':unittest.main()
