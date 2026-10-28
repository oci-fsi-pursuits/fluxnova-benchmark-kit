import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from benchlib.reset_mailbox import Mailbox,atomic_new,validate_receipt
from benchlib.runner import _run_reset

BINDING={'dataset_sha256':'a'*64,'state_fingerprint':'b'*64}


class ResetMailboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'mailbox'
        self.box=Mailbox.create(self.path,BINDING,120,2)

    def request(self,name='trial'):
        return self.box.submit(name,30,BINDING)

    def receipt(self,name='trial'):
        return {'run_id':name,'success':True,'writers_resumed':True,'method':'verified-test',**BINDING}

    def test_matching_claimed_receipt_works_after_broker_closes(self):
        request=self.request();self.assertEqual(self.box.pending(),[request])
        self.box.claim(request);self.assertEqual(self.box.pending(),[])
        self.box.deliver(request,self.receipt());self.box.close('completed')
        self.assertEqual(self.box.receive(request),self.receipt())
        with self.assertRaises(ValueError):self.request('later')

    def test_checkpoint_mismatch_and_duplicate_run_are_rejected(self):
        with self.assertRaises(ValueError):self.box.submit('wrong',30,{**BINDING,'dataset_sha256':'c'*64})
        self.request()
        with self.assertRaises(FileExistsError):self.request()

    def test_receipts_cannot_be_replaced_or_replayed_for_another_request(self):
        request=self.request();self.box.claim(request)
        with self.assertRaises(ValueError):self.box.deliver(request,self.receipt('different'))
        self.box.deliver(request,self.receipt())
        with self.assertRaises(FileExistsError):self.box.deliver(request,self.receipt())
        value=json.loads((self.path/'requests/trial/response.json').read_text())
        value['request_sha256']='c'*64
        (self.path/'requests/trial/response.json').write_text(json.dumps(value))
        with self.assertRaises(ValueError):self.box.receive(request)

    def test_failed_or_unresumed_restore_never_releases_workload(self):
        request=self.request();self.box.claim(request)
        with self.assertRaises(ValueError):self.box.deliver(request,{**self.receipt(),'writers_resumed':False})
        self.box.deliver(request,{'success':False,'run_id':'trial','error_type':'RestoreFailure'})
        with self.assertRaises(ValueError):self.box.receive(request)

    def test_expired_and_abandoned_requests_are_not_claimed(self):
        request=self.request()
        with patch('benchlib.reset_mailbox.time.time',return_value=request['deadline']+1):
            self.assertEqual(self.box.pending(),[])
            with self.assertRaises(ValueError):self.box.claim(request)
            with self.assertRaises(TimeoutError):self.box.receive(request)
        atomic_new(self.path/'requests/trial/abandoned.json',{'at':time.time()})
        self.assertEqual(self.box.pending(),[])
        with self.assertRaises(ValueError):self.box.claim(request)

    def test_request_count_and_concurrency_are_bounded(self):
        self.request();self.request('second')
        with self.assertRaises(ValueError):self.box.pending()
        with self.assertRaises(ValueError):self.request('third')
        (self.path/'active').mkdir()
        with self.assertRaises(FileExistsError):self.box.wait('new',30,BINDING)

    def test_zero_exit_with_false_or_wrong_reset_receipt_stops_runner(self):
        config={'execution':{'reset_command':['test-hook']},'comparison':BINDING}
        directory=Path(self.temp.name)
        for receipt in ({**self.receipt(),'success':False},self.receipt('another'),{**self.receipt(),'dataset_sha256':'c'*64},{}):
            completed=subprocess.CompletedProcess(['test-hook'],0,json.dumps(receipt),'')
            with patch('benchlib.runner.subprocess.run',return_value=completed):
                with self.assertRaises(RuntimeError):_run_reset(config,'trial',directory,{})
            self.assertFalse(json.loads((directory/'reset.json').read_text())['success'])
        with patch('benchlib.runner.subprocess.run',return_value=subprocess.CompletedProcess(['test-hook'],0,json.dumps(self.receipt()),'')):
            _run_reset(config,'trial',directory,{})
        self.assertEqual(json.loads((directory/'reset.json').read_text())['state_fingerprint'],BINDING['state_fingerprint'])

    def test_hook_timeout_retains_failure_receipt(self):
        directory=Path(self.temp.name)
        with patch('benchlib.runner.subprocess.run',side_effect=subprocess.TimeoutExpired(['hook'],1,output=b'waiting')):
            with self.assertRaises(RuntimeError):_run_reset({'execution':{'reset_command':['hook']}},'trial',directory,{})
        self.assertFalse(json.loads((directory/'reset.json').read_text())['success'])
        self.assertIn('waiting',(directory/'reset.log').read_text())


if __name__=='__main__':unittest.main()
