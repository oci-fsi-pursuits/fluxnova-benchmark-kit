import copy,json,subprocess,sys,tempfile
from pathlib import Path
import unittest
from scripts.audit_fnv06_load_qualification import check_driver
from scripts.fnv06_outcome_accounting import deadline_bounds,protocol,declared_for_reset
from benchlib.fnv06_reset import require_previous,require_failure_marker,reset_owned_schema
from tests.test_fnv06_load import driver_fixture
from tests.test_fnv06_reset import fixture as reset_fixture,FakeDatabase

ROOT=Path(__file__).resolve().parents[1]

def poll(count,start,end):return {'count':count,'start_elapsed_s':start,'finish_elapsed_s':end}

def accounted_reset(failed=True):
    m,t,s,a=reset_fixture();_,digest=protocol()
    m.update(qualification_phase='outcome_accounting_v1',outcome_protocol_sha256=digest)
    s['cases_collected']=['C01W']+([] if failed else ['C01M'])
    if failed:s.update(status='failed',error_type='RuntimeError',failed_observation={'id':'C01M','phase':'request_result','error_type':'RuntimeError','snapshot_available':True})
    a.update(status='outcomes_verified_with_request_failures' if failed else 'outcomes_verified',
        independent_outcomes_verified=True,reset_eligible=True,accounting_declared_before_execution=True,retrospective_accounting=False,
        accounting_protocol_sha256=digest,unverified_or_unfinished_completions=0,ordinary_qualification_passed=not failed,
        request_success=not failed,reported_collector_status=s['status'],
        request_accounting={'planned':110,'observed':110,'successful':106 if failed else 110,'timeout':4 if failed else 0,
                            'transport_failure':0,'http_failure':0,'failed':4 if failed else 0})
    return m,t,s,a

