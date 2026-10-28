#!/usr/bin/env python3
"""Audit a sealed W02-M2 cloud export and retain outcome-specific evidence."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchlib.runner import extract_event,normalize_event
from benchlib.w02_cloud import assess_cloud,require_matched_controls
from benchlib.w02_mixed_cloud import resolve_cloud_corpus,mixed_measurement_plan,summarize_mixed_pairs
from scripts.audit_w02_functional import read,sha
from scripts.audit_w02_mixed import audit_store
from scripts.run_w02_functional import require_outcome


def verify_export(folder):
    seal=read(folder/'export-checksums.json');assert seal['algorithm']=='sha256' and seal['files']
    for name,digest in seal['files'].items():
        path=(folder/name).resolve()
        assert path.is_relative_to(folder) and path.is_file() and sha(path)==digest, 'export_changed: '+name
    actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() and p!=folder/'export-checksums.json'}
    assert actual==set(seal['files']), 'export_file_inventory_changed'
    return seal


def functional_audit(folder):
    summary=read(folder/'functional/summary.json');assert summary['status']=='passed'
    cases=read(folder/'source/workloads/w02/cases.json')['cases'];rows=[];effects=repeats=0
    for case in cases:
        for arm in ('sequential','parallel'):
            raw=read(folder/'functional'/(arm+'-'+case['case_id']+'.json'))
            assert raw['original']==case
            require_outcome(case,raw['backend'],raw['observed'],raw['activities'])
            after=read(folder/'functional'/(arm+'-'+case['case_id']+'-replay.json'))
            assert after['effects']==raw['backend']['effects'] and after['receipts']==raw['backend']['receipts']
            before=raw['backend']['audit'];assert after['audit'][:len(before)]==before
            extra=after['audit'][len(before):];assert len(extra)==6 and all(x['event']=='replay' for x in extra)
            effects+=len(after['effects']);repeats+=len(extra);rows.append((case['case_id'],arm))
    assert [(r['case_id'],r['arm']) for r in summary['cases']]==rows and all(r['status']=='passed' and r['premature_effect_refused'] for r in summary['cases'])
    assert summary['functional_cases_passed']==20 and effects==8 and repeats==120
    assert len(summary['replay_probes'])==2 and all(r['status']=='blocked_as_expected' and r['original_effects_unchanged'] for r in summary['replay_probes'])
    return {'cases':20,'effects':8,'repeated_calls':120,'premature_effect_refusals':20,'cross_process_replays_blocked':2}


def audit(folder,phase):
    folder=Path(folder).resolve();seal=verify_export(folder)
    manifest=read(folder/'source-manifest.json');meta=read(folder/'identity.json')
    assert meta['study_id']=='w02_m2' and meta['target_id']!=meta['source_id']
    assert sha(folder/'runtime.py')==manifest['runtime_sha256']
    for name,digest in manifest['files'].items():
        path=(folder/'source'/name).resolve()
        assert path.is_relative_to(folder/'source') and sha(path)==digest, 'source_changed: '+name
    corpus=read(folder/'mixed-corpus.json')
    assert sha(folder/'mixed-corpus.json')==meta['mixed_corpus_sha256']==manifest['mixed_corpus_sha256']
    assert corpus==resolve_cloud_corpus(read(folder/'source/workloads/w02/sensitivity-protocol.json'),read(folder/'source/workloads/w02/cases.json'))
    qualification=read(folder/'qualification.json');plan=read(folder/'measurement-plan.json')
    assert qualification['status']=='passed' and qualification['measurement_plan_sha256']==sha(folder/'measurement-plan.json')
    assert plan['source_commit']==manifest['commit']==meta['source_commit']
    expected=mixed_measurement_plan(meta['run_id'],plan['rate_per_s'],corpus)
    assert all(plan[key]==value for key,value in expected.items()), 'frozen_mixed_plan_changed'
    assert plan['corpus_sha256']==meta['mixed_corpus_sha256']
    design=read(folder/'mixed-design.json')
    assert design['candidate_design']==mixed_measurement_plan(meta['run_id'],1,corpus)
    assert design['frozen_at']<=plan['frozen_at']
    assert design['source_commit']==manifest['commit'] and design['corpus_sha256']==plan['corpus_sha256']
    assert plan['qualification_sha256']==sha(folder/'qualification-progress.json')
    functionals=functional_audit(folder);calibration=read(folder/'calibration/generator-calibration.json')
    if phase=='qualification':
        lanes=[(f'q{i}-{row["arm"]}',row['arm'],row['rate'],100,100) for i,row in enumerate(plan['qualification_lanes'],1)]
    else:
        lanes=[(f'p{i}-{j}-{arm}',arm,plan['rate_per_s'],plan['warmup_s'],plan['measurement_s'])
               for i,pair in enumerate(plan['pairs'],1) for j,arm in enumerate(pair,1)]
    records=[];stores=[];measure=Counter();warmup=Counter();all_keys=set();all_pids=set()
    for name,arm,rate,warm,duration in lanes:
        path=folder/name
        if not (path/'assessment.json').exists():
            assert phase=='campaign', 'missing_qualification_arm'
            continue
        require_matched_controls(plan['controls'],read(path/'controls.json'))
        summary=read(path/'k6-summary.json')
        for key,value in {'arm':arm,'rate':rate,'warmup_s':warm,'duration_s':duration,'fixed_vus':16,
                          'drain_s':30,'deadline_s':2,'poll_interval_ms':100,'corpus_sha256':plan['corpus_sha256']}.items():
            assert summary['w02'][key]==value, 'run_controls_changed: '+key
        events=[]
        for line in (path/'k6.log').read_text(encoding='utf-8').splitlines():
            event=extract_event(line)
            if event:events.append(normalize_event(event))
        assert events==[json.loads(line) for line in (path/'events.ndjson').read_text(encoding='utf-8').splitlines()], 'derived_events_changed'
        telemetry=[json.loads(line) for line in (path/'telemetry.ndjson').read_text(encoding='utf-8').splitlines()]
        result=assess_cloud(events,summary,read(path/'execution.json'),calibration,read(path/'final-state.json'),
                            telemetry,read(path/'reset.json'),corpus=corpus)
        saved=read(path/'assessment.json')
        assert result=={k:v for k,v in saved.items() if k not in ('run_id','folder')}, 'derived_assessment_changed'
        assert saved['run_id']==summary['w02']['run_id'] and saved['folder']==name
        if phase=='qualification':assert result['status']=='passed' and result['measurement_valid']
        hashes=read(path/'stores/files.json')
        assert all(sha(path/'stores'/key)==value for key,value in hashes.items())
        if result['measurement_valid']:
            checked=audit_store(path/'stores/study.sqlite',events)
            stores.append({'folder':name,**checked,'telemetry':result['telemetry'],
                           'reset_fingerprint':result['checkpoint_reset']['restored_fingerprint']})
        for event in events:
            if event['event']=='completed':
                key=event['logical_request_id'];pid=event['details']['process_instance_id']
                assert key not in all_keys and pid not in all_pids, 'duplicate_process_across_arms'
                all_keys.add(key);all_pids.add(pid)
        for kind in ('settled','rejected','blocked'):
            measure[kind]+=result['cohorts']['measurement']['outcomes'][kind]['verified']
            warmup[kind]+=result['cohorts']['warmup']['outcomes'][kind]['verified']
        records.append({'folder':name,'assessment':result})
    if phase=='qualification':
        assert len(records)==4
        eligible=[rate for rate in (.5,1) if all(r['assessment']['status']=='passed' for r in records if r['assessment']['offered_rate']==rate)]
        assert plan['rate_per_s']==qualification['selected_rate']==max(eligible)
        result={'status':'passed','selected_rate_per_s':plan['rate_per_s'],'comparison':None,
                'planned_pairs':5,'pair_order':plan['pairs'],'completed_arms':4}
    else:result=summarize_mixed_pairs(plan,records)
    assert verify_export(folder)==seal
    result.update(study_id='w02_m2',scope='OCI synthetic mixed-outcome workflow study',phase=phase,
        functional_audit=functionals,measurement_outcomes=dict(measure),warmup_outcomes=dict(warmup),
        persisted_store_totals={key:sum(row[key] for row in stores) for key in ('cases','effects','receipts','injected_once_faults')},
        persisted_store_audit=stores,source_commit=manifest['commit'],source_manifest_sha256=sha(folder/'source-manifest.json'),
        frozen_plan_sha256=sha(folder/'measurement-plan.json'),corpus_sha256=plan['corpus_sha256'],
        export_files_verified=len(seal['files']),source_unchanged=True,native_model_calls=0,
        cloud_resources_accessed=True,representative_customer_distribution=False,auditor_sha256=sha(Path(__file__)))
    return result


def markdown(result):
    lines=['# W02-M2: mixed payment outcomes','',f'Status: **{result["status"]}**. Phase: {result["phase"]}.',
           '',f'Measured verified outcomes: {result["measurement_outcomes"]}. Warmup: {result["warmup_outcomes"]}.','',
           'Settlements, expected refusals and safe unknown-check stops have separate denominators. Only settlements contribute to settlement goodput.','']
    comparison=result.get('comparison')
    if comparison:
        delta=comparison['median_paired_on_time_settlement_change_per_s'];bounds=comparison['paired_on_time_settlement_change_per_s_range']
        lines += [f'Median paired change in timely settlements: **{delta:+.4f}/s** (pair range {bounds[0]:+.4f} to {bounds[1]:+.4f}/s).',
                  '', '| Workflow | Outcome | Verified / expected | Within 2 s | Late | Median run p95 |',
                  '| --- | --- | ---: | ---: | ---: | ---: |']
        for arm,rows in comparison['arms'].items():
            for kind,row in rows['outcomes'].items():
                lines.append(f'| {arm} | {kind} | {row["verified"]}/{row["expected"]} | {row["within_deadline"]} | {row["deadline_misses"]} | {row["median_of_run_p95_ms"]:.1f} ms |')
    else:lines += ['No paired improvement claim from this phase or incomplete series.']
    lines += ['', 'Synthetic seeded grid, five dependency profiles, two fixed workers and 100 ms completion polling. No live model or representative-customer/capacity claim.',
              '',f'Executed source: `{result["source_commit"]}`. The audit verified {result["export_files_verified"]} exported files and original SQLite stores.','']
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--phase',choices=('qualification','campaign'),required=True)
    args=parser.parse_args();source=args.evidence.resolve();output=args.output.resolve()
    if output.exists() or output.is_relative_to(source):parser.error('Use a fresh audit directory outside the sealed source.')
    result=audit(source,args.phase);output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/'README.md').write_text(markdown(result),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('status','phase','measurement_outcomes','persisted_store_totals','export_files_verified')}))


if __name__=='__main__':main()
