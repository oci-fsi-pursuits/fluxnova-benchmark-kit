import unittest

from benchlib.adversarial import dispatch_cases
from benchlib.oci_model import normalize
from services.payment_agent import AgentStopped, run_agent


class AdversarialTests(unittest.TestCase):
    def test_continuation_selection_cannot_repeat_or_invent_case_ids(self):
        from scripts.run_adversarial_payment_checks import select_cases
        from benchlib.adversarial import NOTE_CASES
        remaining = [row['id'] for row in NOTE_CASES[3:]]
        self.assertEqual(select_cases(NOTE_CASES, remaining), NOTE_CASES[3:])
        self.assertEqual(select_cases(NOTE_CASES, list(reversed(remaining))), NOTE_CASES[3:])
        for invalid in ([remaining[0], remaining[0]], ['missing']):
            with self.assertRaisesRegex(ValueError, 'unknown_or_duplicate'):
                select_cases(NOTE_CASES, invalid)

    def test_online_fixture_evaluator_cannot_hide_an_unexpected_effect(self):
        from scripts.run_adversarial_payment_checks import fixture_errors
        case = dispatch_cases()[0]
        result = {'status': 'stopped', 'reason': 'invalid_tool'}
        observed = {'history': {'kyc': [], 'settlement': []},
                    'ledger': {s: {'result': None, 'effect_count': 0, 'violations': []} for s in ('kyc', 'settlement')},
                    'queues': {'active_instances': 0, 'external_tasks': 0, 'incidents': 0}}
        self.assertEqual(fixture_errors(case, result, [], observed, 'trial'), [])
        observed['ledger']['settlement']['effect_count'] = 1
        self.assertIn('settlement_effect_or_violation', fixture_errors(case, result, [], observed, 'trial'))
        observed['history']['settlement'] = [{'state': 'COMPLETED', 'businessKey': 'other', 'processDefinitionKey': 'bench_settlement'}]
        self.assertIn('settlement_instance_count', fixture_errors(case, result, [], observed, 'trial'))
        self.assertIn('settlement_history', fixture_errors(case, result, [], observed, 'trial'))

    def test_optional_note_is_user_data_and_cannot_replace_verified_tool_result(self):
        import json
        from benchlib.adversarial import NOTE_CASES
        from benchlib.oci_model import SYSTEM, conversation
        self.assertEqual(conversation([], [])[0]['text'], SYSTEM)
        for row in NOTE_CASES:
            messages = conversation([], [], row['note'])
            self.assertEqual(messages[-1]['role'], 'USER')
            self.assertEqual(json.loads(messages[-1]['text']), {'untrusted_beneficiary_note': row['note']})
            self.assertNotIn(row['note'], messages[0]['text'])
        with self.assertRaisesRegex(ValueError, 'invalid_untrusted_context'):
            conversation([], [], 'x' * 2049)
        with self.assertRaisesRegex(ValueError, 'missing_or_duplicate_tool_result'):
            conversation([{'final': False, 'tool_calls': [{'id': 'real', 'name': 'screen_beneficiary', 'arguments': {}}]}], [], NOTE_CASES[3]['note'])

    def test_hostile_native_responses_and_valid_controls(self):
        for case in dispatch_cases():
            with self.subTest(case=case['id']):
                sequence = iter(case['raw_responses']);stages = []
                def model(*args):
                    try:
                        return normalize(next(sequence))
                    except ValueError:
                        raise AgentStopped('normalization_rejected') from None
                def tools(scenario, deadline):
                    stages.append(scenario)
                    return 'approved' if scenario == 'kyc' else ('settled' if case['trusted_case'] == 'authorized' else 'rejected')
                try:
                    result = run_agent(model, tools)
                except AgentStopped as error:
                    result = {'status': 'stopped', 'reason': str(error)}
                self.assertEqual(result['status'], case['expected_status'])
                self.assertEqual(result.get('reason'), case['expected_reason'])
                self.assertEqual(stages, case['expected_stages'])

    def test_empty_wrong_type_calls_cannot_become_final_after_screening(self):
        for value in ({}, '', 0, False):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'invalid_model_tool_calls'):
                    normalize({'choices': [{'finish_reason': 'stop', 'message': {'tool_calls': value}}]})

    def test_duplicate_keys_are_rejected_at_any_nesting_depth(self):
        from benchlib.adversarial import tool
        for args in ('{"beneficiary":"other","beneficiary":"synthetic-beneficiary"}',
                     '{"beneficiary":{"name":"other","name":"synthetic-beneficiary"}}'):
            with self.assertRaisesRegex(ValueError, 'duplicate_model_argument_key'):
                normalize(tool(arguments=args))
