import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from services.payment_approval import Approval, snapshot, typed

PAYMENT = {'amount_minor': 2000, 'limit_minor': 1000, 'authorized': True, 'sanctions_match': False}


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.engine = Mock(base='http://engine'); self.support = Mock(base='http://support')
        self.review = Approval(self.folder, self.engine, self.support, PAYMENT)

    def pending(self):
        self.review.status = Mock(return_value={'state': 'pending', 'task_id': 'task'})

    def test_trusted_inputs_reject_approval_flags_and_bad_numeric_types(self):
        for value in ({**PAYMENT, 'approved': True}, {**PAYMENT, 'amount_minor': True},
                      {**PAYMENT, 'authorized': 1}, {**PAYMENT, 'amount_minor': 100}):
            with self.assertRaises(ValueError): snapshot(value, 'key')
        self.assertEqual(typed({'a': True, 'b': 2000})['b']['type'], 'Long')

    def test_resume_binds_payment_and_endpoint(self):
        with self.assertRaisesRegex(ValueError, 'payment_mismatch'):
            Approval(self.folder, self.engine, self.support, {**PAYMENT, 'amount_minor': 3000})
        with self.assertRaisesRegex(ValueError, 'context_mismatch'):
            Approval(self.folder, Mock(base='other'), self.support)

    def test_unknown_start_not_repeated(self):
        self.engine.request.side_effect = ConnectionError()
        with self.assertRaises(ConnectionError): self.review.start()
        restored = Approval(self.folder, self.engine, self.support)
        self.engine.request.reset_mock(); self.engine.request.side_effect = None
        self.engine.request.return_value = []
        with self.assertRaisesRegex(ValueError, 'unresolved_start_no_retry'): restored.start()
        self.assertEqual([c.args[0] for c in self.engine.request.call_args_list], ['GET'])

    def test_ambiguous_completion_reserves_decision_and_never_resends(self):
        self.pending(); self.engine.request.side_effect = ConnectionError()
        with self.assertRaises(ConnectionError): self.review.decide('approve')
        saved = json.loads(self.review.path.read_text())
        self.assertEqual(saved['decision'], 'approve'); self.assertTrue(saved['complete_intent'])
        self.engine.request.reset_mock()
        with self.assertRaisesRegex(ValueError, 'unresolved_completion_no_retry'): self.review.decide('approve')
        self.engine.request.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'conflicting_review_decision'): self.review.decide('reject')

    def test_expired_review_blocks_new_completion(self):
        self.pending(); self.review.data['review_expires_utc_s'] = 0
        with self.assertRaisesRegex(ValueError, 'review_expired'): self.review.decide('approve')
        self.engine.request.assert_not_called()

    def test_completed_replay_reconciles_without_post_even_after_expiry(self):
        result = {'state': 'completed', 'effect_count': 1}
        self.review.data.update(decision='approve', complete_intent=True, review_expires_utc_s=0)
        self.review.status = Mock(return_value=result)
        self.assertEqual(self.review.decide('approve'), result)
        self.engine.request.assert_not_called()

    def test_changed_snapshot_detected_before_completion(self):
        self.review.data['process_id'] = 'pid'
        self.review.history = Mock(return_value=[{'id': 'pid', 'endTime': None}])
        changed = snapshot(PAYMENT, self.review.data['key']); changed['paymentAmountMinor'] = 3000
        self.engine.request.return_value = [{'name': k, 'value': v} for k, v in changed.items()]
        with self.assertRaisesRegex(ValueError, 'payment_snapshot_changed'): self.review.decide('approve')
        self.assertFalse(any(c.args[0] == 'POST' for c in self.engine.request.call_args_list))
        self.support.request.assert_not_called()

    def test_completion_between_reads_uses_surviving_history(self):
        self.review.data['process_id'] = 'pid'
        self.review.history = Mock(return_value=[{'id': 'pid', 'endTime': None}])
        values = snapshot(PAYMENT, self.review.data['key'])
        self.engine.request.side_effect = [[{'name': k, 'value': v} for k, v in values.items()], []]
        self.support.request.return_value = {'effect_count': 1}
        self.assertEqual(self.review.status()['state'], 'settling')
        self.assertFalse(any('/process-instance/pid/variables' in c.args[1]
                             for c in self.engine.request.call_args_list))


if __name__ == '__main__': unittest.main()
