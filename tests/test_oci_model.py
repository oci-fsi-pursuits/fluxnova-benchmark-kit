import json
import unittest
from types import SimpleNamespace

from benchlib.oci_model import OCIGenericModel, conversation, normalize, usage_counts, request_timeout, exception_diagnostics, guard_transport
from services.payment_agent import AgentStopped, run_agent


class OCIModelTests(unittest.TestCase):
    def test_wrapped_read_timeout_is_classified_without_exception_text(self):
        class ReadTimeout(Exception):pass
        inner=ReadTimeout('SECRET Authorization Bearer token')
        outer=Exception(inner)
        inner.__context__=outer
        result=exception_diagnostics(outer)
        self.assertEqual(result['transport_error_kind'],'read_timeout')
        self.assertEqual(len(result['exception_classes']),2)
        self.assertNotIn('SECRET',json.dumps(result))
        self.assertNotIn('Bearer',json.dumps(result))

    def test_hidden_sdk_or_redirect_resend_cannot_send_twice(self):
        calls=[]
        adapter=SimpleNamespace(send=lambda request,**kwargs:(calls.append(request) or SimpleNamespace(status_code=200)))
        session=SimpleNamespace(adapters={'https://':adapter})
        entry={};guard_transport(session,entry)
        adapter.send('first')
        with self.assertRaisesRegex(ValueError,'transport_resend_blocked'):adapter.send('second')
        self.assertEqual(calls,['first'])
        self.assertEqual(entry['transport_send_attempts'],1)
        self.assertEqual(entry['transport_resends_blocked'],1)

    def test_ambiguous_transport_failure_cannot_be_resent(self):
        def fail(*args,**kwargs):raise TimeoutError('private connection details')
        adapter=SimpleNamespace(send=fail);session=SimpleNamespace(adapters={'https://':adapter});entry={};guard_transport(session,entry)
        with self.assertRaises(TimeoutError):adapter.send('first')
        with self.assertRaisesRegex(ValueError,'transport_resend_blocked'):adapter.send('again')
        self.assertEqual(entry['transport_send_attempts'],1)

    def test_copied_session_and_other_adapter_share_one_send_budget(self):
        calls=[]
        def send(request,**kwargs):calls.append(request);return SimpleNamespace(status_code=200)
        session=SimpleNamespace(adapters={'https://':SimpleNamespace(send=send),'http://':SimpleNamespace(send=send)})
        entry={};guard_transport(session,entry)
        copied=SimpleNamespace(adapters=session.adapters.copy())
        session.adapters['https://'].send('first')
        with self.assertRaisesRegex(ValueError,'transport_resend_blocked'):copied.adapters['https://'].send('again')
        with self.assertRaisesRegex(ValueError,'transport_resend_blocked'):copied.adapters['http://'].send('redirect')
        self.assertEqual(calls,['first'])

    def test_larger_read_timeout_cannot_extend_remaining_action_interval(self):
        self.assertEqual(request_timeout(3, 40), (3, 3))
        self.assertEqual(request_timeout(90, 40), (5, 40))
        for invalid in (0, -1, 61, float('nan'), float('inf'), True):
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    OCIGenericModel({}, None, 'compartment', 'model', 'region', read_timeout_s=invalid)

    def test_tool_result_continuation_retains_call_id(self):
        prior=[{'final':False,'tool_calls':[{'id':'call-real','name':'screen_beneficiary','arguments':{'beneficiary':'synthetic-beneficiary'}}]}]
        messages=conversation(prior,[{'tool_call_id':'call-real','outcome':'approved'}])
        self.assertEqual(messages[-1]['role'],'TOOL')
        self.assertEqual(messages[-1]['tool_call_id'],'call-real')
        self.assertIn('approved',messages[-1]['text'])
        with self.assertRaisesRegex(ValueError,'missing_or_duplicate'):
            conversation(prior,[])

    def test_native_tool_response_normalizes_without_changing_arguments(self):
        response={'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'id':'x','name':'screen_beneficiary','arguments':'{"beneficiary":"synthetic-beneficiary"}'}]}}]}
        normalized=normalize(response)
        self.assertFalse(normalized['final'])
        self.assertEqual(normalized['tool_calls'][0]['arguments'],{'beneficiary':'synthetic-beneficiary'})

    def test_truncation_and_refusal_cannot_finish_payment(self):
        for choice in ({'finish_reason':'length','message':{}},{'finish_reason':'stop','message':{'refusal':'no'}}):
            with self.assertRaises(ValueError):normalize({'choices':[choice]})

    def test_usage_must_be_server_supplied_and_consistent(self):
        self.assertIsNone(usage_counts(None))
        self.assertIsNone(usage_counts({'prompt_tokens':1,'completion_tokens':2,'total_tokens':4}))
        self.assertIsNone(usage_counts({'prompt_tokens':True,'completion_tokens':2,'total_tokens':3}))
        self.assertEqual(usage_counts({'prompt_tokens':1,'completion_tokens':2,'total_tokens':3}),{'prompt_tokens':1,'completion_tokens':2,'total_tokens':3})

    def test_observed_unavailable_continuation_tool_cannot_start_payment(self):
        responses=iter([{'final':False,'tool_calls':[{'id':'screen','name':'screen_beneficiary','arguments':{'beneficiary':'synthetic-beneficiary'}}]},
                        {'final':False,'tool_calls':[{'id':'extra','name':'acknowledge','arguments':{'message':'done'}}]}])
        calls=[]
        def tool(name,deadline):calls.append(name);return 'approved'
        with self.assertRaisesRegex(AgentStopped,'invalid_tool'):
            run_agent(lambda *args:next(responses),tool)
        self.assertEqual(calls,['kyc'])
