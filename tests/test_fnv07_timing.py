"""Original-clock/denominator falsification plus a real local HTTP driver test."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid

from benchlib.fluxnova import FluxnovaClient
from benchlib.fnv07_functional import ProbeClient
from benchlib.fnv07_load import run_arrivals, validate
from benchlib.fnv07_timing_audit import canonical, check_trace, digest
from services.cluster_probe import ProbeStore, make_server

ROOT = Path(__file__).resolve().parents[1]


def protocol():
    value = json.loads((ROOT / 'workloads/fnv07/load-qualification.json').read_text())
    return dict(value, rate_per_s=1, arrival_seconds=1)


def fixture():
    spec = protocol()
    run, key, pid = 'fixture', 'fixture-arrival-0000', 'original-process'
    input_hash = digest(canonical(spec['case']).encode())
    values = dict(completedStages=3, fnv07RunId=run, fnv07CaseKey=key, fnv07InputSha256=input_hash)
    effects = []
    for stage in ('stage1', 'stage2', 'stage3'):
        identity = run + '|' + key + '|' + pid + '|' + stage
        effect = dict(case_key=key, stage=stage, process_id=pid, input_sha256=input_hash,
                      effect_id=digest(('effect|' + identity).encode()), receipt_id=digest(('receipt|' + identity).encode()),
                      first_invocation_id=stage + '-invocation')
        effects.append(effect)
        values[stage + 'Receipt'] = effect['receipt_id']
    history = dict(id=pid, businessKey=key, state='COMPLETED', endTime='2026-11-01T00:00:00Z', deleteReason=None)
    variables = [dict(processInstanceId=pid, name=k, value=v) for k, v in values.items()]
    receipt = dict(case=dict(case_key=key, process_id=pid, business_key=key, body=canonical(spec['case']), input_sha256=input_hash), effects=effects)
    body = {'businessKey': key, 'variables': {k: dict(value=v, type='Integer' if k == 'completedStages' else 'String')
                                            for k, v in {**values, 'completedStages': 0}.items() if not k.endswith('Receipt')}}
    descriptions = [
        ('engine', 'POST', '/process-definition/key/bench_fnv07_jobs/start', body, 200, {'id': pid}),
        ('probe', 'POST', '/register', dict(case_key=key, process_id=pid, business_key=key, case=spec['case']), 200,
         dict(case_key=key, process_id=pid, input_sha256=input_hash)),
        ('engine', 'POST', '/message', dict(messageName='fnv07-release', businessKey=key), 204, None),
        ('engine', 'GET', '/history/process-instance/' + pid, None, 200, history),
        ('engine', 'GET', '/history/variable-instance?processInstanceId=' + pid + '&maxResults=100', None, 200, variables),
        ('probe', 'POST', '/observe', dict(case_key=key), 200, receipt)]
    rows = [dict(index=i, arrival=0, service=s, method=m, path=p, body=b, status=c, response=r,
                 started_offset_ns=1_020_000_000 + i * 20_000_000, finished_offset_ns=1_030_000_000 + i * 20_000_000)
            for i, (s, m, p, b, c, r) in enumerate(descriptions)]
    planned = [dict(index=0, scheduled_offset_ns=1_000_000_000, case_key=key)]
    plan = dict(study_id=spec['study_id'], run_id=run, protocol=spec, origin_monotonic_ns=123_456_789,
                origin_epoch_ns=1793491200000000000, clock=dict(monotonic=True, adjustable=False),
                planned=planned, arrival_start_offset_ns=1_000_000_000, drain_end_offset_ns=22_000_000_000,
                engine_url='http://127.0.0.1:1', probe_url='http://127.0.0.1:2', outcomes=[])
    collected = dict(deepcopy(plan), finished_offset_ns=1_200_000_000, outcomes=[{
        **planned[0], 'dispatch_offset_ns': 1_001_000_000, 'worker_started_offset_ns': 1_010_000_000,
        'finished_offset_ns': 1_140_000_000, 'status': 'observed_correct_pending_audit', 'process_id': pid,
        'requests': list(range(6)), 'observation_requests': [3, 4, 5], 'observed_offset_ns': 1_130_000_000}])
    return plan, collected, rows


class TimingAuditTests(unittest.TestCase):
    def test_primary_includes_scheduling_start_registration_and_observation(self):
        result = check_trace(*fixture())
        self.assertTrue(result['driver_qualified'])
        self.assertEqual(result['planned_arrivals'], 1)
        self.assertEqual(result['observation_latency_seconds'], [.13])

    def test_receipts_or_engine_success_alone_are_not_a_correct_outcome(self):
        for change in ('receipt_missing', 'receipt_input', 'counter', 'engine_active', 'history_id', 'variable_pid', 'receipt_id', 'extra_variable'):
            p, c, r = fixture()
            if change == 'receipt_missing': r[5]['response']['effects'].pop()
            if change == 'receipt_input': r[5]['response']['case']['input_sha256'] = 'wrong'
            if change == 'counter': r[4]['response'][0]['value'] = 2
            if change == 'engine_active': r[3]['response']['state'] = 'ACTIVE'
            if change == 'history_id': r[3]['response']['id'] = 'different'
            if change == 'variable_pid': r[4]['response'][0]['processInstanceId'] = 'different'
            if change == 'receipt_id': r[5]['response']['effects'][0]['receipt_id'] = 'different'
            if change == 'extra_variable': r[4]['response'].append(dict(processInstanceId='original-process', name='unexpected', value=1))
            with self.subTest(change=change), self.assertRaises(AssertionError):
                check_trace(p, c, r)

    def test_clock_shortcuts_missing_requests_and_changed_arrivals_are_rejected(self):
        for change in ('receipt_timestamp', 'schedule', 'reverse_clock', 'early', 'late', 'missing', 'duplicate', 'hidden', 'unknown_mutation', 'retry'):
            p, c, r = fixture()
            if change == 'receipt_timestamp': c['outcomes'][0]['observed_offset_ns'] = r[3]['finished_offset_ns']
            if change == 'schedule': c['planned'][0]['scheduled_offset_ns'] += 1
            if change == 'reverse_clock': r[3]['finished_offset_ns'] = r[3]['started_offset_ns'] - 1
            if change == 'early': c['outcomes'][0]['dispatch_offset_ns'] = 0
            if change == 'late': c['outcomes'][0]['worker_started_offset_ns'] += 500_000_000
            if change == 'missing': r.pop()
            if change == 'duplicate': r[-1]['index'] = 0
            if change == 'hidden': c['outcomes'][0]['requests'].pop()
            if change == 'unknown_mutation': r[0]['transport_error'] = 'TimeoutError'
            if change == 'retry': r[3].update(method='POST', path='/message')
            with self.subTest(change=change), self.assertRaises(AssertionError):
                check_trace(p, c, r)

    def test_missed_arrival_stays_in_denominator_without_invented_latency(self):
        p, c, _ = fixture()
        c['outcomes'] = [{**p['planned'][0], 'status': 'missed_no_worker', 'requests': []}]
        result = check_trace(p, c, [])
        self.assertEqual((result['planned_arrivals'], result['correct_observed'], result['missed_arrivals']), (1, 0, 1))
        self.assertEqual(result['observation_latency_seconds'], [])
        self.assertFalse(result['driver_qualified'])

    def test_original_read_error_is_not_erased_by_later_success(self):
        p, c, r = fixture()
        failed = dict(r[3], status=503, response=None, index=6, started_offset_ns=1_071_000_000, finished_offset_ns=1_075_000_000)
        r.insert(3, failed)
        for index, row in enumerate(r): row['index'] = index
        c['outcomes'][0]['requests'] = list(range(7))
        c['outcomes'][0]['observation_requests'] = [4, 5, 6]
        result = check_trace(p, c, r)
        self.assertEqual(result['correct_observed'], 1)
        self.assertEqual(result['original_http_failures'], 1)
        self.assertFalse(result['driver_qualified'])

    def test_qualification_protocol_cannot_expand_silently(self):
        for change in ({'rate_per_s': 100}, {'rate_per_s': 10, 'arrival_seconds': 10}, {'workers': 0},
                       {'qualification_only': False}, {'poll_seconds': 1}, {'case': {'id': 'fault', 'mode': 'fail_after_always'}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate({**protocol(), **change})


class ActualHttpDriverTests(unittest.TestCase):
    def exercise(self, *, slow=False, reject=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ProbeStore(root / 'receipts.sqlite', 'native-http', ['n1'])
            store.node('n1', {'runtime': 'synthetic-http-fixture'})
            probe = make_server(store, admin_token='private-admin-fixture', execute_token='private-execute-fixture')
            engine_state, starts = {}, []
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_args): pass
                def reply(self, status, body):
                    raw = b'' if body is None else json.dumps(body).encode()
                    self.send_response(status); self.send_header('Content-Length', str(len(raw)))
                    self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(raw)
                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    if self.path.endswith('/start'):
                        starts.append(time.monotonic())
                        if slow: time.sleep(1.1)
                        if reject: return self.reply(503, {'error': 'deliberate'})
                        pid = 'pid-' + str(len(starts))
                        engine_state[pid] = dict(body=body, completed=False)
                        return self.reply(200, {'id': pid})
                    if self.path == '/message':
                        pid = next(k for k, v in engine_state.items() if v['body']['businessKey'] == body['businessKey'])
                        binding = store.observe(body['businessKey'])['case']
                        for stage in ('stage1', 'stage2', 'stage3'):
                            store.execute(dict(run_id='native-http', case_key=binding['case_key'], process_id=pid,
                                business_key=binding['business_key'], input_sha256=binding['input_sha256'], stage=stage,
                                node_id='n1', invocation_id=uuid.uuid4().hex))
                        engine_state[pid]['completed'] = True
                        return self.reply(204, None)
                    self.reply(404, None)
                def do_GET(self):
                    if self.path.startswith('/history/process-instance/'):
                        pid = self.path.rsplit('/', 1)[1]
                        item = engine_state[pid]
                        return self.reply(200, dict(id=pid, businessKey=item['body']['businessKey'], state='COMPLETED',
                                                   endTime='2026-11-01T00:00:00Z', deleteReason=None))
                    if self.path.startswith('/history/variable-instance?'):
                        pid = self.path.split('processInstanceId=')[1].split('&')[0]
                        body = engine_state[pid]['body']
                        values = {k: v['value'] for k, v in body['variables'].items()}
                        values['completedStages'] = 3
                        values.update({e['stage'] + 'Receipt': e['receipt_id'] for e in store.observe(body['businessKey'])['effects']})
                        return self.reply(200, [dict(processInstanceId=pid, name=k, value=v) for k, v in values.items()])
                    self.reply(404, None)
            engine = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (probe, engine)]
            for worker in threads: worker.start()
            try:
                spec = dict(protocol(), rate_per_s=5 if slow else 2, workers=1 if slow else 8)
                run_arrivals(FluxnovaClient(f'http://127.0.0.1:{engine.server_port}', 'fixture', 'fixture'),
                    ProbeClient(f'http://127.0.0.1:{probe.server_port}', 'private-admin-fixture'), root, 'native-http', spec)
                plan = json.loads((root / 'load-plan.json').read_text())
                collection = json.loads((root / 'load-collection.json').read_text())
                rows = [json.loads(line) for line in (root / 'load-requests.ndjson').read_text().splitlines()]
                self.assertNotIn('private-admin-fixture', (root / 'load-requests.ndjson').read_text())
                result = check_trace(plan, collection, rows)
                return result, len(starts), collection
            finally:
                for server in (engine, probe): server.shutdown(); server.server_close()
                for worker in threads: worker.join(timeout=5)

    def test_real_http_roundtrip_preserves_original_inputs_and_receipts(self):
        result, starts, _ = self.exercise()
        self.assertTrue(result['driver_qualified'])
        self.assertEqual((result['planned_arrivals'], result['correct_observed'], starts), (2, 2, 2))

    def test_slow_work_drops_arrivals_instead_of_slowing_offered_load(self):
        result, starts, _ = self.exercise(slow=True)
        self.assertEqual(result['planned_arrivals'], 5)
        self.assertEqual(starts, 1)
        self.assertEqual(result['missed_arrivals'], 4)
        self.assertEqual(result['correct_observed'], 1)
        self.assertFalse(result['driver_qualified'])

    def test_failed_start_is_not_retried_and_does_not_disappear(self):
        result, starts, collection = self.exercise(reject=True)
        self.assertEqual((result['planned_arrivals'], starts, result['failed_arrivals'], result['original_http_failures']), (2, 2, 2, 2))
        self.assertFalse(result['driver_qualified'])
        self.assertTrue(all(o['status'] == 'start_failed_or_unknown' for o in collection['outcomes']))


if __name__ == '__main__':
    unittest.main()
