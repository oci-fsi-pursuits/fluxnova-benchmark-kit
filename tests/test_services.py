import concurrent.futures
import json
import sys
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import Mock, patch

from services.mock_service import Ledger, make_server, mock_model
from services.worker import Client, process_task
from services import worker


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ledger = Ledger(Path(self.directory.name) / 'ledger.sqlite')

    def request(self, key='case', **kw):
        value = {'idempotency_key': key, 'operation': 'settle', 'payload': {'authorized': True, 'amount_minor': 125, 'beneficiary': 'synthetic'}}
        value.update(kw)
        return value

    def test_concurrent_duplicate_has_one_effect(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: self.ledger.execute(self.request()), range(24)))
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertEqual(self.ledger.state('case')['effect_count'], 1)
        self.assertEqual(sum(not result['replayed'] for _, result in results), 1)

    def test_conflicting_payload_is_rejected_without_second_effect(self):
        self.ledger.execute(self.request())
        changed = self.request(payload={'authorized': True, 'amount_minor': 126})
        self.assertEqual(self.ledger.execute(changed)[0], 409)
        self.assertEqual(self.ledger.state('case')['effect_count'], 1)

    def test_post_effect_failure_is_reconciled_after_restart(self):
        self.assertEqual(self.ledger.execute(self.request(fault='after_effect_once'))[0], 503)
        restarted = Ledger(self.ledger.path)
        self.assertEqual(restarted.state('case')['effect_count'], 1)
        self.assertTrue(restarted.execute(self.request(fault='after_effect_once'))[1]['replayed'])

    def test_pre_effect_failure_retries_once(self):
        request = self.request(fault='before_effect_once')
        self.assertEqual(self.ledger.execute(request)[0], 503)
        self.assertEqual(self.ledger.state('case')['effect_count'], 0)
        self.assertEqual(self.ledger.execute(request)[0], 200)
        self.assertEqual(self.ledger.state('case')['effect_count'], 1)

    def test_policy_is_enforced_and_recorded_before_effect(self):
        for payload in ({'authorized': False}, {'authorized': True, 'sanctions_match': True}, {'authorized': True, 'amount_minor': 2000, 'approved': False}):
            key = json.dumps(payload, sort_keys=True)
            status, result = self.ledger.execute(self.request(key, payload=payload))
            self.assertEqual((status, result['outcome']), (200, 'rejected'))
            state = self.ledger.state(key)
            self.assertEqual(state['effect_count'], 0)
            self.assertEqual(state['events'][0]['event'], 'policy_decision')

    def test_model_stub_modes(self):
        self.assertFalse(mock_model({'turn': 0})[1]['final'])
        self.assertTrue(mock_model({'turn': 1})[1]['final'])
        self.assertEqual(mock_model({'mode': 'throttle'})[0], 429)
        self.assertEqual(mock_model({'mode': 'invalid_tool'})[1]['tool_calls'][0]['name'], 'forbidden_tool')

    def test_http_auth_and_state(self):
        server = make_server('127.0.0.1', 0, self.ledger, 'test-token')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = 'http://127.0.0.1:' + str(server.server_port)
        with self.assertRaises(urllib.error.HTTPError):
            Client(base).request('POST', '/tools/execute', self.request())
        client = Client(base, 'Bearer test-token')
        self.assertEqual(client.request('POST', '/tools/execute', self.request())['outcome'], 'settled')
        self.assertEqual(client.request('GET', '/state/case')['effect_count'], 1)

    def test_worker_ambiguous_result_reconciles_not_reexecutes(self):
        ledger = self.ledger
        class Engine:
            calls = []
            def request(self, method, path, body=None):
                self.calls.append((method, path, body))
        class Support:
            def request(self, method, path, body=None):
                if method == 'GET':
                    return ledger.state('worker-case')
                status, result = ledger.execute(body)
                if status != 200:
                    raise urllib.error.HTTPError('http://test', status, 'injected', {}, None)
                return result
        task = {'id': 'task-1', 'variables': {'scenario': {'value': 'settlement'}, 'logical_request_id': {'value': 'worker-case'}, 'fault': {'value': 'after_effect_once'}}}
        engine = Engine()
        with patch('builtins.print') as output:
            self.assertTrue(process_task(task, engine, Support(), 'worker', timing=True))
        timing = json.loads(output.call_args.args[0])
        self.assertEqual([x['stage'] for x in timing['stages']], ['tool_execute', 'tool_reconcile', 'engine_complete'])
        self.assertEqual(timing['stages'][0]['error_type'], 'HTTPError')
        self.assertTrue(timing['completed'])
        self.assertEqual(ledger.state('worker-case')['effect_count'], 1)
        self.assertTrue(engine.calls[-1][1].endswith('/complete'))
        self.assertEqual(engine.calls[-1][2]['variables']['workerResult']['value'], 'settled')

    def test_worker_conflict_does_not_accept_old_state(self):
        class Engine:
            calls = []
            def request(self, method, path, body=None):
                self.calls.append((path, body))
        class Support:
            def request(self, method, path, body=None):
                if method == 'GET':
                    raise AssertionError('409 must not reconcile unrelated old payload')
                raise urllib.error.HTTPError('http://test', 409, 'conflict', {}, None)
        task = {'id': 'task-1', 'variables': {'logical_request_id': {'value': 'worker-case'}}}
        engine = Engine()
        self.assertFalse(process_task(task, engine, Support(), 'worker'))
        self.assertTrue(engine.calls[-1][0].endswith('/failure'))
        self.assertEqual(engine.calls[-1][1]['retries'], 2)


