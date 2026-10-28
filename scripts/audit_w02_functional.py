#!/usr/bin/env python3
"""Recheck sealed W02 functional evidence, original SQLite state and case coverage."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from benchlib.workflow_corpus import resolve_sensitivity
from scripts.run_w02_functional import require_outcome


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def verify_inventory(folder):
    inventory=read(folder/'checksums.json')
    assert inventory['algorithm']=='sha256' and inventory['files']
    for name,wanted in inventory['files'].items():
        path=(folder/name).resolve()
        assert path.is_relative_to(folder) and path.is_file(), 'invalid_sealed_path'
        assert sha(path)==wanted, 'sealed_file_changed: '+name
    actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() and p!=folder/'checksums.json'}
    assert actual==set(inventory['files']), 'unexpected_or_missing_archive_file'
    return inventory


def audit(folder):
    folder=Path(folder).resolve()
    inventory=verify_inventory(folder)
    manifest,summary=read(folder/'manifest.json'),read(folder/'summary.json')
    assert manifest['scope']=='LOCAL FUNCTIONAL VALIDATION - NOT OCI PERFORMANCE'
    assert manifest['native_model_calls']==0 and manifest['cloud_resources_accessed'] is False
    assert summary['status']=='passed' and summary['owned_engine_stopped'] and not summary['cleanup_errors']
    assert summary['run_id']==manifest['run_id']
    for name,wanted in manifest['source_sha256'].items():
        assert inventory['files']['inputs/'+name]==wanted, 'source_inventory_mismatch'
    case_file=manifest.get('case_file','inputs/workloads/w02/cases.json')
    assert case_file in inventory['files'], 'unsealed_corpus'
    corpus=read(folder/case_file)
    if 'case_file_sha256' in manifest:assert sha(folder/case_file)==manifest['case_file_sha256']
    if 'protocol_file' in manifest:
        assert manifest['protocol_file'] in inventory['files']
        assert sha(folder/manifest['protocol_file'])==manifest['protocol_sha256']
        resolved=resolve_sensitivity(read(folder/manifest['protocol_file']),read(folder/'inputs/workloads/w02/cases.json'))
        assert corpus==resolved, 'resolved_grid_does_not_match_protocol'
        assert manifest['arm_order_by_case']==corpus['arm_order_by_case']
    cases=corpus['cases'];by_id={c['case_id']:c for c in cases}
    assert len(by_id)==len(cases) and manifest['case_order']==list(by_id)
    orders=manifest.get('arm_order_by_case',{c['case_id']:['sequential','parallel'] for c in cases})
    expected_order=[(c['case_id'],a) for c in cases for a in orders[c['case_id']]]
    assert all(sorted(orders[c['case_id']])==['parallel','sequential'] for c in cases)
    assert [(r['case_id'],r['arm']) for r in summary['cases']]==expected_order, 'incomplete_or_reordered_cases'
    database=folder/'study.sqlite'
    for suffix in ('-wal','-journal'):
        sidecar=Path(str(database)+suffix)
        assert not sidecar.exists() or sidecar.stat().st_size==0, 'uncheckpointed_store'
    profiles=defaultdict(lambda:{'executions':0,'effects':0,'outcomes':Counter(),'repeated_calls':0,
                                 'parallel_executions':0,'parallel_receipt_overlaps':0})
    expected_keys=set();expected_pids=set();faults=set();total_effects=0;repeats=0;overlap_probes=0
    with sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1',uri=True) as db:
        for result in summary['cases']:
            case=by_id[result['case_id']];arm=result['arm'];key=result['case_key'];pid=result['process_id']
            assert key not in expected_keys and pid not in expected_pids
            expected_keys.add(key);expected_pids.add(pid)
            name=arm+'-'+case['case_id']
            record=read(folder/(name+'.json'));raw=read(folder/(name+'.raw.json'))
            assert record['case']==raw['case']==case and record['result']==result
            for field in ('history','activities','worker_records'):assert raw[field]==record[field]
            before,after=record['backend_before_replay'],record['backend_after_replay']
            assert raw['backend']==before
            require_outcome(case,before,record['history'],record['activities'])
            assert before['receipts']==after['receipts'] and before['effects']==after['effects']
            assert before['process_id']==pid and before['case_key']==key
            canonical=json.dumps(case,sort_keys=True,separators=(',',':'),allow_nan=False)
            fingerprint=hashlib.sha256(canonical.encode()).hexdigest()
            assert db.execute('SELECT process_id,body,fingerprint FROM cases WHERE key=?',(key,)).fetchone()==(pid,canonical,fingerprint)
            receipts={op:json.loads(body) for op,body in db.execute('SELECT operation,body FROM receipts WHERE key=?',(key,))}
            assert receipts==before['receipts'], 'original_store_receipts_changed'
            effects=[{'payment_id':p,'amount_minor':a,'beneficiary':b} for p,a,b in db.execute('SELECT payment_id,amount_minor,beneficiary FROM effects WHERE key=?',(key,))]
            assert effects==before['effects'], 'original_store_effects_changed'
            assert result['status']=='passed' and result['outcome']==case['expected_outcome']
            assert result['effects']==len(effects) and result['input_sha256']==fingerprint
            assert result['premature_effect_refused'] and result['replay_calls']==6
            for op in ('kyc','risk','settle'):
                a=sum(x['operation']==op and x['event']=='replay' for x in before['audit'])
                b=sum(x['operation']==op and x['event']=='replay' for x in after['audit'])
                assert b-a==2, 'repeated_calls_not_exercised'
            kyc,risk=receipts['kyc'],receipts['risk']
            overlap=min(kyc['finished_ns'],risk['finished_ns'])>max(kyc['started_ns'],risk['started_ns'])
            assert result['checks_overlap']==overlap
            if arm=='sequential':assert risk['started_ns']>=kyc['finished_ns']
            if arm=='parallel' and case.get('require_parallel_overlap',case['case_id'] in ('approve','concurrent_completion')):
                assert overlap, 'declared_overlap_probe_failed'
                overlap_probes+=1
            for op,schedule in case['schedule'].items():
                event={'before_once':'failure_before_receipt','after_once':'response_lost_after_receipt'}.get(schedule['fault'])
                if event:
                    assert any(x['operation']==op and x['event']==event for x in before['audit'])
                    faults.add((key,op,schedule['fault']))
            p=profiles[case.get('dependency_profile','original')]
            p['executions']+=1;p['effects']+=len(effects);p['outcomes'][case['expected_outcome']]+=1;p['repeated_calls']+=6
            if arm=='parallel':
                p['parallel_executions']+=1;p['parallel_receipt_overlaps']+=int(overlap)
            total_effects+=len(effects);repeats+=6
        assert {row[0] for row in db.execute('SELECT key FROM cases')}==expected_keys
        assert db.execute('SELECT count(*) FROM receipts').fetchone()[0]==len(expected_keys)*3
        assert db.execute('SELECT count(*) FROM effects').fetchone()[0]==total_effects
        assert set(db.execute('SELECT key,operation,fault FROM faults'))==faults
    assert summary['functional_cases_passed']==len(expected_order) and summary['expected_effects']==total_effects
    assert len(summary['replay_probes'])==2 and {p['arm'] for p in summary['replay_probes']}=={'sequential','parallel'}
    assert all(p['status']=='blocked_as_expected' and p['original_effects_unchanged'] and p['cleanup']=='deleted_owned_replay_instance' for p in summary['replay_probes'])
    assert all(summary[k]==0 for k in ('active_instances','external_tasks','incidents'))
    if (folder/'final-engine-counts.json').exists():
        assert read(folder/'final-engine-counts.json')=={'active_instances':0,'external_tasks':0,'incidents':0}
    assert verify_inventory(folder)==inventory
    return {'status':'passed','scope':manifest['scope'],'study_id':manifest.get('study_id','w02'),
            'source_manifest_sha256':sha(folder/'manifest.json'),'source_checksums_sha256':sha(folder/'checksums.json'),
            'auditor_sha256':sha(Path(__file__)),'sealed_files_verified':len(inventory['files']),
            'unique_scenarios':len(cases),'functional_executions':len(expected_order),'expected_effects':total_effects,
            'original_receipts_verified':len(expected_keys)*3,'repeated_calls':repeats,
            'premature_settlement_denials':len(expected_keys),'cross_process_replays_blocked':2,
            'injected_once_faults_verified':len(faults),'required_parallel_overlap_probes':overlap_probes,
            'profiles':dict(profiles),'owned_engine_stopped':True,'remaining_engine_work':0,
            'source_unchanged':True,'performance_claim':None,'representative_customer_distribution':False,
            'native_model_calls':0,'cloud_resources_accessed':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();source=args.source.resolve();output=args.output.resolve()
    if output.exists() or output.is_relative_to(source):p.error('Use a new audit directory outside the sealed source.')
    result=audit(source);output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
