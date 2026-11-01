"""Arrival/verification tests; synthetic HTTP fixtures are not Fluxnova results."""
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, unquote, urlsplit

from benchlib.pay05_load import audit, assess_cloud, expected_case, qualification_plan
from benchlib.runner import extract_event, normalize_event
from scripts.run_pay05_load_cloud import require_tools
from scripts.audit_pay05_load import check_ledger
from services.fx_quotes import QuoteStore
from services.workflow_checks import fingerprint

ROOT = Path(__file__).resolve().parents[1]
DEF = 'bench_pay05_sequential:1:fixture'


def fixture_corpus():
    original = json.loads((ROOT / 'workloads/pay05/cases.json').read_text(encoding='utf-8'))
    selected = [next(c for c in original['cases'] if c['expected']['outcome'] == kind) for kind in ('settled', 'rejected', 'blocked')]
    for case in selected:
        case['require_parallel_overlap'] = False
        for quote in case['quotes'].values(): quote['delay_ms'] = 0
    return {'cases': selected}


def fixture_data(case, key):
    receipts = {}
    for i, route in enumerate(('a', 'b', 'c')):
        receipts['quote_' + route] = {'case_key': key, 'process_id': key, 'operation': 'quote_' + route,
            'input_sha256': fingerprint(case), 'started_ns': 1000 + i * 100, 'finished_ns': 1100 + i * 100,
            'outcome': case['quotes'][route]['outcome'], 'quote': case['quotes'][route]['quote']}
    receipts['select'] = {'case_key': key, 'process_id': key, 'operation': 'select', 'input_sha256': fingerprint(case),
        'started_ns': 1300, 'finished_ns': 1400, 'decision_ns': 1350, 'decision_ms': 0, **case['expected']}
    p, expected = case['payment'], case['expected']
    effects = [{'key': key, **{k: p[k] for k in ('payment_id', 'beneficiary', 'target_amount_minor', 'source_currency', 'target_currency')},
                'route_id': expected['selected_route'], 'source_total_minor': expected['source_total_minor']}] if expected['outcome'] == 'settled' else []
    return {'original': case, 'process_instance_id': key, 'observed_complete_at': 'synthetic', 'poll_interval_ms': 100,
        'backend': {'case_key': key, 'process_id': key, 'input_sha256': fingerprint(case), 'registered_ns': 1000,
                    'receipts': receipts, 'effects': effects, 'audit': []},
        'history': {'instance': {'id': key, 'businessKey': key, 'state': 'COMPLETED', 'endTime': 'synthetic', 'processDefinitionId': DEF},
                    'variables': {'pay05Outcome': expected['outcome'], **{'pay05Quote' + r.upper(): case['quotes'][r]['outcome'] for r in ('a', 'b', 'c')}}},
        'activities': [{'activityType': 'noneEndEvent', 'activityId': expected['outcome'], 'endTime': 'synthetic'}]}


