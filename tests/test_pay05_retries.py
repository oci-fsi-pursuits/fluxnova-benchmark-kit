"""Engine retry attribution must agree by identity and time, not only totals."""
import copy
import unittest

from scripts.audit_pay05_retries import reconcile


def fixture():
    records = []
    for arm in ('sequential', 'parallel'):
        pid = arm + '-process'; definition = 'bench_pay05_' + arm + ':1:fixture'
        operations = ('quote_a', 'quote_b', 'quote_c', 'select')
        worker = [{'status':'completed','task_id':arm + '-' + op,'operation':op} for op in operations]
        if arm == 'parallel':worker.append({'status':'retry_or_incident','task_id':arm + '-quote_b','operation':'quote_b','error_type':'HTTPError'})
        events = [{'event':'completed','phase':'measurement','details':{'process_instance_id':pid,
            'history':{'instance':{'id':pid,'processDefinitionId':definition}},
            'backend':{'audit':[{'event':'replay'}] if arm == 'parallel' else []}}}]
        records.append({'folder':arm,'arm':arm,'stage':'performance','events':events,'worker':{'records':worker},
            'execution':{'started_at':'2026-11-01T00:00:00+00:00','finished_at':'2026-11-01T00:02:00+00:00'}})
    log = ('2026-11-01T00:01:00.000Z WARN app : ENGINE-REST-HTTP500 org.finos.fluxnova.bpm.engine.OptimisticLockingException: '
           'ENGINE-03005 UPDATE ExecutionEntity failed. ProcessDefinitionId: bench_pay05_parallel:1:fixture, '
           'ProcessInstanceId: parallel-process, ExecutionId: example,\n')
    return log, records


class Pay05RetryAuditTests(unittest.TestCase):
    def test_error_receipt_worker_identity_and_window_all_agree(self):
        result = reconcile(*fixture())
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['engine_optimistic_lock_http500'], 1)
        self.assertEqual(result['measured_backend_replays'], {'sequential':0,'parallel':1})

    def test_equal_total_cannot_hide_wrong_process_definition_or_clock(self):
        for old,new in [('parallel-process','unknown-process'), ('bench_pay05_parallel:1:fixture','bench_pay05_parallel:2:other'),
                        ('00:01:00.000Z','00:03:00.000Z')]:
            log, records = fixture()
            with self.subTest(new=new), self.assertRaises(AssertionError): reconcile(log.replace(old,new), records)

    def test_missing_duplicate_or_different_server_error_never_counts_as_explained(self):
        log, records = fixture()
        for changed in ('', log+log, log.replace('OptimisticLockingException','DatabaseTimeoutException')):
            with self.subTest(log=changed[:20]), self.assertRaises(AssertionError): reconcile(changed,records)

    def test_worker_error_or_task_mismatch_rejects_even_with_matching_server_total(self):
        for field,value in [('error_type','TimeoutError'), ('task_id','unrelated-task'), ('operation','select')]:
            log, records = fixture();records[-1]['worker']['records'][-1][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError): reconcile(log,records)

    def test_missing_receipt_or_unclassified_fetch_failure_is_not_explained(self):
        log, records = fixture();records[-1]['events'][0]['details']['backend']['audit'] = []
        with self.assertRaises(AssertionError): reconcile(log,records)
        log, records = fixture();records[-1]['worker']['records'].append({'status':'fetch_error'})
        with self.assertRaises(AssertionError): reconcile(log,records)

    def test_duplicate_process_identity_cannot_be_hidden_by_a_later_lane(self):
        log, records = fixture();duplicate = copy.deepcopy(records[-1]);duplicate['folder'] = 'another';records.append(duplicate)
        with self.assertRaises(AssertionError): reconcile(log,records)

    def test_qualification_and_warmup_never_enter_measured_performance_total(self):
        for stage,phase in [('qualification','measurement'), ('qualification','warmup'), ('performance','warmup')]:
            log, records = fixture()
            records[-1]['stage'] = stage; records[-1]['events'][0]['phase'] = phase
            result = reconcile(log,records)
            with self.subTest(stage=stage,phase=phase):
                self.assertEqual(result['engine_optimistic_lock_http500'], 1)
                self.assertEqual(result['measured_backend_replays']['parallel'], 0)
                self.assertEqual(result['backend_replays_by_stage_phase_arm'][stage][phase]['parallel'], 1)

    def test_unknown_study_stage_is_not_silently_excluded(self):
        log, records = fixture(); records[-1]['stage'] = 'unknown'
        with self.assertRaises(AssertionError): reconcile(log,records)


if __name__ == '__main__': unittest.main()
