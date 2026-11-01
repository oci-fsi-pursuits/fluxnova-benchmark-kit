import copy,hashlib,json
from datetime import datetime,timezone
from pathlib import Path
import tempfile
import unittest

from scripts.audit_fnv06_load_qualification import check_driver
from scripts.fnv06_timeout_evidence import verify_control,LOCK_QUERY
from tests.test_fnv06_load import driver_fixture

ROOT=Path(__file__).resolve().parents[1]
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v))
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def seal(p):save(p/'checksums.json',{'algorithm':'sha256','files':{f.relative_to(p).as_posix():digest(f) for f in p.rglob('*') if f.is_file() and f.name!='checksums.json'}})

def timed_driver():
    fixture,actions,summary,wanted=driver_fixture(population=10,scale=True)
    fixture.update(study_id='FNV-06-REPEAT-QUAL',observation_id='C01M');summary['observation_id']='C01M'
    for r in actions:
        r.update(study_id=fixture['study_id'],observation_id='C01M')
        if r['index']<4:r.update(status=0,error_code=1050,response=None,elapsed_ms=30000,finished_epoch_ms=r['started_epoch_ms']+30000)
        else:r.update(started_epoch_ms=r['started_epoch_ms']+36000,finished_epoch_ms=r['finished_epoch_ms']+36000)
    return fixture,actions,summary,wanted

def control_fixture(base):
    c=base/'control';cell=base/'cell';c.mkdir();cell.mkdir();run='a'*32;source='b'*40
    iso=lambda ms:datetime.fromtimestamp(ms/1000,timezone.utc).isoformat()
    release=1_000_000
    (c/'operator.py').write_text('# fixture operator\n');operator=digest(c/'operator.py')
    protocol=json.loads((ROOT/'workloads/fnv06/timeout-capture.json').read_text())
    pp=cell/'inputs/workloads/fnv06/timeout-capture.json';save(pp,protocol)
    fixture=timed_driver()[0];fixture['release_epoch_ms']=release
    save(c/'input.json',fixture);save(cell/'observations/C01M/driver-input.json',fixture)
    identity={'qualification_protocol':'repeat','qualification_phase':'timeout_capture','qualification_cell':protocol['cell'],
        'source_commit':source,'run_id':run,'target_id':'owned-target','source_id':'protected-reference',
        'admin_username':'fixtureadmin','source_db_host':'reference.example','connection':{'primary_db_endpoint':{'fqdn':'owned.example'}},
        'fault_operator_sha256':operator,'fault_protocol_sha256':digest(pp),'prepared_at':iso(release-20000),'stop_before_utc':iso(release+600000)}
    save(cell/'identity.json',identity);save(cell/'manifest.json',{'source_commit':source})
    save(cell/'source-manifest.json',{'commit':source,'files':{'workloads/fnv06/timeout-capture.json':digest(pp)}})
    target={'id':'owned-target','lifecycle_state':'ACTIVE','instance_count':1,'display_name':'fluxnova-fnv06-'+run[:12],
        'source':{'source_type':'NONE'},'freeform_tags':{'managed_by':'fluxnova-benchmarks','fnv06_run_id':run,'temporary_comparison':'true','comparison_lane':'fnv06'}}
    save(c/'request.json',{'source_commit':source,'operator_sha256':operator,'issued_at':iso(release-10000),'target':target})
    save(c/'ready.json',{'at':iso(release-9000),'run_id':run,'source_commit':source})
    role='fnv06_'+run[:12]
    receipt={'status':'released','source_commit':source,'run_id':run,'target_id':'owned-target','source_id':'protected-reference',
        'case':'C01M','table':'fluxnova.act_ru_event_subscr','mode':'SHARE','database':'postgres','tls':True,
        'database_user':'fixtureadmin','host':'owned.example','table_owner':role,'schema_owner':'fixtureadmin','table_oid':123,'backend_pid':99,
        'idle_transaction_timeout_s':45,'transaction_ended':True,'lock_absent_after_commit':True,'driver_input_sha256':digest(c/'input.json'),
        'release_epoch_ms':release,'lock_acquired_epoch_ms':release-2000,'lock_released_epoch_ms':release+36000,
        'lock_acquired_monotonic_s':998,'lock_released_monotonic_s':1036,'after_release_rows':[]}
    save(c/'receipt.json',receipt)
    samples=[{'query':LOCK_QUERY,'table_oid':123,'epoch_ms':release-2000+i*1000,'monotonic_s':998+i,
        'rows':[[99,'fixtureadmin','ShareLock',True]]+([[n,role,'RowExclusiveLock',False] for n in (1,2,3,4)] if 3<=i<=30 else [])} for i in range(38)]
    (c/'samples.ndjson').write_text(''.join(json.dumps(r)+'\n' for r in samples));seal(c)
    return c,cell


