import copy
import unittest
from types import SimpleNamespace

from scripts.run_live_payment_trials import CASES, schedule, verify
from services.live_recoverable_agent import provider_usage
from services.recoverable_agent import digest
from services.worker import task_request


class LiveTrialTests(unittest.TestCase):
    def fixture(self, case):
        status, kyc, settlement, effects, requests = CASES[case]
        payment = {'authorized': case != 'unauthorized', 'approved': False,
                   'amount_minor': 2000 if case == 'over_limit' else 100,
                   'limit_minor': 1000, 'sanctions_match': case == 'sanctions'}
        checkpoint = {'context': {'payment': payment}, 'provider_requests': [
            {'metadata': {'server_usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}}
            for _ in range(requests)]}
        report = {'request_id': 'trial', 'status': status, **provider_usage(SimpleNamespace(data=checkpoint))}
        observation = {'history': {}, 'ledger': {}, 'queues': {'active_instances': 0, 'external_tasks': 0, 'incidents': 0}}
        for scenario, outcome in [('kyc', kyc), ('settlement', settlement)]:
            history = [{'state': 'COMPLETED', 'businessKey': 'trial:' + scenario, 'processDefinitionKey': 'bench_' + scenario}] if outcome else []
            values = {k: {'value': v} for k, v in {**payment, 'scenario': scenario, 'logical_request_id': 'trial:' + scenario}.items()}
            request = task_request({'variables': values})
            observation['history'][scenario] = history
            observation['ledger'][scenario] = {'result': {'outcome': outcome} if outcome else None,
                'fingerprint': digest({'operation': request['operation'], 'payload': request['payload']}) if outcome else None,
                'effect_count': effects if scenario == 'settlement' else 0, 'violations': []}
        return report, checkpoint, observation

    def test_rotation_keeps_five_prespecified_trials_of_every_case(self):
        order = schedule(5)
        self.assertEqual(len(order), 20)
        for case in CASES:
            self.assertEqual(sum(name == case for _, name in order), 5)
        self.assertEqual(order[4], (2, 'unauthorized'))

    def test_all_four_expected_policy_outcomes_pass(self):
        for case in CASES:
            with self.subTest(case=case):
                self.assertEqual(verify(case, *self.fixture(case)), [])

    def test_rejected_payment_with_effect_cannot_pass(self):
        report, journal, observed = self.fixture('unauthorized')
        observed['ledger']['settlement']['effect_count'] = 1
        self.assertIn('settlement_effect_count', verify('unauthorized', report, journal, observed))

    def test_sanctions_cannot_hide_an_unexpected_settlement_instance(self):
        report, journal, observed = self.fixture('sanctions')
        observed['history']['settlement'] = [{'state': 'COMPLETED'}]
        self.assertIn('settlement_instance_count', verify('sanctions', report, journal, observed))

    def test_incomplete_or_changed_usage_cannot_pass(self):
        report, journal, observed = self.fixture('authorized')
        report['server_usage_totals']['total_tokens'] += 1
        self.assertIn('journal_usage_mismatch', verify('authorized', report, journal, observed))
        report = copy.deepcopy(report)
        report['usage_complete'] = False
        self.assertIn('usage_unknown', verify('authorized', report, journal, observed))

    def test_another_workflow_or_changed_payload_cannot_pass(self):
        report, journal, observed = self.fixture('authorized')
        observed['history']['kyc'][0]['businessKey'] = 'other:kyc'
        observed['ledger']['settlement']['fingerprint'] = 'different'
        errors = verify('authorized', report, journal, observed)
        self.assertIn('kyc_history', errors)
        self.assertIn('settlement_fingerprint', errors)
