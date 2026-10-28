#!/usr/bin/env python3
"""Recheck sealed mixed-load evidence and original SQLite state without OCI."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from html import escape
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from benchlib.runner import extract_event, normalize_event
from benchlib.w02_qualification import assess
from benchlib.workflow_corpus import resolve_mixed_load
from scripts.audit_w02_functional import read, sha, verify_inventory
from scripts.qualify_w02_load import SOURCES, SCOPE
from services.workflow_checks import canonical, fingerprint


def audit_store(database, events):
    """Compare original persisted inputs, receipts, effects and injected faults."""
    for suffix in ('-wal','-journal'):
        sidecar=Path(str(database)+suffix)
        assert not sidecar.exists() or sidecar.stat().st_size==0, 'uncheckpointed_store'
    done=[e for e in events if e['event']=='completed']
    keys,pids,faults=set(),set(),set()
    effects_count=0
    profiles=defaultdict(lambda:{'offered':0,'outcomes':Counter(),'within_deadline':Counter()})
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
        assert db.execute('PRAGMA integrity_check').fetchall()==[('ok',)], 'store_integrity_failed'
        for event in done:
            key=event['logical_request_id'];data=event['details']
            case,state=data['original'],data['backend'];pid=data['process_instance_id']
            assert key not in keys and pid not in pids, 'duplicate_stored_identity'
            keys.add(key);pids.add(pid)
            assert db.execute('SELECT process_id,body,fingerprint FROM cases WHERE key=?',(key,)).fetchone()==(
                pid,canonical(case),fingerprint(case)), 'original_store_input_changed'
            receipts={op:json.loads(body) for op,body in db.execute('SELECT operation,body FROM receipts WHERE key=?',(key,))}
            assert receipts==state['receipts'], 'original_store_receipts_changed'
            effects=[dict(payment_id=p,amount_minor=a,beneficiary=b) for p,a,b in
                     db.execute('SELECT payment_id,amount_minor,beneficiary FROM effects WHERE key=?',(key,))]
            assert effects==state['effects'], 'original_store_effects_changed'
            records=[dict(operation=op,event=kind) for op,kind in
                     db.execute('SELECT operation,event FROM audit WHERE key=? ORDER BY id',(key,))]
            assert records==state['audit'], 'original_store_audit_changed'
            for op in ('kyc','risk','settle'):
                assert sum(r=={'operation':op,'event':receipts[op]['outcome']} for r in records)==1, 'missing_operation_audit'
            for op,schedule in case['schedule'].items():
                kind={'before_once':'failure_before_receipt','after_once':'response_lost_after_receipt'}.get(schedule['fault'])
                if kind:
                    assert sum(r=={'operation':op,'event':kind} for r in records)==1, 'missing_injected_fault_audit'
                    faults.add((key,op,schedule['fault']))
            effects_count+=len(effects)
            if event['phase']=='measurement':
                profile=profiles[case['dependency_profile']];outcome=case['expected_outcome']
                profile['offered']+=1;profile['outcomes'][outcome]+=1
                profile['within_deadline'][outcome]+=int(event['latency_s']<=event['deadline_s'])
        assert {row[0] for row in db.execute('SELECT key FROM cases')}==keys, 'unexpected_stored_case'
        assert db.execute('SELECT count(*) FROM receipts').fetchone()[0]==len(keys)*3, 'unexpected_stored_receipt'
        assert db.execute('SELECT count(*) FROM effects').fetchone()[0]==effects_count, 'unexpected_stored_effect'
        assert set(db.execute('SELECT key,operation,fault FROM faults'))==faults, 'injected_fault_inventory_mismatch'
        assert {row[0] for row in db.execute('SELECT DISTINCT key FROM audit')}==keys, 'unexpected_audit_identity'
    return {'cases':len(keys),'effects':effects_count,'receipts':len(keys)*3,
            'injected_once_faults':len(faults),'measurement_profiles':dict(profiles)}


def audit(source):
    source=Path(source).resolve();inventory=verify_inventory(source)
    manifest,summary=read(source/'manifest.json'),read(source/'summary.json')
    assert manifest['study_id']=='w02_m1' and manifest['scope']==SCOPE
    assert manifest['case_mode']=='mixed_sensitivity' and manifest['deadline_miss_action']=='report'
    assert manifest['cloud_resources_accessed'] is False and manifest['native_model_calls']==0
    assert summary['status']=='passed' and summary['scope']==SCOPE
    assert summary['cloud_resources_accessed'] is False and summary['performance_claim'] is None
    assert set(manifest['source_sha256'])==set(SOURCES), 'source_inventory_incomplete'
    for name,digest in manifest['source_sha256'].items():
        assert inventory['files']['inputs/'+name]==digest, 'source_inventory_mismatch'
    inputs=source/'inputs/workloads/w02'
    assert sha(inputs/'mixed-load-protocol.json')==manifest['protocol_sha256']
    assert sha(inputs/'mixed-corpus.json')==manifest['corpus_sha256']
    plan=resolve_mixed_load(read(inputs/'mixed-load-protocol.json'),read(inputs/'sensitivity-protocol.json'),read(inputs/'cases.json'))
    corpus=read(inputs/'mixed-corpus.json')
    assert corpus==plan['corpus'], 'resolved_corpus_changed'
    assert manifest['lanes']==plan['lanes'], 'lane_order_changed'
    assert manifest['warmup_s']==plan['warmup_s'] and manifest['duration_s']==plan['duration_s']
    calibration=read(source/'generator-calibration.json')
    assert len(summary['lanes'])==len(plan['lanes']), 'incomplete_lane_inventory'
    assert read(source/'progress.json')['lanes']==summary['lanes'], 'progress_summary_disagrees'
    results=[];counts=Counter();outcomes=Counter();warmup=Counter()
    for index,(lane,saved) in enumerate(zip(plan['lanes'],summary['lanes']),1):
        name=f'{index:02d}-{lane["arm"]}-{lane["rate"]}'
        assert saved['folder']==name, 'lane_folder_mismatch'
        folder=source/name;run=read(folder/'run.json');raw=read(folder/'k6-summary.json')
        for key,value in {'arm':lane['arm'],'rate':lane['rate'],'warmup_s':plan['warmup_s'],
                          'duration_s':plan['duration_s'],'worker_slots':2,'fixed_vus':16,'drain_s':15,
                          'deadline_s':2,'completion_poll_ms':100,'fresh_engine_and_backend':True,
                          'case_mode':'mixed_sensitivity','corpus_sha256':manifest['corpus_sha256'],
                          'deadline_miss_action':'report','scope':SCOPE}.items():
            assert run[key]==value, 'run_controls_differ_from_plan'
        for key,value in {'run_id':run['run_id'],**lane,'warmup_s':plan['warmup_s'],
                          'duration_s':plan['duration_s'],'fixed_vus':16,'drain_s':15,
                          'deadline_s':2,'poll_interval_ms':100,'corpus_sha256':manifest['corpus_sha256']}.items():
            assert raw['w02'][key]==value, 'driver_controls_differ_from_plan'
        events=[]
        for line in (folder/'k6.log').read_text(encoding='utf-8').splitlines():
            item=extract_event(line)
            if item:events.append(normalize_event(item))
        assert events==[json.loads(line) for line in (folder/'events.ndjson').read_text(encoding='utf-8').splitlines()], 'derived_events_changed'
        final=read(folder/'final-state.json')
        assessment=assess(events,raw,read(folder/'load-process.json')['returncode'],calibration,final,
                          corpus=corpus,require_deadlines=False)
        assert assessment['status']=='passed', 'recomputed_assessment_failed: '+','.join(assessment['issues'])
        assert assessment==read(folder/'assessment.json')=={k:v for k,v in saved.items() if k!='folder'}, 'derived_assessment_changed'
        assert not any(r['status']=='fatal_worker_error' for r in read(folder/'worker-records.json'))
        assert read(folder/'cleanup.json')=={'owned_engine_stopped':True,'errors':[]}, 'owned_stack_cleanup_failed'
        stored=audit_store(folder/'study.sqlite',events)
        measured=assessment['cohorts']['measurement'];warm=assessment['cohorts']['warmup']
        for key in ('cases','effects','receipts','injected_once_faults'):counts[key]+=stored[key]
        for kind in ('settled','rejected','blocked'):
            outcomes[kind]+=measured['outcomes'][kind]['verified'];warmup[kind]+=warm['outcomes'][kind]['verified']
        results.append({'folder':name,**lane,'measurement':measured,'warmup':warm,
                        'original_store':stored,'cleanup':'owned_engine_stopped'})
    assert verify_inventory(source)==inventory, 'audit_changed_source'
    return {'status':'passed','study_id':'w02_m1','scope':SCOPE,'sealed_files_verified':len(inventory['files']),
            'source_manifest_sha256':sha(source/'manifest.json'),'source_checksums_sha256':sha(source/'checksums.json'),
            'auditor_sha256':sha(Path(__file__)),'protocol_sha256':manifest['protocol_sha256'],
            'resolved_corpus_sha256':manifest['corpus_sha256'],'measurement_arrivals':sum(outcomes.values()),
            'measurement_outcomes':dict(outcomes),'warmup_arrivals':sum(warmup.values()),'warmup_outcomes':dict(warmup),
            'original_store_totals':dict(counts),'lanes':results,'all_owned_engines_stopped':True,
            'remaining_engine_work':0,'source_unchanged':True,'deadline_miss_action':'report',
            'representative_customer_distribution':False,'performance_claim':None,
            'cloud_resources_accessed':False,'native_model_calls':0}


def render_report(result):
    """A portable saved-evidence view; no workload execution or remote assets."""
    labels={'settled':'Settlements','rejected':'Expected refusals','blocked':'Safe unknown-check stops'}
    cards=''.join(f'<article><strong>{result["measurement_outcomes"][kind]}</strong><span>{label}</span></article>'
                  for kind,label in labels.items())
    rows=[];profiles=[]
    for lane in result['lanes']:
        measured=lane['measurement'];name=f'{lane["arm"].title()} · {lane["rate"]}/s'
        cells=''.join(f'<td>{measured["outcomes"][kind]["within_deadline"]} / {measured["outcomes"][kind]["verified"]}</td>'
                      for kind in labels)
        rows.append(f'<tr data-lane="{escape(lane["folder"])}"><th scope="row">{name}</th>'
                    f'<td>{measured["offered"]}</td>{cells}<td>{measured["offered"]-measured["on_time_correct"]}</td>'
                    f'<td>{measured["unresolved_or_invalid_count"]} / {measured["dropped"]}</td></tr>')
        details=[]
        for profile,data in lane['original_store']['measurement_profiles'].items():
            timed=''.join(f'<td>{data["within_deadline"].get(kind,0)} / {data["outcomes"].get(kind,0)}</td>' for kind in labels)
            details.append(f'<tr><th scope="row">{escape(profile.replace("_"," "))}</th><td>{data["offered"]}</td>{timed}</tr>')
        profiles.append(f'<details><summary>{name}: dependency profiles</summary><div class="scroll"><table>'
                        '<thead><tr><th>Profile</th><th>Offered</th><th>Settlements ≤2 s</th><th>Refusals ≤2 s</th><th>Safe stops ≤2 s</th></tr></thead>'
                        f'<tbody>{"".join(details)}</tbody></table></div></details>')
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>W02 mixed-outcome qualification</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f7fa;color:#152e40;font:18px/1.55 system-ui,sans-serif}
main{max-width:1220px;margin:auto;padding:38px 30px 52px}h1{font-size:40px;line-height:1.15;margin:12px 0 18px;letter-spacing:-1px}
h2{font-size:24px;margin:28px 0 12px}.scope{font-size:14px;letter-spacing:.07em;font-weight:750;color:#145c7b}
.lead{max-width:940px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0}
article{border-top:5px solid #087ed3;background:white;padding:20px 24px;border-radius:8px;box-shadow:0 2px 6px #14344912}
article:nth-child(2){border-color:#798b99}article:nth-child(3){border-color:#b76b1a}article strong{display:block;font-size:40px;line-height:1.2}article span{font-size:16px}
.note{background:#e6eff4;border-left:4px solid #1672aa;padding:14px 18px}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:16px;background:white}
th,td{text-align:left;padding:13px 12px;border-bottom:1px solid #dce6ec}thead{background:#173b52;color:white}tbody th{font-weight:650}
details{margin:12px 0;border:1px solid #d1dce3;border-radius:6px;background:white;padding:14px 18px}summary{font-weight:650;cursor:pointer}
a{color:#0068ad;text-underline-offset:3px}a:focus-visible,summary:focus-visible{outline:3px solid #1688d0;outline-offset:4px}
.muted{color:#496273;font-size:16px}footer{border-top:1px solid #c7d7e2;margin-top:28px;padding-top:16px;font-size:14px}
@media(max-width:650px){main{padding:22px 16px}h1{font-size:31px}.cards{grid-template-columns:1fr}article strong{font-size:32px}}
</style><main><p class="scope">LOCAL LOAD-DRIVER QUALIFICATION · NO OCI PERFORMANCE CLAIM</p>
<h1>Mixed payment outcomes under steady load</h1>
<p class="lead">The same 50 synthetic scenarios run through sequential and parallel checks. Verify the authorized outcome first, then count whether it meets the two-second deadline. Expected refusals and safe stops produce no payment effect.</p>
''' + f'<section class="cards" aria-label="Verified measured outcomes">{cards}</section>' + f'''
<p class="note"><strong>{result['measurement_arrivals']} measured arrivals verified.</strong> Another {result['warmup_arrivals']} warmup arrivals are counted separately. Equal scenario coverage is a synthetic test design; it does not describe customer payment frequencies.</p>
<h2>Correctness and timeliness stay separate</h2><p class="muted">Outcome cells show <strong>within 2 seconds / verified</strong>. Only timely settlements contribute to settlement goodput. Late outcomes remain in the denominator.</p>
<div class="scroll"><table id="lanes"><thead><tr><th>Workflow / offered rate</th><th>Offered</th><th>Settlements ≤2 s</th><th>Refusals ≤2 s</th><th>Safe stops ≤2 s</th><th>Late</th><th>Invalid / dropped</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>Inspect the dependency mix</h2><p class="muted">Five fixed KYC/risk delay profiles, including unknown checks and one-time failures. Both arms retain two worker slots, 100 ms completion polling, 20 seconds of warmup and 100 seconds of measurement per run.</p>
{''.join(profiles)}
<h2>What this qualifies</h2><p>The local driver preserves the offered schedule, original inputs, exact effects and outcome categories under continuous load. The audit rechecks {result['original_store_totals']['cases']} original SQLite registrations, {result['original_store_totals']['receipts']} receipts and {result['sealed_files_verified']} sealed files. All owned engines stopped with no remaining work.</p>
<p class="muted">Windows · Fluxnova 3.0.1 · Java 21 · embedded H2. One fixed pass through four runs, with no live model. These diagnostic counts establish neither a matched OCI speedup nor sustainable capacity. The presentation's completed OCI comparisons retain their original evidence.</p>
<p><a id="evidence" href="summary.json">Open the independently audited evidence (JSON)</a></p>
<footer>Research and OCI implementation: Nicholas Cusato. Fluxnova software: FINOS contributors. W02-M1 · Saved evidence only; this page starts no workload.</footer></main></html>'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();source=args.source.resolve();output=args.output.resolve()
    if output.exists() or output.is_relative_to(source):parser.error('Use a fresh output directory outside the sealed source.')
    result=audit(source);output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/'report.html').write_text(render_report(result),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='lanes'},indent=2))


if __name__=='__main__':main()