class TimeoutEvidenceTests(unittest.TestCase):
    def check_driver(self,data,**kwargs):
        f,a,s,w=data
        return check_driver(f,a,s,expected=w,engine_url='http://127.0.0.1:8080/engine-rest',**kwargs)
    def test_controlled_timeout_counts_failures_separately_from_acknowledgments(self):
        result=self.check_driver(timed_driver(),timeout_indices=(0,1,2,3))
        self.assertEqual(result['request_timeouts'],4);self.assertEqual(result['successful_requests'],6)
        self.assertEqual(result['http_ack_max_ms'],5)
    def test_ordinary_qualification_still_rejects_the_same_failed_requests(self):
        with self.assertRaises(AssertionError):self.check_driver(timed_driver())
    def test_timeout_profile_cannot_accept_changed_case_missing_timeout_or_overlapping_clients(self):
        for change in ('case','early_timeout','successful','wrong_code','response','duplicate_vu','overlap'):
            data=timed_driver();f,a,_,_=data
            if change=='case':f['observation_id']='C02M'
            elif change=='early_timeout':a[0].update(elapsed_ms=20000,finished_epoch_ms=a[0]['started_epoch_ms']+20000)
            elif change=='successful':a[0].update(status=200,error_code=0)
            elif change=='wrong_code':a[0]['error_code']=1200
            elif change=='response':a[0]['response']={}
            elif change=='duplicate_vu':a[1]['vu']=1
            else:a[4].update(started_epoch_ms=2000,finished_epoch_ms=2005)
            with self.subTest(change=change),self.assertRaises(AssertionError):self.check_driver(data,timeout_indices=(0,1,2,3))
    def test_owned_control_requires_observed_blocking_and_release(self):
        with tempfile.TemporaryDirectory() as d:
            c,cell=control_fixture(Path(d));result=verify_control(c,cell)
            self.assertTrue(result['four_owned_waiters_observed'] and result['transaction_released'])
            self.assertEqual(result['actual_lock_release_elapsed_s'],36)
    def test_outcome_reset_control_requires_explicit_phase_and_fault_declaration(self):
        with tempfile.TemporaryDirectory() as d:
            c,cell=control_fixture(Path(d));path=cell/'identity.json';identity=json.loads(path.read_text())
            with self.assertRaises(AssertionError):verify_control(c,cell,for_outcome_reset=True)
            identity.update(qualification_phase='outcome_accounting_v1',controlled_fault='timeout_capture_C01');save(path,identity)
            self.assertTrue(verify_control(c,cell,for_outcome_reset=True)['transaction_released'])
            with self.assertRaises(AssertionError):verify_control(c,cell)
            identity['controlled_fault']='undeclared';save(path,identity)
            with self.assertRaises(AssertionError):verify_control(c,cell,for_outcome_reset=True)
    def test_internally_resealed_wrong_target_clock_or_contention_cannot_pass(self):
        for change in ('target','stale','release','owner','missing_waiters','clock','gap','fixture','lock_remains'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as d:
                c,cell=control_fixture(Path(d));p=c/'receipt.json';receipt=json.loads(p.read_text())
                if change in ('target','stale'):
                    q=c/'request.json';v=json.loads(q.read_text())
                    if change=='target':v['target']['id']='protected-reference'
                    else:v['issued_at']='1970-01-01T00:00:00+00:00'
                    save(q,v)
                elif change=='release':receipt['lock_released_epoch_ms']-=5000;save(p,receipt)
                elif change=='owner':receipt['table_owner']='foreign';save(p,receipt)
                elif change=='lock_remains':receipt['after_release_rows']=[[99,'fixtureadmin','ShareLock',True]];save(p,receipt)
                elif change=='fixture':
                    q=c/'input.json';v=json.loads(q.read_text());v['population']=11;save(q,v)
                else:
                    q=c/'samples.ndjson';rows=[json.loads(l) for l in q.read_text().splitlines()]
                    if change=='missing_waiters':
                        for r in rows:r['rows']=r['rows'][:1]
                    elif change=='clock':rows[4]['epoch_ms']+=1000
                    else:rows=rows[:4]+rows[7:]
                    q.write_text(''.join(json.dumps(r)+'\n' for r in rows))
                seal(c)
                with self.assertRaises(AssertionError):verify_control(c,cell)
    def test_operator_or_protocol_change_cannot_borrow_a_valid_control(self):
        for change in ('operator','protocol'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as d:
                c,cell=control_fixture(Path(d))
                if change=='operator':(c/'operator.py').write_text('# changed operator\n');seal(c)
                else:
                    p=cell/'inputs/workloads/fnv06/timeout-capture.json';v=json.loads(p.read_text());v['request_timeout_s']=60;save(p,v)
                with self.assertRaises(AssertionError):verify_control(c,cell)

if __name__=='__main__':unittest.main()
