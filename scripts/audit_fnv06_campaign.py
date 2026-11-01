#!/usr/bin/env python3
"""Audit every declared attempt in a saved FNV-06 population campaign."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchlib.fnv06_campaign import ledger, digest, canonical, protocol, STUDY
from scripts.audit_fnv06_load_qualification import audit as audit_outcomes
from scripts.audit_fnv06_telemetry import audit as audit_resources


def read(path): return json.loads(Path(path).read_text())
def sha(path): return digest(Path(path).read_bytes())


def comparisons(rows):
    """Keep every planned pair, including pairs with unusable/missing members."""
    result=[]
    for repeat in range(1,6):
        for kind in ('message','signal','batch'):
            group={r['cell']['population']:r for r in rows if r['cell']['repeat']==repeat and r['cell']['kind']==kind}
            for population in (100,1000):
                control,changed=group[10],group[population]
                eligible=all(r['status']=='audited' and r['generator_headroom_passed'] for r in (control,changed))
                item={'repeat':repeat,'kind':kind,'control_cell':control['cell']['id'],
                      'changed_cell':changed['cell']['id'],'control_population':10,'changed_population':population,
                      'resource_qualified':eligible,'completion_ratio_interval':None,
                      'request_failures':sum(r.get('request_failed',0) for r in (control,changed)) if all(r['status']=='audited' for r in (control,changed)) else None}
                if eligible:
                    a,b=control['measured']['all_recipients_completion_interval_s'],changed['measured']['all_recipients_completion_interval_s']
                    # No invented point estimate or individual-workflow p95/p99.
                    item['completion_ratio_interval']=[b[0]/a[1],b[1]/a[0] if a[0]>0 else None]
                result.append(item)
    return result


def audit(folder):
    folder=Path(folder).resolve(); state=ledger(folder/'ledger'); plan=state['plan']; rows=[]
    fixed=protocol(); shared=None; signatures={}; verified=[]
    event_rows=[read(p) for p in sorted((folder/'ledger/events').glob('*.json'))]
    for index,entry in enumerate(state['cells']):
        cell=entry['cell']; item={'cell':cell,'status':entry['status']}
        if entry['status']=='audited':
            raw=folder/'collected'/cell['id']; ref=entry['result']
            assert sha(raw/'checksums.json')==ref['seal_sha256']
            outcome_path=folder/'cell-audits'/cell['id']/'summary.json'
            resource_path=folder/'resource-audits'/cell['id']/'summary.json'
            assert sha(outcome_path)==ref['outcome_audit_sha256'] and sha(resource_path)==ref['resource_audit_sha256']
            outcomes=audit_outcomes(raw,outcome_accounting=True); resources=audit_resources(raw)
            assert outcomes==read(outcome_path) and resources==read(resource_path)
            identity=read(raw/'identity.json'); source=read(raw/'source-manifest.json')
            assert identity['run_id']==plan['run_id'] and source['commit']==plan['source_commit']
            assert identity['qualification_cell']==outcomes['qualification_cell']==cell
            for name in ('scripts/audit_fnv06_load_qualification.py','scripts/fnv06_outcome_accounting.py',
                         'scripts/audit_fnv06_functional.py','scripts/audit_fnv06_cloud.py',
                         'scripts/audit_fnv06_telemetry.py','scripts/audit_fnv06_resources.py','benchlib/fnv06_campaign.py',
                         'benchlib/fnv06_reset.py','scripts/run_fnv06_load_cloud.py'):
                assert source['files'][name]==digest(canonical(Path(__file__).resolve().parents[1]/name))
            binding={k:identity[k] for k in ('run_id','source_commit','source_id','target_id','images',
                       'campaign_design_sha256','campaign_execution_sha256','resource_protocol_sha256','outcome_protocol_sha256')}
            if shared is None:shared=binding
            assert binding==shared
            signature=outcomes['warmup']['signature']
            if cell['kind'] in signatures:assert signature==signatures[cell['kind']]
            else:signatures[cell['kind']]=signature
            assert ref['failed_requests']==outcomes['request_accounting']['failed']
            assert ref['generator_headroom_passed'] is resources['generator_headroom_passed']
            assert resources['resource_evidence_verified'] is outcomes['independent_outcomes_verified'] is True
            if index:
                previous=state['cells'][index-1]
                reset_file=folder/'reset-receipts'/(previous['cell']['id']+'.json')
                reset=read(reset_file)
                started=next(e for e in event_rows if e['kind']=='started' and e['data']['cell']==cell)
                assert sha(reset_file)==identity['reset_receipt_sha256']==started['data']['reset_receipt_sha256']
                assert reset['previous_cell']==previous['cell'] and reset['next_cell']==cell
                assert reset['prior_seal_sha256']==previous['result']['seal_sha256']==identity['prior_cell_seal_sha256']
                assert reset['target_id']==identity['target_id'] and reset['run_id']==plan['run_id']
                assert reset['status']=='passed' and reset['outside_schema_catalogs_unchanged'] is True
                assert reset['transaction_committed'] is reset['schema_and_owned_role_absent'] is True
            else:assert 'reset_receipt_sha256' not in identity
            measured=[r for r in outcomes['diagnostics'] if r['id']==cell['id']+'M']
            assert len(measured)==1
            item.update(measured=measured[0],warmup=outcomes['diagnostics'][0],
                generator_headroom_passed=resources['generator_headroom_passed'],
                request_failed=outcomes['request_accounting']['failed'],
                request_accounting=outcomes['request_accounting'],seal_sha256=ref['seal_sha256'])
            verified.append(outcomes)
        elif entry['status']=='failed':item['reason']=entry['result']['reason']
        rows.append(item)
    counts=Counter(r['status'] for r in rows)
    complete=counts['audited']==45 and state['closed']
    return {'study_id':STUDY,'source_commit':plan['source_commit'],'status':'complete' if complete else 'incomplete',
        'plan_sha256':state['plan_sha256'],'ledger_head_sha256':state['head_sha256'],
        'planned_cells':45,'cells_by_status':dict(counts),'planned_denominators':fixed['denominators'],
        'verified_denominators':{'measured_recipients':sum(r['cell']['population'] for r in rows if r['status']=='audited'),
            'warmup_recipients':100*len(verified),'waiting_controls':4*len(verified),
            'total_workflow_instances':sum(r['workflow_instances'] for r in verified)},
        'all_planned_cells_reported':True,'campaign_closed':state['closed'],
        'request_failures_retained':sum(r['request_accounting']['failed'] for r in verified),
        'generator_constrained_cells':[r['cell']['id'] for r in rows if r['status']=='audited' and not r['generator_headroom_passed']],
        'performance_comparison_complete':complete and all(r.get('generator_headroom_passed') for r in rows),
        'cells':rows,'comparisons':comparisons(rows),
        'boundary':'Finite cohorts, compared within pattern and repeat. Original request failures and polling uncertainty remain visible. No cross-pattern ranking, arrival-rate capacity, per-workflow p99 or database-host headroom claim.'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to((args.source/'collected').resolve()):p.error('Use a fresh report outside collected evidence.')
    result=audit(args.source);args.output.mkdir(parents=True)
    (args.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('cells','comparisons')}))


if __name__=='__main__':main()
