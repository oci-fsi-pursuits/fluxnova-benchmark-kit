"""Synthetic receipt-oracle invariants; these tests do not measure an engine."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid

from services.cluster_probe import ProbeStore, make_server
from services.workflow_checks import fingerprint
from benchlib.fnv07_functional import ProbeClient


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'receipts.sqlite'
        self.store = ProbeStore(self.path, 'run-fixture', ['n1', 'n2'])
        for node in ('n1', 'n2'):
            self.store.node(node, {'runtime': 'fixture'})

    def tearDown(self):
        self.folder.cleanup()

    def case(self, mode='normal'):
        case = {'id': 'case-fixture', 'mode': mode}
        self.store.register('case-fixture', 'process-fixture', 'business-fixture', case)
        return case

    def request(self, case, stage='stage1', node='n1'):
        return {'run_id': 'run-fixture', 'case_key': 'case-fixture', 'process_id': 'process-fixture',
                'business_key': 'business-fixture', 'input_sha256': fingerprint(case), 'stage': stage,
                'node_id': node, 'invocation_id': uuid.uuid4().hex}

    def test_concurrent_replays_commit_one_effect_and_retain_every_attempt(self):
        case = self.case()
        requests = [self.request(case, node='n1' if i % 2 else 'n2') for i in range(12)]
        with ThreadPoolExecutor(max_workers=12) as pool:
            responses = list(pool.map(self.store.execute, requests))
        self.assertTrue(all(code == 200 for code, _ in responses))
        self.assertEqual(sum(body['disposition'] == 'committed' for _, body in responses), 1)
        self.assertEqual(len({body['receipt_id'] for _, body in responses}), 1)
        snapshot = self.store.snapshot()
        self.assertEqual(len(snapshot['effects']), 1)
        self.assertEqual(len(snapshot['attempts']), 12)

    def test_after_effect_failure_is_durable_and_retry_returns_same_receipt(self):
        case = self.case('fail_after_once')
        self.store.execute(self.request(case))
        code, first = self.store.execute(self.request(case, 'stage2'))
        self.assertEqual(code, 503)
        reopened = ProbeStore(self.path, 'run-fixture', ['n1', 'n2'])
        code, second = reopened.execute(self.request(case, 'stage2', 'n2'))
        self.assertEqual((code, second['disposition']), (200, 'cached'))
        self.assertEqual(first['receipt_id'], second['receipt_id'])
        self.assertEqual(len(reopened.snapshot()['effects']), 2)

    def test_before_effect_failure_does_not_commit_and_retry_can_commit(self):
        case = self.case('fail_before_once')
        self.store.execute(self.request(case))
        self.assertEqual(self.store.execute(self.request(case, 'stage2'))[0], 503)
        self.assertEqual(len(self.store.snapshot()['effects']), 1)
        self.assertEqual(self.store.execute(self.request(case, 'stage2'))[1]['disposition'], 'committed')

    def test_exhausted_after_effect_is_not_successful_acknowledgment(self):
        case = self.case('fail_after_always')
        self.store.execute(self.request(case))
        responses = [self.store.execute(self.request(case, 'stage2')) for _ in range(3)]
        self.assertEqual([r[0] for r in responses], [503, 503, 503])
        self.assertEqual(len(self.store.snapshot()['effects']), 2)
        self.assertEqual(len({r[1]['receipt_id'] for r in responses}), 1)

    def test_exhausted_before_effect_leaves_only_prior_stage(self):
        case = self.case('fail_before_always')
        self.store.execute(self.request(case))
        for _ in range(3):
            self.assertEqual(self.store.execute(self.request(case, 'stage2'))[0], 503)
        self.assertEqual([e['stage'] for e in self.store.snapshot()['effects']], ['stage1'])

    def test_original_input_process_business_key_run_and_node_are_bound(self):
        case = self.case()
        for field in ('input_sha256', 'process_id', 'business_key', 'run_id', 'node_id'):
            request = self.request(case)
            request[field] = 'another'
            with self.subTest(field=field):
                self.assertEqual(self.store.execute(request)[0], 403)
        self.assertEqual(self.store.snapshot()['effects'], [])
        self.assertEqual(len(self.store.snapshot()['attempts']), 5)

    def test_invocation_identity_cannot_move_to_another_stage(self):
        case = self.case()
        request = self.request(case)
        self.assertEqual(self.store.execute(request)[0], 200)
        self.assertEqual(self.store.execute({**request, 'stage': 'stage2'})[0], 403)
        self.assertEqual(len(self.store.snapshot()['effects']), 1)

    def test_stage_order_and_registration_are_immutable(self):
        case = self.case()
        self.assertEqual(self.store.execute(self.request(case, 'stage3'))[0], 403)
        with self.assertRaises(ValueError):
            self.store.register('case-fixture', 'process-fixture', 'business-fixture', {**case, 'mode': 'fail_after_always'})
        with self.assertRaises(ValueError):
            ProbeStore(self.path, 'different-run', ['n1', 'n2'])
        with self.assertRaises(ValueError):
            self.store.node('n1', {'runtime': 'changed'})

    def test_unregistered_node_is_rejected(self):
        other = ProbeStore(Path(self.folder.name) / 'other.sqlite', 'run-fixture', ['n1'])
        case = {'id': 'case-fixture', 'mode': 'normal'}
        other.register('case-fixture', 'process-fixture', 'business-fixture', case)
        self.assertEqual(other.execute(self.request(case))[0], 403)

    def test_case_observation_is_durable_scoped_and_has_no_growing_attempt_ledger(self):
        case = self.case()
        for stage in ('stage1', 'stage2', 'stage3'):
            self.store.execute(self.request(case, stage))
        other = {'id': 'other', 'mode': 'normal'}
        self.store.register('other', 'other-process', 'other-business', other)
        reopened = ProbeStore(self.path, 'run-fixture', ['n1', 'n2'])
        observation = reopened.observe('case-fixture')
        self.assertEqual(set(observation), {'case', 'effects'})
        self.assertEqual(observation['case']['input_sha256'], fingerprint(case))
        self.assertEqual([e['stage'] for e in observation['effects']], ['stage1', 'stage2', 'stage3'])
        self.assertEqual(reopened.observe('missing'), {'case': None, 'effects': []})
        with self.assertRaises(ValueError):
            reopened.observe("'; SELECT * FROM cases")

    def test_execution_token_cannot_register_new_authority(self):
        server = make_server(self.store, admin_token='admin-fixture', execute_token='execute-fixture')
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/register', data=b'{}',
                                             headers={'X-Fnv07-Token': 'execute-fixture'}, method='POST')
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(error.exception.code, 403)
            self.assertEqual(self.store.snapshot()['cases'], [])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


    def test_administrative_snapshot_requires_admin_token(self):
        server = make_server(self.store, admin_token='admin-fixture', execute_token='execute-fixture')
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            base = 'http://127.0.0.1:' + str(server.server_port)
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                ProbeClient(base, 'execute-fixture').snapshot()
            self.assertEqual(rejected.exception.code, 403)
            self.assertEqual(ProbeClient(base, 'admin-fixture').snapshot(), self.store.snapshot())
            case = self.case()
            for stage in ('stage1', 'stage2', 'stage3'):
                self.store.execute(self.request(case, stage))
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                ProbeClient(base, 'execute-fixture').call('/observe', {'case_key': 'case-fixture'})
            self.assertEqual(rejected.exception.code, 403)
            self.assertEqual(ProbeClient(base, 'admin-fixture').call('/observe', {'case_key': 'case-fixture'}), self.store.observe('case-fixture'))
            with self.assertRaises(urllib.error.HTTPError) as malformed:
                ProbeClient(base, 'admin-fixture').call('/observe', {'case_key': 'case-fixture', 'all': True})
            self.assertEqual(malformed.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
