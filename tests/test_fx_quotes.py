import concurrent.futures
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import xml.etree.ElementTree as ET

from benchlib.fx_quotes import select_quote, validate_intent
from services.fx_quotes import ACTIVITIES, OPERATIONS, QuoteStore
from services.workflow_checks import canonical, make_server
from services.workflow_checks_worker import process_task
from services.worker import Client

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / 'workloads/pay05/cases.json').read_text())['cases']


class QuotePolicyTests(unittest.TestCase):
    def test_prespecified_routes_and_safe_stops(self):
        for case in CASES:
            with self.subTest(case=case['case_id']):
                quotes = {r: s['quote'] if s['outcome'] == 'quote' else None for r, s in case['quotes'].items()}
                result = select_quote(case['payment'], quotes, 1000, minimum_valid_quotes=case['minimum_valid_quotes'])
                self.assertEqual({k: result[k] for k in case['expected']}, case['expected'])

    def test_half_open_validity_and_order_independent_tie_break(self):
        case = copy.deepcopy(CASES[0])
        quotes = {r: s['quote'] for r, s in case['quotes'].items()}
        quotes['b'].update(issued_ms=100, expires_ms=200)
        self.assertEqual(select_quote(case['payment'], quotes, 99)['selected_route'], 'a')
        self.assertEqual(select_quote(case['payment'], quotes, 100)['selected_route'], 'b')
        self.assertEqual(select_quote(case['payment'], quotes, 199)['selected_route'], 'b')
        self.assertEqual(select_quote(case['payment'], quotes, 200)['selected_route'], 'a')
        quotes['b'].update(issued_ms=0, expires_ms=60000, source_amount_minor=1120)
        self.assertEqual(select_quote(case['payment'], dict(reversed(list(quotes.items()))), 100)['selected_route'], 'a')

    def test_policy_inputs_fail_closed(self):
        case = copy.deepcopy(CASES[0])
        for field, value in [('authorized', 'true'), ('target_amount_minor', True), ('target_amount_minor', 0),
                             ('max_source_total_minor', float('inf')), ('allowed_routes', ['a', 'a']), ('source_currency', 'usd')]:
            changed = copy.deepcopy(case['payment']); changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_intent(changed)
        quotes = {r: s['quote'] for r, s in case['quotes'].items()}
        for value in [-1, True, float('nan'), '100']:
            with self.assertRaises(ValueError): select_quote(case['payment'], quotes, value)
        with self.assertRaises(ValueError): select_quote(case['payment'], {'a': quotes['a']}, 100)

    def test_response_cannot_supply_authority_or_non_integer_money(self):
        for value in [True, 1.5, float('inf'), float('nan'), '10', -1, 0]:
            case = copy.deepcopy(CASES[0]); quotes = {r: s['quote'] for r, s in case['quotes'].items()}
            quotes['b']['source_amount_minor'] = value
            self.assertEqual(select_quote(case['payment'], quotes, 100)['selected_route'], 'a')
        case['payment']['authorized'] = False
        quotes['b']['authorized'] = True
        self.assertEqual(select_quote(case['payment'], quotes, 100)['outcome'], 'rejected')

    def test_bpmn_arms_only_change_quote_order(self):
        ns = {'b': 'http://www.omg.org/spec/BPMN/20100524/MODEL'}
        for arm in ('sequential', 'parallel'):
            root = ET.parse(ROOT / 'workloads/pay05' / (arm + '.bpmn'))
            tasks = root.findall('.//b:serviceTask', ns)
            self.assertEqual({t.attrib['id'] for t in tasks}, set(ACTIVITIES))
            self.assertEqual({t.attrib['{http://camunda.org/schema/1.0/bpmn}topic'] for t in tasks}, {'bench-pay05-quotes'})
            self.assertEqual(len(root.findall('.//b:parallelGateway', ns)), 2 if arm == 'parallel' else 0)


class QuoteStoreTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.clock = [10_000_000_000]
        self.store = QuoteStore(Path(folder.name) / 'quotes.sqlite', clock_ns=lambda: self.clock[0], sleep=lambda _: None)
        self.case = copy.deepcopy(CASES[0])
        self.store.register('case', 'process', self.case)

    def execute(self, operation, **changes):
        return self.store.execute({'case_key': 'case', 'process_id': 'process', 'operation': operation, **changes})

    def collect(self):
        for op in OPERATIONS[:-1]: self.assertEqual(self.execute(op)[0], 200)

    def test_waits_for_all_responses_and_commits_exact_intent(self):
        self.assertEqual(self.execute('select'), (409, {'error': 'quotes_incomplete'}))
        self.execute('quote_a'); self.execute('quote_b')
        self.assertEqual(self.execute('select')[0], 409)
        self.execute('quote_c'); result = self.execute('select')[1]
        self.assertEqual(result['selected_route'], 'b')
        self.assertEqual(self.store.state('case')['effects'], [{'key': 'case', 'payment_id': 'synthetic-fx-payment',
            'beneficiary': 'synthetic-fx-beneficiary', 'target_amount_minor': 1000, 'source_currency': 'USD',
            'target_currency': 'EUR', 'route_id': 'b', 'source_total_minor': 1120}])

    def test_concurrent_repeats_and_restart_preserve_one_effect_after_expiry(self):
        self.collect()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.execute('select'), range(16)))
        self.assertEqual(sum(not result[1]['replayed'] for result in results), 1)
        original = self.store.state('case')
        reopened = QuoteStore(self.store.path, clock_ns=lambda: 10**15, sleep=lambda _: None)
        result = reopened.execute({'case_key': 'case', 'process_id': 'process', 'operation': 'select'})
        self.assertTrue(result[1]['replayed'])
        self.assertEqual(reopened.state('case')['effects'], original['effects'])
        self.assertEqual(reopened.state('case')['receipts'], original['receipts'])

    def test_expiry_is_rechecked_at_effect_not_quote_collection(self):
        self.collect(); self.clock[0] += 60_000_000_000
        self.assertEqual(self.execute('select')[1]['outcome'], 'blocked')
        self.assertEqual(self.store.state('case')['effects'], [])

    def test_process_and_business_payment_cannot_rebind(self):
        for key, process in [('case', 'different'), ('different', 'different')]:
            with self.assertRaises(ValueError): self.store.register(key, process, self.case)
        changed = copy.deepcopy(self.case); changed['payment']['authorized'] = False
        with self.assertRaises(ValueError): self.store.register('case', 'process', changed)
        self.assertEqual(self.execute('quote_a', process_id='different')[0], 409)
        self.assertEqual(self.store.state('case')['receipts'], {})

    def test_worker_cannot_override_selection_or_authority(self):
        for changes in [{'authorized': True}, {'selected_route': 'c'}, {'source_total_minor': 1}]:
            self.assertEqual(self.execute('select', **changes)[0], 400)
        self.assertEqual(self.store.state('case')['effects'], [])

    def test_tampered_receipt_is_refused(self):
        self.collect()
        with self.store.connect() as db:
            receipt = json.loads(db.execute("SELECT body FROM receipts WHERE operation='quote_b'").fetchone()[0])
            receipt['quote']['fee_minor'] = 0
            db.execute("UPDATE receipts SET body=? WHERE operation='quote_b'", (canonical(receipt),))
        self.assertEqual(self.execute('select'), (409, {'error': 'quote_receipt_binding_mismatch'}))
        self.assertEqual(self.store.state('case')['effects'], [])

    def test_clock_reset_does_not_create_effect(self):
        self.collect(); self.clock[0] = 0
        self.assertEqual(self.execute('select'), (409, {'error': 'registration_clock_changed'}))
        self.assertEqual(self.store.state('case')['effects'], [])

    def test_registered_scenarios_match_expected_effects(self):
        for source in CASES[1:]:
            case = copy.deepcopy(source); original = case['payment']['payment_id']; key = case['case_id']
            case['payment']['payment_id'] = key
            for s in case['quotes'].values():
                if isinstance(s['quote'], dict) and s['quote'].get('payment_id') == original: s['quote']['payment_id'] = key
            self.store.register(key, key, case)
            for op in OPERATIONS:
                status, receipt = self.store.execute({'case_key': key, 'process_id': key, 'operation': op})
                self.assertEqual(status, 200)
            self.assertEqual({k: receipt[k] for k in case['expected']}, case['expected'])
            self.assertEqual(len(self.store.state(key)['effects']), int(case['expected']['outcome'] == 'settled'))

    def test_http_roles_and_custom_worker_mapping(self):
        server = make_server('127.0.0.1', 0, self.store, 'worker-fixture', 'registrar-fixture', service_name='pay05-synthetic-quotes')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        client = Client('http://127.0.0.1:' + str(server.server_port), 'Bearer worker-fixture')
        self.assertEqual(client.request('GET', '/health')['service'], 'pay05-synthetic-quotes')
        with self.assertRaises(urllib.error.HTTPError) as error:
            client.request('POST', '/cases/register', {'case_key': 'new', 'process_id': 'new', 'case': self.case})
        self.assertEqual(error.exception.code, 401)
        class Engine:
            calls = []
            def request(self, method, path, body): self.calls.append(body)
        engine = Engine()
        task = {'id': 'task', 'activityId': 'quote_c', 'processInstanceId': 'process',
                'variables': {'studyCaseKey': {'value': 'case'}}}
        result = process_task(task, engine, client, 'worker', activities=ACTIVITIES, error_prefix='pay05')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(engine.calls[0]['variables'], {'pay05QuoteC': {'value': 'quote', 'type': 'String'}})


if __name__ == '__main__': unittest.main()
