import json
import subprocess
import unittest

from benchlib.control_poll import read_status_with_retries


class ControlPollTests(unittest.TestCase):
    def test_transient_status_disconnect_recovers_without_restarting_a_workload(self):
        calls=[];waits=[]
        def read():
            calls.append('status_read')
            if len(calls)==1:raise ConnectionError('channel reset')
            return {'status':'running','completed_arms':2}
        self.assertEqual(read_status_with_retries(read,wait=waits.append),{'status':'running','completed_arms':2})
        self.assertEqual(calls,['status_read','status_read']);self.assertEqual(waits,[5])

    def test_timeouts_and_incomplete_json_have_a_finite_retry_bound(self):
        for error in (subprocess.TimeoutExpired('ssh',45),json.JSONDecodeError('partial','',0)):
            with self.subTest(error=type(error).__name__):
                calls=[];waits=[]
                def read():calls.append(1);raise error
                with self.assertRaises(type(error)):read_status_with_retries(read,wait=waits.append)
                self.assertEqual(len(calls),3);self.assertEqual(waits,[5,5])

    def test_failed_workload_status_is_returned_without_retry(self):
        waits=[];value={'status':'failed','issues':['incorrect_effect']}
        self.assertIs(read_status_with_retries(lambda:value,wait=waits.append),value)
        self.assertEqual(waits,[])

    def test_invalid_state_or_ownership_error_is_not_retried(self):
        calls=[]
        def read():calls.append(1);raise ValueError('ownership mismatch')
        with self.assertRaisesRegex(ValueError,'ownership mismatch'):read_status_with_retries(read)
        self.assertEqual(calls,[1])
        with self.assertRaises(ValueError):read_status_with_retries(read,attempts=0)


if __name__=='__main__':unittest.main()
