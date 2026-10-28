import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from services.live_recoverable_agent import JournaledProvider, provider_usage
from services.payment_agent import AgentStopped
from services.recoverable_agent import Journal, ReplayModel, atomic_json


class Crash(BaseException):
    pass


class LiveRecoveryTests(unittest.TestCase):
    context = {'wall_s': 60, 'provider': {'model_id': 'model', 'region': 'region', 'request_budget': 2}}

    def setUp(self):
        self.context = copy.deepcopy(self.context)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.journal = Journal(self.directory, self.context)
        self.calls = 0

    def factory(self, prior, record):
        def call(body, deadline):
            self.calls += 1
            response = {'model_id': 'model', 'chat_response': {'choices': [
                {'finish_reason': 'stop', 'message': {}}]}}
            decision = {'final': True, 'tool_calls': []}
            record({'status': 'received', 'requested_model_id': 'model', 'returned_model_id': 'model',
                    'region': 'region', 'response': response, 'normalized': decision,
                    'response_sha256': hashlib.sha256(json.dumps(response, sort_keys=True).encode()).hexdigest(),
                    'server_usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}})
            return decision
        return call

    def invoke(self, journal=None, body=None):
        journal = journal or self.journal
        provider = JournaledProvider(journal, self.factory)
        return ReplayModel(journal, provider)(body or {'turn': 0}, time.monotonic() + 30)

    def resume(self):
        return Journal(self.directory, self.context, resume=True)

    def test_saved_decision_replays_without_another_native_call(self):
        self.invoke()
        journal = self.resume()
        self.assertEqual(self.invoke(journal), {'final': True, 'tool_calls': []})
        self.assertEqual(self.calls, 1)
        self.assertEqual(provider_usage(journal)['server_usage_totals']['total_tokens'], 12)

    def test_receipt_survives_crash_before_checkpoint_update(self):
        def write_then_crash(path, value):
            atomic_json(path, value)
            raise Crash()
        with patch('services.live_recoverable_agent.atomic_json', write_then_crash):
            with self.assertRaises(Crash):
                self.invoke()
        journal = self.resume()
        self.assertNotIn('response', journal.data['model'][0])
        self.invoke(journal)
        self.assertEqual(self.calls, 1)
        self.assertEqual(journal.data['model_requests'], 1)
        self.assertTrue(provider_usage(journal)['usage_complete'])

    def test_unknown_request_is_never_repeated_or_reported_as_zero_usage(self):
        def fail_factory(prior, record):
            def fail(*args):
                raise Crash()
            return fail
        with self.assertRaises(Crash):
            ReplayModel(self.journal, JournaledProvider(self.journal, fail_factory))({'turn': 0}, time.monotonic()+30)
        journal = self.resume()
        with self.assertRaisesRegex(AgentStopped, 'unresolved_provider_request'):
            self.invoke(journal)
        self.assertEqual(self.calls, 0)
        self.assertIsNone(provider_usage(journal)['server_usage_totals'])
        self.assertEqual(provider_usage(journal)['unknown_usage_requests'], 1)

    def test_native_budget_does_not_reset_on_restart(self):
        self.invoke(body={'turn': 0})
        journal = self.resume()
        provider = JournaledProvider(journal, self.factory)
        replay = ReplayModel(journal, provider)
        replay({'turn': 0}, time.monotonic()+30)
        replay({'turn': 1}, time.monotonic()+30)
        journal = self.resume()
        replay = ReplayModel(journal, JournaledProvider(journal, self.factory))
        replay({'turn': 0}, time.monotonic()+30)
        replay({'turn': 1}, time.monotonic()+30)
        with self.assertRaisesRegex(AgentStopped, 'durable_native_request_budget'):
            replay({'turn': 2}, time.monotonic()+30)
        self.assertEqual(self.calls, 2)
        self.assertEqual(provider_usage(journal)['server_usage_totals']['total_tokens'], 24)

    def test_missing_or_changed_receipt_blocks_resume(self):
        self.invoke()
        path = self.directory / 'provider-1.json'
        original = path.read_bytes()
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            JournaledProvider(self.resume(), self.factory)
        path.write_bytes(original)
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'receipt_missing'):
            JournaledProvider(self.resume(), self.factory)

    def test_missing_usage_cannot_cache_a_payment_decision(self):
        def factory(prior, record):
            def missing(entry):
                entry['server_usage'] = None
                record(entry)
            return self.factory(prior, missing)
        with self.assertRaisesRegex(AgentStopped, 'provider_usage_missing'):
            ReplayModel(self.journal, JournaledProvider(self.journal, factory))({'turn': 0}, time.monotonic()+30)
        self.assertNotIn('response', self.journal.data['model'][0])
        self.assertFalse(provider_usage(self.journal)['usage_complete'])

    def test_wrong_model_receipt_cannot_cache_a_payment_decision(self):
        def factory(prior, record):
            def wrong(entry):
                entry['returned_model_id'] = 'other-model'
                record(entry)
            return self.factory(prior, wrong)
        with self.assertRaisesRegex(ValueError, 'provider_response_mismatch'):
            ReplayModel(self.journal, JournaledProvider(self.journal, factory))({'turn': 0}, time.monotonic()+30)
        self.assertNotIn('response', self.journal.data['model'][0])

    def test_note_and_timeout_settings_must_match_durable_receipt(self):
        self.journal.data['context']['provider'] = {**self.context['provider'], 'read_timeout_s': 40,
            'untrusted_context_sha256': 'expected-note', 'untrusted_context_bytes': 10}
        self.journal.save()
        with self.assertRaisesRegex(ValueError, 'provider_receipt_settings_mismatch'):
            self.invoke()
        self.assertNotIn('response', self.journal.data['model'][0])