class OutcomeAccountingTests(unittest.TestCase):
    def driver(self,kind='signal'):
        f,a,s,w=driver_fixture(kind,10,True)
        f.update(study_id='FNV-06-REPEAT-QUAL',observation_id='C03M');s['observation_id']='C03M'
        for row in a:row.update(study_id=f['study_id'],observation_id='C03M',response=None)
        return f,a,s,w
    def check(self,data):
        f,a,s,w=data
        return check_driver(f,a,s,expected=w,engine_url='http://127.0.0.1:8080/engine-rest',account_failures=True)
    def test_failed_signal_has_no_invented_successful_ack_latency(self):
        d=self.driver();r=d[1][0];r.update(status=0,error_code=1050,elapsed_ms=30000,finished_epoch_ms=r['started_epoch_ms']+30000)
        result=self.check(d)
        self.assertEqual(result['request_accounting'],{'planned':1,'observed':1,'successful':0,'timeout':1,'transport_failure':0,'http_failure':0,'failed':1})
        self.assertIsNone(result['http_ack_median_ms']);self.assertIsNone(result['http_ack_max_ms'])
    def test_http_and_transport_errors_stay_distinct_from_success(self):
        for status,code,expected in ((500,1500,'http_failure'),(0,1200,'transport_failure')):
            d=self.driver();d[1][0].update(status=status,error_code=code)
            with self.subTest(status=status):self.assertEqual(self.check(d)['request_accounting'][expected],1)
    def test_failed_request_cannot_hide_missing_duplicate_input_clock_or_duration(self):
        for change in ('missing','duplicate','request','clock','timeout','body','warmup','bad_status'):
            d=self.driver('message');f,a,s,w=d
            if change=='missing':a.pop()
            elif change=='duplicate':a[1]['index']=0
            elif change=='request':a[0]['request']={}
            elif change=='clock':a[0]['finished_epoch_ms']+=100
            elif change=='timeout':a[0].update(status=0,error_code=1050,elapsed_ms=10)
            elif change=='body':a[0].update(status=0,error_code=1200,response={'unexpected':True})
            elif change=='warmup':f['observation_id']='C03W'
            else:a[0]['status']=-1
            with self.subTest(change=change),self.assertRaises(AssertionError):self.check(d)
    def test_deadline_counts_preserve_polling_uncertainty(self):
        r=deadline_bounds([poll(0,-.1,-.08),poll(3,1.9,2.1),poll(8,2.2,2.3),poll(10,2.4,2.5)],10)
        self.assertEqual((r['correct_by_deadline_lower_bound'],r['correct_by_deadline_upper_bound'],r['deadline_uncertain'],r['correct_after_deadline_lower_bound']),(0,8,8,2))
        self.assertEqual(r['cohort_deadline_status'],'confirmed_late')
    def test_exact_deadline_and_straddling_poll_are_different(self):
        ontime=deadline_bounds([poll(0,-.1,0),poll(10,1.9,2)],10)
        uncertain=deadline_bounds([poll(0,1.8,1.9),poll(10,1.95,2.05)],10)
        self.assertEqual(ontime['cohort_deadline_status'],'confirmed_on_time')
        self.assertEqual(ontime['correct_by_deadline_lower_bound'],10)
        self.assertEqual(uncertain['cohort_deadline_status'],'uncertain');self.assertEqual(uncertain['deadline_uncertain'],10)
    def test_all_late_completions_do_not_count_as_on_time_work(self):
        r=deadline_bounds([poll(0,-.1,0),poll(0,2.1,2.2),poll(10,36,36.1)],10)
        self.assertEqual(r['correct_by_deadline_upper_bound'],0);self.assertEqual(r['correct_after_deadline_lower_bound'],10)
    def test_unfinished_regressing_nonfinite_or_overlapping_observations_reject(self):
        for rows in ([poll(0,-.1,0),poll(9,3,3.1)], [poll(2,-.1,0),poll(10,3,3.1)],
                     [poll(0,-.1,0),poll(8,1,1.1),poll(7,2,2.1),poll(10,3,3.1)],
                     [poll(0,-.1,0),poll(10,float('nan'),3)], [poll(0,-.1,.2),poll(10,.1,.3)]):
            with self.subTest(rows=rows),self.assertRaises(AssertionError):deadline_bounds(rows,10)
    def test_old_evidence_cannot_retroactively_authorize_reset(self):
        self.assertFalse(declared_for_reset(Path('unused'),{'qualification_phase':'timeout_capture'},{}))
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'inputs/workloads/fnv06/outcome-accounting.json';path.parent.mkdir(parents=True)
            from scripts.fnv06_outcome_accounting import PROTOCOL
            path.write_bytes(PROTOCOL.read_bytes().replace(b'\r\n',b'\n'));_,digest=protocol()
            meta={'qualification_phase':'outcome_accounting_v1','outcome_protocol_sha256':digest}
            source={'files':{'workloads/fnv06/outcome-accounting.json':digest}}
            self.assertTrue(declared_for_reset(Path(d),meta,source))
            meta['outcome_protocol_sha256']='a'*64
            with self.assertRaises(AssertionError):declared_for_reset(Path(d),meta,source)
    def test_complete_failed_requests_reset_without_reclassifying_failure(self):
        for failed in (False,True):
            m,t,s,a=accounted_reset(failed);db=FakeDatabase(m,s)
            result=reset_owned_schema(db,meta=m,target=t,summary=s,audit=a,seal_sha256='d'*64,bound=lambda:None)
            self.assertTrue(db.committed);self.assertEqual(result['next_cell']['id'],'C02')
            self.assertEqual(s['status'],'failed' if failed else 'collected_pending_independent_audit')
    def test_incomplete_undeclared_or_promoted_failure_never_drops(self):
        for change in ('eligible','outcomes','protocol','unknown','count','request_success','status','collector','retrospective','warmup'):
            m,t,s,a=accounted_reset()
            if change=='eligible':a['reset_eligible']=False
            elif change=='outcomes':a['independent_outcomes_verified']=False
            elif change=='protocol':m['outcome_protocol_sha256']='f'*64
            elif change=='unknown':a['unverified_or_unfinished_completions']=1
            elif change=='count':a['request_accounting']['failed']=0
            elif change=='request_success':a['request_success']=True
            elif change=='status':a['status']='passed'
            elif change=='collector':s['status']='collected_pending_independent_audit'
            elif change=='retrospective':a['retrospective_accounting']=True
            else:s['cases_collected']=[]
            db=FakeDatabase(m,s)
            with self.subTest(change=change),self.assertRaises(ValueError):reset_owned_schema(db,meta=m,target=t,summary=s,audit=a,seal_sha256='d'*64,bound=lambda:None)
            self.assertFalse(db.dropped)
    def test_only_known_failed_job_marker_can_be_archived(self):
        m,t,s,a=accounted_reset();marker={'error_type':'AssertionError','reason':'qualification_job_failed'}
        self.assertTrue(require_failure_marker(m,s,a,marker))
        for bad in (None,{'error_type':'TimeoutError','reason':'window expired'},dict(marker,extra=True)):
            with self.assertRaises(ValueError):require_failure_marker(m,s,a,bad)
        m['qualification_phase']='timeout_capture'
        with self.assertRaises(ValueError):require_failure_marker(m,s,a,marker)
    def test_missing_evidence_cli_reports_unknown_not_zero_or_pass(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'missing';out=Path(d)/'audit'
            r=subprocess.run([sys.executable,str(ROOT/'scripts/audit_fnv06_outcomes.py'),'--source',str(source),'--output',str(out)],capture_output=True,text=True)
            self.assertEqual(r.returncode,1);v=json.loads((out/'summary.json').read_text())
            self.assertEqual(v['status'],'not_verified');self.assertFalse(v['reset_eligible']);self.assertIsNone(v['verified_correct_completions'])

if __name__=='__main__':unittest.main()
