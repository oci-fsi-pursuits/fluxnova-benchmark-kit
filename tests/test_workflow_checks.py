import concurrent.futures
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.error

from services.workflow_checks import StudyStore, canonical, make_server
from services.worker import Client
from services.workflow_checks_worker import process_task

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / 'workloads/w02/cases.json').read_text())['cases']


class WorkflowCheckTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = StudyStore(Path(folder.name) / 'study.sqlite')
        self.case = copy.deepcopy(CASES[0])
        for s in self.case['schedule'].values():
            s['delay_ms'] = 0
        self.store.register('payment', 'process', self.case)

    def execute(self, operation, **values):
        return self.store.execute({'case_key':'payment', 'process_id':'process', 'operation':operation, **values})

    def test_settlement_requires_both_checks_and_exact_original_effect(self):
        self.assertEqual(self.execute('settle'), (409, {'error':'checks_incomplete'}))
        self.execute('kyc')
        self.assertEqual(self.execute('settle')[0], 409)
        self.execute('risk')
        self.assertEqual(self.execute('settle')[1]['outcome'], 'settled')
        self.assertEqual(self.store.state('payment')['effects'], [
            {'payment_id':'synthetic-payment-01','amount_minor':125,'beneficiary':'synthetic-beneficiary-01'}])

    def test_rebinding_and_process_replay_fail_without_effects(self):
        with self.assertRaises(ValueError):
            self.store.register('payment', 'other-process', self.case)
        changed = copy.deepcopy(self.case)
        changed['payment']['amount_minor'] = 126
        with self.assertRaises(ValueError):
            self.store.register('payment', 'process', changed)
        self.assertEqual(self.execute('kyc', process_id='other-process')[0], 409)
        self.assertEqual(self.store.state('payment')['receipts'], {})

    def test_worker_cannot_override_payment_or_pass_flags(self):
        self.assertEqual(self.execute('settle', authorized=True)[0], 400)
        self.assertEqual(self.execute('kyc', outcome='approved')[0], 400)
        self.assertEqual(self.store.state('payment')['effects'], [])

    def test_rejected_review_and_unknown_receipts_cannot_settle(self):
        for source in CASES[1:7]:
            case = copy.deepcopy(source)
            for s in case['schedule'].values():
                s['delay_ms'] = 0
            key = case['case_id']
            self.store.register(key, key, case)
            for op in ('risk','kyc','settle'):
                status, result = self.store.execute({'case_key':key,'process_id':key,'operation':op})
                self.assertEqual(status, 200)
            self.assertEqual(result['outcome'], case['expected_outcome'])
            self.assertEqual(self.store.state(key)['effects'], [])

    def test_concurrent_branches_preserve_both_receipts_then_duplicate_settlement_once(self):
        gate = threading.Barrier(2)
        def check(op):
            gate.wait(timeout=5)
            return self.execute(op)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(check, ['kyc','risk']))
        self.assertEqual({r[1]['outcome'] for r in results}, {'approved','accepted'})
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            settled = list(pool.map(lambda _: self.execute('settle'), range(16)))
        self.assertTrue(all(r[0] == 200 for r in settled))
        self.assertEqual(sum(not r[1]['replayed'] for r in settled), 1)
        self.assertEqual(len(self.store.state('payment')['effects']), 1)

    def test_cross_case_receipt_is_rejected(self):
        self.execute('kyc')
        self.execute('risk')
        with self.store.connect() as db:
            receipt = json.loads(db.execute("SELECT body FROM receipts WHERE key='payment' AND operation='risk'").fetchone()[0])
            receipt['input_sha256'] = 'wrong-case'
            db.execute("UPDATE receipts SET body=? WHERE key='payment' AND operation='risk'", (canonical(receipt),))
        self.assertEqual(self.execute('settle'), (409, {'error':'receipt_binding_mismatch'}))
        self.assertEqual(self.store.state('payment')['effects'], [])

    def test_fault_receipts_survive_reopen(self):
        for source in CASES[7:9]:
            case = copy.deepcopy(source)
            for s in case['schedule'].values():
                s['delay_ms'] = 0
            key = case['case_id']
            self.store.register(key, key, case)
            op = next(op for op,s in case['schedule'].items() if s['fault'] != 'none')
            request = {'case_key':key, 'process_id':key, 'operation':op}
            self.assertEqual(self.store.execute(request)[0], 503)
            restarted = StudyStore(self.store.path)
            status, result = restarted.execute(request)
            self.assertEqual(status, 200)
            self.assertEqual(result['replayed'], case['schedule'][op]['fault'] == 'after_once')

    def test_http_separates_registration_from_worker_privilege(self):
        server = make_server('127.0.0.1', 0, self.store, 'worker', 'registrar')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = 'http://127.0.0.1:' + str(server.server_port)
        worker = Client(base, 'Bearer worker')
        with self.assertRaises(urllib.error.HTTPError) as error:
            worker.request('POST', '/cases/register', {'case_key':'new','process_id':'new','case':self.case})
        self.assertEqual(error.exception.code, 401)
        self.assertEqual(worker.request('POST','/tools/execute',{'case_key':'payment','process_id':'process','operation':'kyc'})['outcome'],'approved')

    def test_invalid_case_types_fail_before_registration(self):
        for field, value in [('authorized','true'), ('amount_minor',True), ('amount_minor',0)]:
            case = copy.deepcopy(self.case)
            case['payment'][field] = value
            with self.assertRaises(ValueError):
                self.store.register('bad','bad',case)

    def test_worker_reconciles_lost_response_and_writes_only_its_branch(self):
        source = copy.deepcopy(CASES[8])
        source['schedule']['risk']['delay_ms'] = 0
        self.store.register('lost', 'pid', source)
        store = self.store
        class Support:
            def request(self, method, path, body=None):
                if method == 'GET':
                    return store.state('lost')
                status, value = store.execute(body)
                if status != 200:
                    raise urllib.error.HTTPError('http://fixture',status,'failure',{},None)
                return value
        class Engine:
            def __init__(self): self.calls = []
            def request(self, method, path, body): self.calls.append((method,path,body))
        engine = Engine()
        task = {'id':'task', 'activityId':'risk_check','processInstanceId':'pid',
                'variables':{'studyCaseKey':{'value':'lost'}}}
        result = process_task(task, engine, Support(), 'worker')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(engine.calls[0][2]['variables'], {'w02Risk':{'value':'accepted','type':'String'}})
        self.assertEqual(self.store.state('lost')['effects'], [])


if __name__ == '__main__':
    unittest.main()
