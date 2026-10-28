import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from services.bound_payment import IntentRegistry, BoundWorker, payment_request, verify_history
from services.mock_service import Ledger
from services.payment_approval import snapshot, typed
from services.recoverable_agent import digest

PAYMENT={'amount_minor':2000,'limit_minor':1000,'authorized':True,'sanctions_match':False}


class BoundPaymentTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.folder=Path(temp.name)
        self.registry=IntentRegistry(self.folder/'intent.sqlite',b'test-only-signing-key-material-32-bytes')
        self.intent=self.registry.issue(PAYMENT,'reviewer')
        self.registry.bind(self.intent['key'],'process','screen','screen-key')
        self.values={**snapshot(PAYMENT,self.intent['key']),'paymentBeneficiary':'synthetic-beneficiary',
                     'boundIntent':self.intent['token'],'reviewedPaymentSha256':digest(PAYMENT),'reviewerDecision':'approve'}
        self.task={'id':'external','businessKey':self.intent['key'],'processInstanceId':'process','variables':typed(self.values)}

    def test_restart_retains_original_binding_and_rebinding_is_rejected(self):
        restored=IntentRegistry(self.folder/'intent.sqlite',b'test-only-signing-key-material-32-bytes')
        self.assertEqual(restored.verify(self.task,self.values)['process_id'],'process')
        restored.bind(self.intent['key'],'process','screen','screen-key')
        with self.assertRaisesRegex(ValueError,'already_bound'):restored.bind(self.intent['key'],'other','screen','screen-key')

    def test_changed_amount_authority_beneficiary_key_or_ack_cannot_pass(self):
        for field,value in [('paymentAmountMinor',3000),('paymentAuthorized',False),('paymentSanctions',True),
                            ('paymentBeneficiary','attacker'),('paymentKey','other'),('reviewedPaymentSha256','forged')]:
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'review_payment_changed'):
                self.registry.verify(self.task,{**self.values,field:value})

    def test_signed_token_cannot_be_reused_on_another_engine_process(self):
        with self.assertRaisesRegex(ValueError,'intent_process_mismatch'):
            self.registry.verify({**self.task,'processInstanceId':'other'},self.values)

    def test_cross_payment_token_swap_and_unicode_signature_fail_closed(self):
        other=self.registry.issue(PAYMENT,'reviewer')
        with self.assertRaisesRegex(ValueError,'intent_registry_mismatch'):
            self.registry.verify(self.task,{**self.values,'boundIntent':other['token']})
        for token in (self.intent['token'][:-1]+'z',self.intent['token'].split('.')[0]+'.é','invalid'):
            with self.assertRaises(ValueError):self.registry.verify(self.task,{**self.values,'boundIntent':token})

    def test_expired_intent_does_not_authorize_a_payment(self):
        expires=self.registry.load(self.intent['key'])['body']['expires_utc_s']
        with self.assertRaisesRegex(ValueError,'intent_expired'):self.registry.verify(self.task,self.values,now=expires)

    def test_wrong_key_cannot_reopen_the_persistent_registry(self):
        with self.assertRaisesRegex(ValueError,'registry_signing_key_mismatch'):
            IntentRegistry(self.folder/'intent.sqlite',b'different-test-signing-key-32-bytes')

    def test_issuance_request_id_survives_restart_without_renewal(self):
        issued=self.registry.issue(PAYMENT,'reviewer',request_id='operation-1')
        restored=IntentRegistry(self.folder/'intent.sqlite',b'test-only-signing-key-material-32-bytes')
        self.assertEqual(restored.issue(PAYMENT,'reviewer',request_id='operation-1'),issued)
        with self.assertRaisesRegex(ValueError,'issuance_request_conflict'):
            restored.issue({**PAYMENT,'amount_minor':3000},'reviewer',request_id='operation-1')

    def test_expired_intent_reconciles_prior_receipt_but_cannot_send(self):
        ledger=Ledger(self.folder/'ledger.sqlite');request=payment_request(self.intent)
        self.assertEqual(ledger.execute(request)[0],200)
        support=Mock();support.request.side_effect=lambda method,path,body=None:ledger.state(self.intent['key'])
        engine=Mock();worker=BoundWorker(engine,support,self.registry)
        expires=self.registry.load(self.intent['key'])['body']['expires_utc_s']
        with patch('services.bound_payment.verify_history'),patch('services.bound_payment.time.time',return_value=expires+1):
            worker.process(self.task)
        self.assertFalse(any(c.args[0]=='POST' for c in support.request.call_args_list))
        self.assertEqual(engine.request.call_args.args[2]['variables']['workerResult']['value'],'settled')
        empty=Mock();empty.request.return_value={'result':None,'effect_count':0,'fingerprint':None,'violations':[]}
        engine=Mock();worker=BoundWorker(engine,empty,self.registry)
        with patch('services.bound_payment.verify_history'),patch('services.bound_payment.time.time',return_value=expires+1):
            worker.process(self.task)
        self.assertFalse(any(c.args[0]=='POST' for c in empty.request.call_args_list))
        self.assertEqual(engine.request.call_args.args[2]['variables']['integrityReason']['value'],'intent_expired')

    def test_business_key_is_taken_from_engine_metadata(self):
        with self.assertRaisesRegex(ValueError,'unknown_intent'):
            self.registry.verify({**self.task,'businessKey':'other'},self.values)

    def test_tampered_review_is_completed_as_rejected_without_ledger_access(self):
        engine,support=Mock(),Mock()
        worker=BoundWorker(engine,support,self.registry)
        self.task['variables']=typed({**self.values,'paymentAmountMinor':3000})
        worker.process(self.task)
        support.request.assert_not_called()
        body=engine.request.call_args.args[2]
        self.assertEqual(body['variables']['workerResult']['value'],'integrity_rejected')

    def test_valid_worker_replay_uses_canonical_ledger_receipt_once(self):
        ledger=Ledger(self.folder/'ledger.sqlite');calls=[]
        class Support:
            def request(self,method,path,body=None):
                calls.append(method)
                if method=='GET':return ledger.state(path.rsplit('/',1)[1])
                status,result=ledger.execute(body)
                assert status==200
                return result
        engine=Mock();worker=BoundWorker(engine,Support(),self.registry)
        with patch('services.bound_payment.verify_history'):
            worker.process(self.task)
            worker.process(self.task)
        self.assertEqual(calls.count('POST'),1)
        state=ledger.state(self.intent['key']);self.assertEqual(state['effect_count'],1)
        effect=[e for e in state['events'] if e['event']=='payment_effect'][0]
        self.assertEqual(effect['details']['amount_minor'],2000)

    def test_display_label_does_not_substitute_for_actual_review_actor(self):
        record=self.registry.load(self.intent['key'])
        engine=Mock();support=Mock()
        engine.request.side_effect=[{'businessKey':self.intent['key'],'processDefinitionKey':'bench_bound_payment_approval'},
                                    [{'taskDefinitionKey':'payment_review','endTime':'done','id':'task'}],
                                    [{'taskId':'task','operationType':'Complete','userId':'attacker'}]]
        with self.assertRaisesRegex(ValueError,'review_actor_mismatch'):verify_history(engine,support,record)
        support.request.assert_not_called()


if __name__=='__main__':unittest.main()
