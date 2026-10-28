import copy
from pathlib import Path
import tempfile
import unittest

from services.review_routing import RoutingStore,policy,snapshot,fingerprint


def payment(**changes):
    return {'payment_id':'payment','beneficiary':'synthetic-beneficiary','amount_minor':100,
            'limit_minor':1000,'authorized':True,'sanctions_match':False,'high_risk':False,
            'stale_market':False,'checks_known':True,**changes}


class Engine:
    def __init__(self,arm,p,review=False,actor='reviewer',decision='approve'):
        self.values={**snapshot(p,'case'),'reviewerDecision':decision,'reviewedPaymentSha256':fingerprint(p)}
        self.arm=arm;self.tasks=[{'id':'task','taskDefinitionKey':'payment_review','endTime':'done'}] if review else []
        self.actor=actor
    def request(self,method,path):
        if path.startswith('/history/process-instance/'):return {'businessKey':'case','processDefinitionKey':'bench_w03_'+self.arm}
        if path.startswith('/history/variable-instance?'):return [{'name':k,'value':v} for k,v in self.values.items()]
        if path.startswith('/history/task?'):return self.tasks
        if path.startswith('/history/user-operation?'):return [{'taskId':'task','operationType':'Complete','userId':self.actor}]
        raise AssertionError(path)


class RoutingTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
    def store(self,arm='policy_routing',p=None,**kwargs):
        p=p or payment();e=Engine(arm,p,**kwargs);s=RoutingStore(Path(self.tmp.name)/'study.sqlite',e)
        s.register('case','pid',arm,p,'reviewer');return s,e

    def test_policy_fixed_authority_and_required_reviews(self):
        self.assertEqual(policy(payment()),'eligible')
        self.assertEqual(policy(payment(amount_minor=1001)),'review_required')
        self.assertEqual(policy(payment(high_risk=True)),'review_required')
        self.assertEqual(policy(payment(authorized=False)),'rejected')
        self.assertEqual(policy(payment(sanctions_match=True)),'rejected')
        self.assertEqual(policy(payment(checks_known=False)),'blocked')
        self.assertEqual(policy(payment(stale_market=True)),'blocked')
        with self.assertRaises(ValueError):policy(payment(limit_minor=999999))
        with self.assertRaises(ValueError):policy(payment(authorized=1))

    def test_direct_outcome_and_repeated_calls_have_one_exact_effect(self):
        store,_=self.store();store.execute('case','pid','route')
        first=store.execute('case','pid','settle');self.assertEqual(first['outcome'],'settled')
        for _ in range(3):self.assertTrue(store.execute('case','pid','settle')['replayed'])
        self.assertEqual(store.state('case')['effects'],[{'payment_id':'payment','amount_minor':100,'beneficiary':'synthetic-beneficiary'}])

    def test_missing_required_review_cannot_be_bypassed(self):
        store,e=self.store(p=payment(amount_minor=2000));store.execute('case','pid','route')
        e.values['reviewerDecision']='approve'
        with self.assertRaisesRegex(ValueError,'required_review_count'):store.execute('case','pid','settle')
        self.assertEqual(store.state('case')['effects'],[])

    def test_authenticated_actor_must_match_not_identity_variable(self):
        store,e=self.store('all_review',review=True,actor='agent');store.execute('case','pid','route')
        e.values['reviewerIdentity']='reviewer'
        with self.assertRaisesRegex(ValueError,'review_actor'):store.execute('case','pid','settle')

    def test_rejection_and_unsafe_inputs_never_effect(self):
        store,_=self.store('all_review',p=payment(authorized=False),review=True)
        store.execute('case','pid','route');self.assertEqual(store.execute('case','pid','settle')['outcome'],'rejected')
        self.assertEqual(store.state('case')['effects'],[])

    def test_original_input_binding_and_cross_process_replay(self):
        store,e=self.store();store.execute('case','pid','route')
        with self.assertRaisesRegex(ValueError,'binding'):store.execute('case','other','settle')
        e.values['payment_amount_minor']=1
        with self.assertRaisesRegex(ValueError,'original_payment_changed'):store.execute('case','pid','settle')
        with self.assertRaisesRegex(ValueError,'immutable_binding'):store.register('case','pid','policy_routing',payment(amount_minor=1),'reviewer')


if __name__=='__main__':unittest.main()
