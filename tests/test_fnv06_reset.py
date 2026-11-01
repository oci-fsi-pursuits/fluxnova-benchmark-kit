from contextlib import contextmanager
import copy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from benchlib.fnv06_repeat import cell_protocol
from benchlib.fnv06_reset import require_previous,reset_owned_schema,OUTSIDE_QUERIES,STORAGE_QUERY


def fixture():
    run='a'*32;now=datetime.now(timezone.utc)
    meta={'study_id':'FNV-06','run_id':run,'stage':'/opt/fluxnova-bench/workspace/fnv06-'+run,
        'source_id':'source-db','target_id':'trial-db','source_db_host':'source.postgresql.us-phoenix-1.oci.oraclecloud.com',
        'connection':{'primary_db_endpoint':{'fqdn':'trial.postgresql.us-phoenix-1.oci.oraclecloud.com'},'ca_certificate':'fixture-ca'},
        'images':{'engine':'example/engine@sha256:'+'b'*64,'services':'example/services@sha256:'+'c'*64},
        'budget_limit_usd':400,'prepared_at':(now-timedelta(minutes=1)).isoformat(),'stop_before_utc':(now+timedelta(hours=1)).isoformat(),
        'admin_username':'fixtureadmin','qualification_protocol':'repeat','qualification_cell':{'id':'C01','kind':'message','population':10}}
    target={'id':'trial-db','display_name':'fluxnova-fnv06-'+run[:12],'instance_count':1,'source':{'source_type':'NONE'},'lifecycle_state':'ACTIVE',
        'freeform_tags':{'managed_by':'fluxnova-benchmarks','fnv06_run_id':run,'temporary_comparison':'true','comparison_lane':'fnv06'}}
    summary={'study_id':'FNV-06-REPEAT-QUAL','run_id':run,'status':'collected_pending_independent_audit',
        'owned_engine_stopped':True,'cleanup_errors':[],'owned_process_ids':['pid'+str(i) for i in range(114)]}
    audit={'study_id':'FNV-06-REPEAT-QUAL','status':'passed','qualification_cell':meta['qualification_cell'],
        'workflow_instances':114,'expected_completions':110,'unchanged_waiting_controls':4,'cloud_resources_accessed':True,
        'database_audit':{'tables_reconciled':9},'warmup':{'status':'passed'},'source_checksums_sha256':'d'*64}
    return meta,target,summary,audit


class FakeDatabase:
    def __init__(self,meta,summary):
        self.info=SimpleNamespace(host=meta['connection']['primary_db_endpoint']['fqdn'],dbname='postgres',port=5432,transaction_status=0)
        self.autocommit=True;self.meta=meta;self.summary=summary;self.queries=[];self.dropped=False;self.committed=False;self.rolled_back=False
        self.active=False;self.foreign_history=False;self.outside_change=False;self.remaining_work=False
        self.toast=False;self.foreign_toast=False;self.bad_toast_owner=False;self.remaining_toast=False
    @contextmanager
    def transaction(self):
        try:yield
        except BaseException:self.rolled_back=True;raise
        else:self.committed=True
    def execute(self,query,params=None):
        self.queries.append(query);role='fnv06_'+self.meta['run_id'][:12]
        rows=[]
        if query.startswith('SELECT current_database'):rows=[('postgres',self.meta['admin_username'],True)]
        elif query.startswith('SELECT pg_get_userbyid'):rows=[(self.meta['admin_username'],)]
        elif query.startswith('SELECT rolname'):rows=[(role,False,False,False)]
        elif query.startswith('SELECT count(*) FROM pg_stat_activity'):rows=[(int(self.active),)]
        elif query.startswith('SELECT id_,end_time_'):
            rows=[(pid,'completed') for pid in self.summary['owned_process_ids']]
            if self.foreign_history:rows[0]=('foreign-process','completed')
        elif query.startswith('SELECT count(*) FROM fluxnova.'):rows=[(int(self.remaining_work),)]
        elif query==STORAGE_QUERY:
            rows=[(100,101,5,6 if self.bad_toast_owner else 5,'pg_toast','t',102,5,'pg_toast','i')] if self.toast else []
        elif query=='SELECT count(*) FROM pg_class WHERE oid=ANY(%s)':rows=[(int(self.remaining_toast),)]
        elif query in OUTSIDE_QUERIES.values():
            name=next(k for k,v in OUTSIDE_QUERIES.items() if v==query)
            rows=[('outside-changed' if self.dropped and self.outside_change else 'outside-stable',0,0,0,0,0,0)]
            if name=='relations' and self.toast:
                rows.extend([(101,1,'toast',0,0),(102,1,'toast-index',0,0)] if not self.dropped else [])
                rows.extend([(201,1,'foreign-toast',0,0)] if not self.dropped or not self.foreign_toast else [])
        elif query.startswith('DROP '):self.dropped=True
        elif query.startswith('SELECT count(*) FROM pg_namespace') or query.startswith('SELECT count(*) FROM pg_roles'):rows=[(0 if self.dropped else 1,)]
        elif not query.startswith(('SET ','LOCK ')):raise AssertionError('Unexpected SQL: '+query)
        return SimpleNamespace(fetchone=lambda:rows[0],fetchall=lambda:rows)


