import copy
from collections import Counter
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from benchlib.fnv06_campaign import (protocol,cell_protocol,require_binding,create_ledger,
    append_event,ledger,DESIGN,EXECUTION,OUTCOME_SHA,RESOURCE_SHA,STUDY)
from benchlib.fnv06_reset import require_previous,reset_owned_schema
from scripts.audit_fnv06_campaign import audit,comparisons
from tests.test_fnv06_outcomes import accounted_reset
from tests.test_fnv06_reset import FakeDatabase

ROOT=Path(__file__).resolve().parents[1]


def campaign_reset(failed=False):
    meta,target,summary,result=accounted_reset(failed);p=protocol();cell=p['cells'][0]
    meta.update(qualification_protocol='campaign',qualification_cell=cell,campaign_phase='population_campaign_v1',
        campaign_design_sha256=p['design_sha256'],campaign_execution_sha256=p['execution_sha256'],
        resource_phase='workload_resources_v1',resource_protocol_sha256=RESOURCE_SHA)
    summary.update(study_id=STUDY,cases_collected=['C001W']+([] if failed else ['C001M']))
    if failed:summary['failed_observation']['id']='C001M'
    result.update(study_id=STUDY,qualification_cell=cell,campaign_design_sha256=p['design_sha256'],
        campaign_execution_sha256=p['execution_sha256'])
    return meta,target,summary,result


def audited(cell,failed_requests=0,headroom=True):
    return {'cell':cell,'seal_sha256':'b'*64,'outcome_audit_sha256':'c'*64,'resource_audit_sha256':'d'*64,
            'failed_requests':failed_requests,'outcomes_verified':True,'resources_verified':True,
            'generator_headroom_passed':headroom}