class WorkerPollingTests(unittest.TestCase):
    def run_until_backoff(self, responses, processed=True, args=()):
        calls = []
        engine = Mock()
        replies = iter(responses)

        def fetch(*args):
            calls.append('fetch')
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        def process(*args):
            calls.append('process')
            return processed

        def sleep(delay):
            calls.append(('sleep', delay))
            raise StopIteration('end test after backoff')

        engine.request.side_effect = fetch
        with patch.object(sys, 'argv', ['worker', *args]), \
                patch.object(worker, 'Client', side_effect=[engine, Mock()]), \
                patch.object(worker, 'process_task', side_effect=process), \
                patch.object(worker.signal, 'signal'), \
                patch.object(worker.time, 'sleep', side_effect=sleep):
            with self.assertRaises(StopIteration):
                worker.main()
        for invocation in engine.request.call_args_list:
            self.assertEqual(invocation.args[2]['maxTasks'], 1)
        return calls

    def test_successful_tasks_continue_without_per_task_delay(self):
        self.assertEqual(self.run_until_backoff([[{'id':'one'}], [{'id':'two'}], []]),
                         ['fetch','process','fetch','process','fetch',('sleep',0.1)])

    def test_empty_poll_backs_off(self):
        self.assertEqual(self.run_until_backoff([[]]), ['fetch',('sleep',0.1)])

    def test_task_failure_backs_off(self):
        self.assertEqual(self.run_until_backoff([[{'id':'one'}]], processed=False),
                         ['fetch','process',('sleep',0.1)])

    def test_transport_failure_backs_off(self):
        self.assertEqual(self.run_until_backoff([urllib.error.URLError('unavailable')]),
                         ['fetch',('sleep',0.1)])

    def test_research_control_delays_success_without_changing_task_processing(self):
        self.assertEqual(self.run_until_backoff([[{'id':'one'}]], args=('--success-delay-s','0.1')),
                         ['fetch','process',('sleep',0.1)])

    def test_research_control_preserves_independent_error_backoff(self):
        self.assertEqual(self.run_until_backoff([[{'id':'one'}]], processed=False,
                         args=('--success-delay-s','0.1','--poll-s','0.25')),
                         ['fetch','process',('sleep',0.25)])

    def test_nonfinite_or_unbounded_success_delay_rejected_before_clients(self):
        for value in ('nan','inf','-0.1','1.01'):
            with self.subTest(value=value), patch.object(sys,'argv',['worker','--success-delay-s',value]), \
                    patch.object(worker,'Client') as client, patch('sys.stderr'):
                with self.assertRaises(SystemExit):
                    worker.main()
                client.assert_not_called()


class WorkerTimingTests(unittest.TestCase):
    def setUp(self):
        self.task = {'id': 'task-1', 'processInstanceId': 'process-1', 'variables': {
            'logical_request_id': {'value': 'private-business-key'}, 'payload': {'value': 'sensitive-payload'}}}
        self.engine = Mock()
        self.support = Mock()
        self.support.request.return_value = {'outcome': 'ok'}

    def test_stage_durations_and_identity_without_payload(self):
        with patch.object(worker.time, 'monotonic_ns', side_effect=[100, 10_000_100, 20_000_000, 45_000_000]), patch('builtins.print') as output:
            self.assertTrue(process_task(self.task, self.engine, self.support, 'worker', timing=True))
        raw = output.call_args.args[0]
        value = json.loads(raw)
        self.assertEqual([x['duration_s'] for x in value['stages']], [0.01, 0.025])
        self.assertEqual([x['stage'] for x in value['stages']], ['tool_execute', 'engine_complete'])
        self.assertEqual(value['process_instance_id'], 'process-1')
        self.assertNotIn('sensitive-payload', raw)
        self.assertNotIn('private-business-key', raw)

    def test_disabled_does_not_read_clock_or_emit_logs(self):
        with patch.object(worker.time, 'monotonic_ns') as clock, patch('builtins.print') as output:
            self.assertTrue(process_task(self.task, self.engine, self.support, 'worker'))
        clock.assert_not_called()
        output.assert_not_called()

    def test_failed_diagnostic_output_does_not_retry_completed_task(self):
        with patch('builtins.print', side_effect=OSError('closed diagnostic stream')):
            self.assertTrue(process_task(self.task, self.engine, self.support, 'worker', timing=True))
        self.assertEqual(self.engine.request.call_count, 1)
        self.assertTrue(self.engine.request.call_args.args[1].endswith('/complete'))

    def test_failed_completion_records_failure_stage_without_exception_text(self):
        self.engine.request.side_effect = [TimeoutError('credential-in-exception'), None]
        with patch('builtins.print') as output:
            self.assertFalse(process_task(self.task, self.engine, self.support, 'worker', timing=True))
        value = json.loads(output.call_args.args[0])
        self.assertFalse(value['completed'])
        self.assertEqual([x['stage'] for x in value['stages']], ['tool_execute', 'engine_complete', 'engine_failure'])
        self.assertEqual(value['stages'][1]['error_type'], 'TimeoutError')
        self.assertNotIn('credential-in-exception', str(output.call_args_list))

    def test_poll_timing_records_transport_failure(self):
        self.engine.request.side_effect = TimeoutError('private-url')
        with patch.object(sys, 'argv', ['worker', '--timing', '--once']), \
                patch.object(worker, 'Client', side_effect=[self.engine, self.support]), \
                patch.object(worker.signal, 'signal'), patch('builtins.print') as output:
            worker.main()
        record = json.loads(output.call_args_list[0].args[0])
        self.assertEqual(record['event'], 'worker_poll_timing')
        self.assertEqual(record['stages'][0]['error_type'], 'TimeoutError')
        self.assertNotIn('private-url', str(output.call_args_list))


if __name__ == '__main__':
    unittest.main()