class ResetTests(unittest.TestCase):
    def call(self,db,data,bound=lambda:None):
        m,t,s,a=data;return reset_owned_schema(db,meta=m,target=t,summary=s,audit=a,seal_sha256='d'*64,bound=bound)
    def test_audited_owned_state_resets_and_advances_one_fixed_cell(self):
        data=fixture();db=FakeDatabase(data[0],data[2]);result=self.call(db,data)
        self.assertTrue(db.committed);self.assertTrue(result['outside_schema_catalogs_unchanged'])
        self.assertEqual(result['next_cell'],{'id':'C02','kind':'message','population':1000})
        self.assertEqual([q for q in db.queries if q.startswith('DROP ')],['DROP SCHEMA fluxnova CASCADE','DROP ROLE "fnv06_aaaaaaaaaaaa"'])
    def test_reference_wrong_owner_missing_audit_and_wrong_seal_never_drop(self):
        for kind in ('reference','tag','inactive','audit','seal','live','duplicate'):
            data=fixture();m,t,s,a=data
            if kind=='reference':m['target_id']=m['source_id']
            elif kind=='tag':t['freeform_tags']['fnv06_run_id']='b'*32
            elif kind=='inactive':t['lifecycle_state']='DELETING'
            elif kind=='audit':a['status']='failed'
            elif kind=='seal':a['source_checksums_sha256']='e'*64
            elif kind=='live':s['owned_engine_stopped']=False
            else:s['owned_process_ids'][0]=s['owned_process_ids'][1]
            db=FakeDatabase(m,s)
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.call(db,data)
            self.assertFalse(any(q.startswith('DROP ') for q in db.queries))
    def test_wrong_connection_sessions_work_or_foreign_history_never_drop(self):
        for kind in ('host','active','remaining_work','foreign_history'):
            data=fixture();db=FakeDatabase(data[0],data[2])
            if kind=='host':db.info.host=data[0]['source_db_host']
            else:setattr(db,kind,True)
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.call(db,data)
            self.assertFalse(any(q.startswith('DROP ') for q in db.queries))
    def test_cross_schema_effect_is_detected_inside_transaction(self):
        data=fixture();db=FakeDatabase(data[0],data[2]);db.outside_change=True
        with self.assertRaisesRegex(ValueError,'outside'):self.call(db,data)
        self.assertTrue(db.rolled_back);self.assertFalse(db.committed)
    def test_owned_toast_storage_is_removed_but_other_toast_stays_protected(self):
        data=fixture();db=FakeDatabase(data[0],data[2]);db.toast=True
        result=self.call(db,data);self.assertTrue(db.committed)
        self.assertEqual(result['owned_toast_relations_removed'],2)
        db=FakeDatabase(data[0],data[2]);db.toast=True;db.foreign_toast=True
        with self.assertRaisesRegex(ValueError,'outside'):self.call(db,data)
        self.assertTrue(db.rolled_back);self.assertFalse(db.committed)
    def test_wrong_toast_owner_never_drops_and_remaining_storage_rolls_back(self):
        data=fixture();db=FakeDatabase(data[0],data[2]);db.toast=True;db.bad_toast_owner=True
        with self.assertRaisesRegex(ValueError,'TOAST'):self.call(db,data)
        self.assertFalse(db.dropped)
        db=FakeDatabase(data[0],data[2]);db.toast=True;db.remaining_toast=True
        with self.assertRaisesRegex(ValueError,'TOAST'):self.call(db,data)
        self.assertTrue(db.rolled_back);self.assertFalse(db.committed)
    def test_window_expiry_after_drop_rolls_back(self):
        data=fixture();db=FakeDatabase(data[0],data[2])
        def bound():
            if db.dropped:raise TimeoutError('expired')
        with self.assertRaises(TimeoutError):self.call(db,data,bound)
        self.assertTrue(db.rolled_back);self.assertFalse(db.committed)
    def test_fixed_warmup_selection_rejects_reordered_cells_and_changed_controls(self):
        corpus=json.loads((Path(__file__).resolve().parents[1]/'workloads/fnv06/repeat-qualification.json').read_text())
        self.assertEqual(cell_protocol(corpus,'C02')['observations'],[{'id':'C02W','kind':'message','population':100},{'id':'C02M','kind':'message','population':1000}])
        for key,value in [('cells',list(reversed(corpus['cells']))),('warmup_recipients',99),('completion_poll_ms',500)]:
            altered=copy.deepcopy(corpus);altered[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):cell_protocol(altered,'C02')


if __name__=='__main__':unittest.main()