class Pay05LoadAuditTests(unittest.TestCase):
    def healthy(self):
        corpus = fixture_corpus(); events = []; count = 24
        for i in range(count):
            key = 'test:measurement:' + str(i)
            data = fixture_data(expected_case(corpus, i, key), key)
            data['observed_complete_elapsed_s'] = i + 1.2
            for kind, offset in [('scheduled', 0), ('started', .01), ('accepted', .02), ('completed', 1.2)]:
                events.append({'event': kind, 'run_id': 'test', 'logical_request_id': key, 'phase': 'measurement',
                    'elapsed_s': i + offset, 'status': 'correct' if kind == 'completed' else 'unknown', 'at': 'synthetic',
                    'details': copy.deepcopy(data) if kind == 'completed' else {}, 'latency_s': 1.2})
        summary = {'pay05': {'run_id': 'test', 'arm': 'sequential', 'rate': 1, 'duration_s': count, 'warmup_s': 0,
            'deadline_s': 2, 'poll_interval_ms': 100, 'case_mode': 'pay05_fixed_functional', 'definition_id': DEF,
            'corpus_fingerprint': fingerprint(corpus), 'corpus_size': 3, 'warmup': {'scheduled': 0, 'started': 0, 'dropped': 0},
            'measurement': {'scheduled': count, 'started': count, 'dropped': 0}}, 'metrics': {'http_reqs': {'values': {'rate': 10}}}}
        final = {'active_instances': 0, 'external_tasks': 0, 'incidents': 0, 'completed_instances': count,
                 'effects': 8, 'registrations': count, 'receipts': 4 * count}
        return events, summary, final, corpus

    def evaluate(self, events, summary, final, corpus):
        return audit(events, summary, 0, {'valid': True, 'requested_http_rate': 50}, final, corpus)

    def test_every_outcome_keeps_its_own_denominator(self):
        result = self.evaluate(*self.healthy())
        self.assertEqual(result['status'], 'passed', result['issues'])
        for kind in ('settled', 'rejected', 'blocked'):
            self.assertEqual(result['cohorts']['measurement']['outcomes'][kind]['verified'], 8)
        self.assertFalse(result['performance_comparison_complete'])

    def test_driver_success_cannot_override_wrong_quote_binding_or_ledger(self):
        for mutation in ('route', 'receipt_pid', 'effect', 'decision_clock', 'definition', 'original'):
            with self.subTest(mutation=mutation):
                args = self.healthy(); done = next(e for e in args[0] if e['event'] == 'completed')['details']
                if mutation == 'route': done['backend']['receipts']['select']['selected_route'] = 'a'
                elif mutation == 'receipt_pid': done['backend']['receipts']['quote_b']['process_id'] = 'another'
                elif mutation == 'effect': done['backend']['effects'][0]['target_amount_minor'] += 1
                elif mutation == 'decision_clock': done['backend']['receipts']['select']['decision_ns'] = -1
                elif mutation == 'definition': done['history']['instance']['processDefinitionId'] = 'another'
                else: done['original']['payment']['authorized'] = False
                result = self.evaluate(*args)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['cohorts']['measurement']['correct'], 23)

    def test_missing_duplicate_and_dropped_arrivals_fail(self):
        for change in ('missing', 'duplicate', 'dropped'):
            args = self.healthy()
            if change == 'missing': args[0].pop()
            elif change == 'duplicate': args[0].append(copy.deepcopy(args[0][0]))
            else: args[1]['pay05']['measurement']['dropped'] = 1
            with self.subTest(change=change): self.assertEqual(self.evaluate(*args)['status'], 'failed')

    def test_scheduled_clock_and_quote_delay_must_be_observed(self):
        args = self.healthy()
        for e in args[0]:
            if e['event'] == 'started': e['elapsed_s'] += .2
        self.assertIn('measurement_arrival_lag_exceeds_100ms', self.evaluate(*args)['issues'])
        args = self.healthy(); args[3]['cases'][0]['quotes']['a']['delay_ms'] = 100
        args[1]['pay05']['corpus_fingerprint'] = fingerprint(args[3])
        for e in args[0]:
            if e['event'] != 'completed' or int(e['logical_request_id'].rsplit(':', 1)[1]) % 3: continue
            data = e['details']; data['original']['quotes']['a']['delay_ms'] = 100
            data['backend']['input_sha256'] = fingerprint(data['original'])
            for r in data['backend']['receipts'].values(): r['input_sha256'] = fingerprint(data['original'])
        result = self.evaluate(*args)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(any('declared_quote_delay' in r['reason'] for r in result['cohorts']['measurement']['invalid_or_unknown']))

    def test_deadline_misses_are_results_not_discarded_runs(self):
        args = self.healthy()
        for e in args[0]:
            if e['event'] == 'completed':
                e['latency_s'] = 2.5; e['elapsed_s'] += 1.3
                e['details']['observed_complete_elapsed_s'] = e['elapsed_s']
        result = self.evaluate(*args)
        self.assertEqual(result['status'], 'passed', result['issues'])
        self.assertEqual(result['cohorts']['measurement']['on_time_correct'], 0)
        self.assertEqual(result['cohorts']['measurement']['correct'], 24)

    def test_empty_run_reset_or_missing_telemetry_cannot_pass(self):
        events, summary, final, corpus = self.healthy()
        result = assess_cloud(events, summary, {'returncode': 0}, {'valid': True, 'requested_http_rate': 50}, final, [], {}, corpus)
        self.assertIn('too_few_telemetry_samples', result['issues'])
        self.assertIn('database_and_ledger_reset_not_verified', result['issues'])
        self.assertEqual(self.evaluate([], summary, final, corpus)['status'], 'failed')

    def test_plan_fixes_all_22_cases_and_both_rates_without_ranking(self):
        corpus = json.loads((ROOT / 'workloads/pay05/cases.json').read_text())
        plan = qualification_plan(corpus)
        self.assertEqual(plan['warmup_arrivals'] + plan['measurement_arrivals'], 66)
        self.assertEqual(len(plan['lanes']), 4)
        self.assertFalse(plan['performance_comparison_complete'])
        with self.assertRaises(ValueError): qualification_plan(fixture_corpus())

    def test_load_tools_must_be_pinned_separately(self):
        images = {k: 'registry.example/' + k + '@sha256:' + '1' * 64 for k in ('k6', 'postgres')}
        self.assertEqual(require_tools({'tool_images': images}), images)
        for tools in ({}, {**images, 'k6': 'grafana/k6:latest'}, {**images, 'other': images['k6']}):
            with self.assertRaises(ValueError): require_tools({'tool_images': tools})

    def test_retained_ledger_and_sql_history_must_match_driver_evidence(self):
        for mutation in (None, 'effect', 'sql_history', 'extra_registration'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'ledger.sqlite'; store = QuoteStore(path)
                key = 'independent-ledger-test'; case = expected_case(fixture_corpus(), 0, key)
                store.register(key, key, case)
                for op in ('quote_a', 'quote_b', 'quote_c', 'select'):
                    self.assertEqual(store.execute({'case_key': key, 'process_id': key, 'operation': op})[0], 200)
                data = fixture_data(case, key); data['backend'] = store.state(key)
                events = [{'event': 'completed', 'logical_request_id': key, 'details': data}]
                history = [{'id': key, 'business_key': key, 'definition_id': DEF, 'state': 'COMPLETED'}]
                if mutation == 'effect':
                    with store.connect() as db: db.execute('UPDATE effects SET target_amount_minor=999')
                elif mutation == 'sql_history': history[0]['state'] = 'ACTIVE'
                elif mutation == 'extra_registration': store.register('extra', 'extra', expected_case(fixture_corpus(), 0, 'extra'))
                if mutation:
                    with self.assertRaises(AssertionError): check_ledger(path, events, history)
                else: self.assertEqual(check_ledger(path, events, history), 1)


@unittest.skipUnless(os.environ.get('BENCH_TEST_K6_BINARY'), 'requires an explicit k6 executable')
class Pay05RealDriverTests(unittest.TestCase):
    def exercise(self, mode='healthy', extra=None, *, profiles=False):
        cases = {}; corpus = fixture_corpus()
        if profiles:
            corpus['case_mode'] = 'pay05_delay_profiles_v1'
            for case in corpus['cases']:
                case.update(base_case_id=case['case_id'], profile_id='fixture_zero_delay')
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def reply(self, status, data):
                raw = json.dumps(data).encode(); self.send_response(status)
                self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(raw))); self.end_headers()
                try: self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path.endswith('/start'): return self.reply(200, {'id': body['businessKey'], 'definitionId': DEF})
                if self.path == '/cases/register':
                    if mode == 'registration_unknown': return self.reply(503, {})
                    cases[body['case_key']] = body['case']
                    return self.reply(200, {'case_key': body['case_key'], 'process_id': body['process_id'], 'input_sha256': fingerprint(body['case'])})
                self.reply(404, {})
            def do_GET(self):
                path = urlsplit(self.path); query = parse_qs(path.query)
                key = unquote(path.path.rsplit('/', 1)[-1]) if not query else query['processInstanceId'][0]
                data = fixture_data(cases[key], key)
                if path.path.startswith('/history/process-instance/'):
                    if mode == 'never_completes': data['history']['instance']['endTime'] = None
                    return self.reply(200, data['history']['instance'])
                if path.path == '/history/variable-instance':
                    if mode == 'verification_delayed': time.sleep(.35)
                    return self.reply(200, [{'name': k, 'value': v} for k, v in data['history']['variables'].items()])
                if path.path == '/history/activity-instance': return self.reply(200, data['activities'])
                if path.path.startswith('/state/'):
                    if mode == 'wrong_effect': data['backend']['effects'] = [{'wrong': True}]
                    if mode == 'cross_process': data['backend']['receipts']['quote_b']['process_id'] = 'another'
                    return self.reply(200, data['backend'])
                self.reply(404, {})
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'corpus.json'; path.write_text(json.dumps(corpus), encoding='utf-8')
                env = {k: v for k, v in os.environ.items() if not k.startswith('PAY05_')}
                env.update(PAY05_ENGINE_URL=f'http://127.0.0.1:{server.server_port}', PAY05_SUPPORT_URL=f'http://127.0.0.1:{server.server_port}',
                    PAY05_RUN_ID='synthetic-driver-test', PAY05_ARM='sequential', PAY05_DEFINITION_ID=DEF,
                    PAY05_USERNAME='fixture', PAY05_PASSWORD='fixture', PAY05_REGISTRATION_TOKEN='fixture',
                    PAY05_RATE='1', PAY05_WARMUP_S='0', PAY05_DURATION_S='6', PAY05_DRAIN_S='3', PAY05_VUS='4',
                    PAY05_OUTPUT_DIR=directory, PAY05_CORPUS_FILE=str(path), PAY05_CORPUS_SHA256=hashlib.sha256(path.read_bytes()).hexdigest())
                env.update(extra or {})
                proc = subprocess.run([os.environ['BENCH_TEST_K6_BINARY'], 'run', '--log-format', 'json', '--no-color', str(ROOT / 'load/pay05_quotes.js')],
                                      env=env, capture_output=True, text=True, timeout=25)
                events = []
                for line in (proc.stdout + '\n' + proc.stderr).splitlines():
                    item = extract_event(line)
                    if item: events.append(normalize_event(item))
                summary_path = Path(directory) / 'k6-summary.json'
                self.assertTrue(summary_path.is_file(), proc.stderr)
                return proc.returncode, events, json.loads(summary_path.read_text()), corpus
        finally: server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_real_k6_mixed_outcomes_and_half_rate_arrivals(self):
        code, events, summary, corpus = self.exercise(extra={'PAY05_RATE': '.5', 'PAY05_DURATION_S': '6'})
        final = {'active_instances': 0, 'external_tasks': 0, 'incidents': 0, 'completed_instances': 3, 'effects': 1, 'registrations': 3, 'receipts': 12}
        result = audit(events, summary, code, {'valid': True, 'requested_http_rate': 50}, final, corpus)
        self.assertEqual(result['status'], 'passed', result['issues'])
        self.assertEqual([e['elapsed_s'] for e in events if e['event'] == 'scheduled'], [0, 2, 4])

    def test_real_k6_preserves_profile_metadata_and_checks_payment_effects(self):
        code, events, summary, corpus = self.exercise(extra={'PAY05_RATE': '.5', 'PAY05_DURATION_S': '6'}, profiles=True)
        final = {'active_instances': 0, 'external_tasks': 0, 'incidents': 0, 'completed_instances': 3, 'effects': 1, 'registrations': 3, 'receipts': 12}
        result = audit(events, summary, code, {'valid': True, 'requested_http_rate': 50}, final, corpus)
        self.assertEqual(result['status'], 'passed', result['issues'])
        self.assertEqual(summary['pay05']['case_mode'], 'pay05_delay_profiles_v1')
        self.assertTrue(all(e['details']['original']['profile_id'] == 'fixture_zero_delay' for e in events if e['event'] == 'completed'))
        _, bad, _, _ = self.exercise('wrong_effect', {'PAY05_DURATION_S': '2'}, profiles=True)
        completed_bad = [e for e in bad if e['event'] == 'completed']
        self.assertEqual(len(completed_bad), 2)
        self.assertTrue(all(e['status'] == 'incorrect' for e in completed_bad))

    def test_real_driver_rejects_unsafe_effects_and_cross_process_receipts(self):
        for mode in ('wrong_effect', 'cross_process'):
            with self.subTest(mode=mode):
                _, events, _, _ = self.exercise(mode, {'PAY05_DURATION_S': '2'})
                done = [e for e in events if e['event'] == 'completed']
                self.assertEqual(len(done), 2); self.assertTrue(all(e['status'] == 'incorrect' for e in done))

    def test_verification_reads_do_not_extend_completion_latency(self):
        _, events, _, _ = self.exercise('verification_delayed', {'PAY05_DURATION_S': '2'})
        done = [e for e in events if e['event'] == 'completed']
        self.assertEqual(len(done), 2)
        self.assertTrue(all(e['details']['validation_elapsed_s'] >= .35 and e['latency_s'] < .35 for e in done))

    def test_unknown_warmup_aborts_before_measurement(self):
        code, events, _, _ = self.exercise('registration_unknown', {'PAY05_WARMUP_S': '6'})
        self.assertEqual(code, 108); self.assertFalse(any(e['phase'] == 'measurement' for e in events))

    def test_dropped_arrivals_remain_in_denominator(self):
        _, events, summary, _ = self.exercise('never_completes', {'PAY05_DURATION_S': '4', 'PAY05_VUS': '1'})
        self.assertEqual(summary['pay05']['measurement']['scheduled'], 4)
        self.assertGreater(summary['pay05']['measurement']['dropped'], 0)
        self.assertFalse(any(e['event'] == 'completed' for e in events))


if __name__ == '__main__': unittest.main()
