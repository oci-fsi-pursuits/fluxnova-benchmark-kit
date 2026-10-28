import unittest
import urllib.error

from services.mock_service import mock_model
from services.payment_agent import AgentStopped, run_agent


class PaymentAgentTests(unittest.TestCase):
    def model(self, body, deadline):
        status, result = mock_model(body)
        if status != 200:
            raise urllib.error.HTTPError('fixture', status, 'injected', {}, None)
        return result

    def test_normal_continuation_uses_verified_screen_before_payment(self):
        calls=[]
        def tool(name, deadline):
            calls.append(name)
            return 'approved' if name=='kyc' else 'settled'
        result=run_agent(self.model,tool)
        self.assertEqual(result['status'],'settled')
        self.assertEqual(calls,['kyc','settlement'])

    def test_invalid_tool_cannot_invoke_any_workflow(self):
        calls=[]
        with self.assertRaisesRegex(AgentStopped,'invalid_tool'):
            run_agent(self.model,lambda *args:calls.append(args),mode='invalid_tool')
        self.assertEqual(calls,[])

    def test_loop_reuses_screening_and_stops_at_turn_budget(self):
        calls=[]
        def tool(name,deadline):calls.append(name);return 'approved'
        with self.assertRaisesRegex(AgentStopped,'turn_budget'):
            run_agent(self.model,tool,mode='loop',max_turns=3)
        self.assertEqual(calls,['kyc'])

    def test_429_has_bounded_retries_without_tool_effects(self):
        calls=[];sleeps=[]
        with self.assertRaisesRegex(AgentStopped,'throttle_budget'):
            run_agent(self.model,lambda *args:calls.append(args),mode='throttle',sleep=sleeps.append)
        self.assertEqual(calls,[])
        self.assertEqual(sleeps,[.1,.2])

    def test_model_cannot_finish_before_screening(self):
        with self.assertRaisesRegex(AgentStopped,'final_without_verified_screening'):
            run_agent(lambda *args:{'final':True,'tool_calls':[]},lambda *args:self.fail('tool called'))

    def test_screen_rejection_prevents_payment(self):
        calls=[]
        def tool(name,deadline):calls.append(name);return 'rejected'
        self.assertEqual(run_agent(self.model,tool)['status'],'blocked_screening')
        self.assertEqual(calls,['kyc'])

    def test_elapsed_model_call_cannot_start_tool(self):
        now=[0.0]
        def model(body,deadline):now[0]=2;return mock_model(body)[1]
        with self.assertRaisesRegex(AgentStopped,'wall_budget'):
            run_agent(model,lambda *args:self.fail('tool called'),wall_s=1,clock=lambda:now[0])

    def test_model_cannot_modify_trusted_payment_arguments(self):
        response={'final':False,'tool_calls':[{'id':'x','name':'screen_beneficiary','arguments':{'beneficiary':'synthetic-beneficiary','approved':True}}]}
        with self.assertRaisesRegex(AgentStopped,'invalid_tool'):
            run_agent(lambda *args:response,lambda *args:self.fail('tool called'))
