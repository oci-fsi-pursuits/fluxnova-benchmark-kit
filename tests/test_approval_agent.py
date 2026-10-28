import tempfile
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch

from services.approval_agent import ApprovalAgentTools, DeadlineClient
from services.payment_agent import AgentStopped, run_agent
from services.recoverable_agent import Journal
from services.mock_service import mock_model


class ApprovalAgentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.payment = {'authorized': True, 'approved': False, 'amount_minor': 2000, 'limit_minor': 1000, 'sanctions_match': False}
        self.journal = Journal(self.folder, {'payment': self.payment, 'wall_s': 30})
        self.engine, self.support = Mock(base='http://engine'), Mock(base='http://support')

    def test_pending_review_is_a_bounded_agent_outcome(self):
        calls=[];events=[]
        def tool(name, deadline):calls.append(name); return 'approved' if name=='kyc' else 'pending_review'
        result=run_agent(lambda body,deadline:mock_model(body)[1],tool,emit=events.append)
        self.assertEqual(result['status'],'pending_review');self.assertEqual(calls,['kyc','settlement'])
        self.assertIn('payment_review_pending',[e['event'] for e in events])
        self.assertNotIn('settlement_verified',[e['event'] for e in events])

    def test_deadline_wrapper_blocks_expired_requests(self):
        with self.assertRaisesRegex(AgentStopped,'wall_budget'):
            DeadlineClient(self.engine,time.monotonic()-1).request('GET','/version')
        self.engine.request.assert_not_called()
        DeadlineClient(self.engine,time.monotonic()+1).request('GET','/version')
        self.engine.request.assert_called_once_with('GET','/version',None)

    def test_trusted_fixed_delegation_cannot_be_overridden(self):
        for change in ({'approved':True},{'limit_minor':20000},{'amount_minor':True},{'authorized':1}):
            self.journal.data['context']['payment']={**self.payment,**change}
            with self.assertRaisesRegex(ValueError,'invalid_trusted_agent_payment'):
                ApprovalAgentTools(self.engine,self.support,self.journal,lambda event:None)

    def test_over_limit_routes_to_review_without_normal_settlement(self):
        tools=ApprovalAgentTools(self.engine,self.support,self.journal,lambda event:None)
        with patch('services.approval_agent.Approval') as cls:
            cls.return_value.data={'start_intent':True}
            cls.return_value.start.return_value={'state':'pending'}
            self.assertEqual(tools('settlement',time.monotonic()+10),'pending_review')
            cls.return_value.start.assert_called_once()
        self.engine.request.assert_not_called()
        self.assertEqual(tools.definition('kyc'),'bench_agent_screen')
        self.assertEqual(tools.definition('settlement'),'bench_agent_direct')

    def test_original_deadline_cannot_create_a_new_review(self):
        self.journal.data['action_deadline_utc_s']=0
        tools=ApprovalAgentTools(self.engine,self.support,self.journal,lambda event:None)
        with self.assertRaisesRegex(AgentStopped,'original_action_deadline'):
            tools('settlement',time.monotonic()+10)
        self.engine.request.assert_not_called()


if __name__=='__main__':unittest.main()
