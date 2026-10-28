import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
from services.bound_payment import IntentRegistry
from services.verifier_service import make_api
from services.worker import Client


class VerifierAPITests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        registry=IntentRegistry(Path(folder.name)/'intents.sqlite',b'test-only-long-signing-key-32-bytes')
        self.server=make_api(registry,'test-management-token',{'ready':True},0)
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start()
        def close():self.server.shutdown();self.server.server_close();self.thread.join()
        self.addCleanup(close)
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.client=Client(self.base,'Bearer test-management-token')
        self.body={'request_id':'request-1','payment':{'amount_minor':2000,'limit_minor':1000,'authorized':True,'sanctions_match':False},'reviewer':'reviewer','ttl_s':3600}

    def test_unauthenticated_management_is_denied(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:Client(self.base).request('POST','/intents',self.body)
        self.assertEqual(caught.exception.code,401);caught.exception.close()
        self.assertTrue(Client(self.base).request('GET','/health')['ready'])

    def test_issue_and_bind_replays_are_idempotent_and_conflicts_rejected(self):
        intent=self.client.request('POST','/intents',self.body)
        self.assertEqual(self.client.request('POST','/intents',self.body),intent)
        path='/intents/'+intent['key']+'/bind'
        binding={'process_id':'pid','screening_id':'sid','screening_key':'skey'}
        self.assertTrue(self.client.request('POST',path,binding)['bound'])
        self.assertTrue(self.client.request('POST',path,binding)['bound'])
        with self.assertRaises(urllib.error.HTTPError) as caught:self.client.request('POST',path,{**binding,'process_id':'other'})
        self.assertEqual(caught.exception.code,409);caught.exception.close()
        self.assertEqual(self.client.request('GET','/intents/'+intent['key'])['process_id'],'pid')

    def test_null_request_id_cannot_bypass_durable_issuance(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.client.request('POST','/intents',{**self.body,'request_id':None})
        self.assertEqual(caught.exception.code,409);caught.exception.close()


if __name__=='__main__':unittest.main()