class CampaignTests(unittest.TestCase):
    def test_exact_matrix_orders_denominators_and_unique_comparisons(self):
        p=protocol();cells=p['cells']
        self.assertEqual(len(cells),45)
        self.assertEqual([c['id'] for c in cells],['C'+str(i).zfill(3) for i in range(1,46)])
        self.assertEqual(Counter((c['kind'],c['population']) for c in cells),Counter({(k,n):5 for k in ('message','signal','batch') for n in (10,100,1000)}))
        self.assertEqual([(c['kind'],c['population']) for c in cells[9:12]],[('signal',100),('signal',1000),('signal',10)])
        self.assertEqual(p['denominators'],{'measured_recipients':sum(c['population'] for c in cells),
            'warmup_recipients':4500,'waiting_controls':180,'total_workflow_instances':21330})
    def test_every_cell_has_same_width_and_same_pattern_warmup(self):
        p=protocol()
        for c in p['cells']:
            selected=cell_protocol(p,c['id'])
            self.assertEqual(selected,cell_protocol(selected,c['id']))
            self.assertEqual(selected['observations'],[{'id':c['id']+'W','kind':c['kind'],'population':100},
                {'id':c['id']+'M','kind':c['kind'],'population':c['population']}])
            self.assertEqual(len(c['id']),4)
    def test_altered_population_order_or_controls_reject(self):
        for change in ('order','count','controls','cell'):
            p=protocol();cell='C001'
            if change=='order':p['cells'][0],p['cells'][1]=p['cells'][1],p['cells'][0]
            elif change=='count':p['cells'].pop()
            elif change=='controls':p['message_client_ceiling']=8
            else:cell='C046'
            with self.subTest(change=change),self.assertRaises(ValueError):cell_protocol(p,cell)
    def test_changed_original_design_or_execution_contract_reject(self):
        for name in (DESIGN,EXECUTION):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as d:
                root=Path(d)
                for source in (ROOT/'workloads/fnv06').glob('*.json'):
                    target=root/'workloads/fnv06'/source.name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
                value=json.loads((root/name).read_text());value['execution_enabled']=False if name==EXECUTION else True
                (root/name).write_text(json.dumps(value))
                with self.assertRaises(ValueError):protocol(root)
    def test_missing_phase_wrong_hash_qualification_or_fault_cannot_be_campaign(self):
        for key,value in [('campaign_phase',None),('campaign_execution_sha256','f'*64),('campaign_design_sha256','e'*64),
                          ('qualification_protocol','repeat'),('resource_protocol_sha256','a'*64),('controlled_fault','timeout_capture_C01')]:
            m,*_=campaign_reset();m[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):require_binding(m)
    def test_audited_campaign_resets_to_declared_next_population_with_failures_retained(self):
        for failed in (False,True):
            data=campaign_reset(failed);m,t,s,a=data;db=FakeDatabase(m,s)
            result=reset_owned_schema(db,meta=m,target=t,summary=s,audit=a,seal_sha256='d'*64,bound=lambda:None)
            self.assertTrue(db.committed);self.assertEqual(result['next_cell'],protocol()['cells'][1])
            self.assertEqual(result['next_cell']['population'],100)
            self.assertEqual(a['request_accounting']['failed'],4 if failed else 0)
    def test_unknown_outcomes_or_wrong_campaign_binding_never_drop(self):
        for change in ('unknown','study','binding','last'):
            m,t,s,a=campaign_reset()
            if change=='unknown':a['unverified_or_unfinished_completions']=1
            elif change=='study':a['study_id']='FNV-06-REPEAT-QUAL'
            elif change=='binding':a['campaign_execution_sha256']='f'*64
            else:m['qualification_cell']=protocol()['cells'][-1]
            db=FakeDatabase(m,s)
            with self.subTest(change=change),self.assertRaises(ValueError):reset_owned_schema(db,meta=m,target=t,summary=s,audit=a,seal_sha256='d'*64,bound=lambda:None)
            self.assertFalse(db.dropped)
    def make_ledger(self,root):
        return create_ledger(Path(root)/'ledger',run_id='a'*32,source_commit='f'*40)
    def test_all_unattempted_cells_are_in_partial_report(self):
        with tempfile.TemporaryDirectory() as d:
            self.make_ledger(d);r=audit(d)
            self.assertEqual(r['cells_by_status'],{'unattempted':45});self.assertEqual(len(r['comparisons']),30)
            self.assertFalse(r['performance_comparison_complete'])
            self.assertEqual(r['verified_denominators']['total_workflow_instances'],0)
    def test_failure_leaves_remaining_cells_unattempted_and_forbids_advancement(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.make_ledger(d);folder=Path(d)/'ledger';cell=s['cells'][0]['cell']
            append_event(folder,'started',{'cell':cell})
            append_event(folder,'failed',{'cell':cell,'reason':'final_outcomes_not_verified'})
            with self.assertRaises(ValueError):append_event(folder,'started',{'cell':s['cells'][1]['cell'],'reset_receipt_sha256':'b'*64})
            append_event(folder,'closed',{'reason':'incomplete_outcomes'})
            report=audit(d);self.assertEqual(report['cells_by_status'],{'failed':1,'unattempted':44})
    def test_silent_retry_reorder_overlap_and_missing_reset_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.make_ledger(d);folder=Path(d)/'ledger';first,second=[r['cell'] for r in s['cells'][:2]]
            with self.assertRaises(ValueError):append_event(folder,'started',{'cell':second})
            append_event(folder,'started',{'cell':first})
            with self.assertRaises(ValueError):append_event(folder,'started',{'cell':second})
            append_event(folder,'audited',audited(first,4,False))
            with self.assertRaises(ValueError):append_event(folder,'started',{'cell':first})
            with self.assertRaises(ValueError):append_event(folder,'started',{'cell':second})
            s=append_event(folder,'started',{'cell':second,'reset_receipt_sha256':'e'*64})
            self.assertEqual(s['cells'][0]['result']['failed_requests'],4)
            self.assertFalse(s['cells'][0]['result']['generator_headroom_passed'])
    def test_incomplete_result_or_active_close_cannot_hide_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.make_ledger(d);folder=Path(d)/'ledger';cell=s['cells'][0]['cell'];append_event(folder,'started',{'cell':cell})
            for key,value in [('resources_verified',False),('outcomes_verified',False),('seal_sha256',''),('failed_requests',-1)]:
                result=audited(cell);result[key]=value
                with self.subTest(key=key),self.assertRaises(ValueError):append_event(folder,'audited',result)
            with self.assertRaises(ValueError):append_event(folder,'closed',{'reason':'done'})
            self.assertEqual(ledger(folder)['counts']['running'],1)
    def test_removed_or_reordered_event_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.make_ledger(d);folder=Path(d)/'ledger';cell=s['cells'][0]['cell']
            append_event(folder,'started',{'cell':cell});append_event(folder,'failed',{'cell':cell,'reason':'unknown'})
            first=folder/'events/0001.json';first.unlink()
            with self.assertRaises(ValueError):ledger(folder)
    def test_all_45_attempts_accounted_without_replacement(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.make_ledger(d);folder=Path(d)/'ledger'
            for index,r in enumerate(s['cells']):
                data={'cell':r['cell']}
                if index:data['reset_receipt_sha256']='b'*64
                append_event(folder,'started',data);append_event(folder,'audited',audited(r['cell']))
            s=append_event(folder,'closed',{'reason':'all_planned_cells_accounted'})
            self.assertEqual(s['counts'],{'unattempted':0,'running':0,'audited':45,'failed':0})
            with self.assertRaises(ValueError):append_event(folder,'started',{'cell':s['cells'][0]['cell']})
    def test_comparisons_are_within_repeat_pattern_and_keep_constraints(self):
        rows=[{'cell':c,'status':'audited','generator_headroom_passed':True,'request_failed':0,
               'measured':{'all_recipients_completion_interval_s':[c['population']/10,c['population']/10+.1]}}
              for c in protocol()['cells']]
        result=comparisons(rows)
        self.assertEqual(len(result),30)
        self.assertEqual(result[0]['completion_ratio_interval'],[10/1.1,10.1])
        rows[0]['generator_headroom_passed']=False;result=comparisons(rows)
        self.assertEqual(sum(r['resource_qualified'] for r in result),28)
        self.assertIsNone(result[0]['completion_ratio_interval'])
        rows[0]['status']='unattempted';self.assertIsNone(comparisons(rows)[0]['request_failures'])


if __name__=='__main__':unittest.main()
