#!/usr/bin/env python3
"""Independently audit W01 exports, persisted effects and paired measurements."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from benchlib.w01_cloud import assess_cloud,require_matched_controls
from benchlib.w01_pacing import payload


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def lines(path):return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def verify_export(folder):
    export=read(folder/'export-checksums.json');manifest=read(folder/'source-manifest.json')
    for name,wanted in export['files'].items():
        path=(folder/name).resolve()
        assert path.is_relative_to(folder.resolve()) and sha(path)==wanted,'export changed'
    assert sha(folder/'runtime.py')==manifest['runtime_sha256']
    for name,wanted in manifest['files'].items():
        path=(folder/'source'/name).resolve()
        assert path.is_relative_to((folder/'source').resolve()) and sha(path)==wanted,'archived source changed'
    # The independent entry point may be newer; the outcome/telemetry rules must
    # be byte-equivalent to the rules that were frozen before execution.
    for name in ('benchlib/w01_cloud.py','benchlib/w01_pacing.py','benchlib/w02_cloud.py','benchlib/analysis.py'):
        assert hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==manifest['files'][name]
    qualification=read(folder/'qualification.json');plan=read(folder/'measurement-plan.json')
    assert qualification['status']=='passed' and qualification['measurement_plan_sha256']==sha(folder/'measurement-plan.json')
    assert plan['source_commit']==manifest['commit']
    return plan,manifest,len(export['files'])


def audit_lane(folder,path,arm,plan,calibration,warmup,measured):
    settings=read(path/'k6-summary.json')
    for key,value in {'arm':arm,'warmup_bursts':warmup,'measurement_bursts':measured,'burst_rate':20,
        'burst_duration_s':1,'period_s':10,'fixed_vus_per_burst':32,'deadline_s':2,'poll_interval_ms':100,'drain_s':20}.items():
        assert settings['w01'][key]==value,(path.name,key)
    require_matched_controls(plan['controls'],read(path/'controls.json'))
    events=lines(path/'events.ndjson');telemetry=lines(path/'telemetry.ndjson')
    result=assess_cloud(events,settings,read(path/'execution.json'),calibration,read(path/'final-state.json'),
        telemetry,read(path/'reset.json'),read(path/'http-observations.json'))
    saved=read(path/'assessment.json')
    for key in ('measurement_valid','issues','cohorts','telemetry','checkpoint_reset'):
        assert result[key]==saved[key],(path.name,key,'recomputed assessment differs')
    stores=read(path/'stores/files.json')
    assert all(sha(path/'stores'/name)==wanted for name,wanted in stores.items())
    completed=[e for e in events if e['event']=='completed']
    with closing(sqlite3.connect((path/'stores/study.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        assert db.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
        assert db.execute('SELECT count(*) FROM effects').fetchone()[0]==len(completed)==20*(warmup+measured)
        assert db.execute('SELECT count(*) FROM receipts').fetchone()[0]==len(completed)
        for event in completed:
            key=event['logical_request_id'];backend=event['details']['backend']
            actual=db.execute('SELECT fingerprint,result FROM receipts WHERE key=?',(key,)).fetchone()
            assert actual and actual[0]==backend['fingerprint'] and json.loads(actual[1])==backend['result']
            assert db.execute('SELECT amount,beneficiary FROM effects WHERE key=?',(key,)).fetchall()==[(125,'synthetic-beneficiary')]
    result['persisted_original_effects_checked']=len(completed)
    return result


def audit_functional(folder):
    summary=read(folder/'functional/summary.json');assert summary['status']=='passed' and summary['functional_cases_passed']==12
    definitions=[('approve',{},'settled'),('unauthorized',{'authorized':False},'rejected'),
        ('sanctions',{'sanctions_match':True},'rejected'),('over_limit',{'amount_minor':2000},'rejected'),
        ('before_effect',{},'settled'),('after_effect',{},'settled')]
    effects=repeats=0
    for arm in ('success_delay_100ms','idle_only'):
        for name,changes,outcome in definitions:
            value=read(folder/'functional'/arm/(name+'.json'));payment={**payload(),**changes}
            assert value['original']==payment
            history=value['history'];before=value['before_replay'];after=value['after_replay']
            assert history['instance']['state']=='COMPLETED' and history['variables']['workerResult']==outcome
            for key,expected in payment.items():assert history['variables'][key]==expected
            assert before['result']['outcome']==outcome and before['effect_count']==int(outcome=='settled')
            expected=([{'amount_minor':payment['amount_minor'],'beneficiary':'synthetic-beneficiary'}] if outcome=='settled' else [])
            assert [e['details'] for e in before['events'] if e['event']=='payment_effect']==expected
            assert before['result']==after['result'] and before['fingerprint']==after['fingerprint'] and before['effect_count']==after['effect_count']
            assert after['events'][:len(before['events'])]==before['events']
            added=after['events'][len(before['events']):]
            assert len(added)==3 and all(e['event']=='replay' for e in added)
            effects+=after['effect_count'];repeats+=len(added)
    assert effects==6 and repeats==36
    return {'cases':12,'expected_effects':effects,'repeat_calls':repeats}


def recheck(folder,phase):
    plan,manifest,files=verify_export(folder);calibration=read(folder/'calibration/generator-calibration.json')
    functional=audit_functional(folder)
    rows=[]
    order=([(i,1,arm,f'q{i}-{arm}',2,3) for i,arm in enumerate(('success_delay_100ms','idle_only','idle_only','success_delay_100ms'),1)]
        if phase=='qualification' else [(i,j,arm,f'p{i}-{j}-{arm}',plan['warmup_bursts'],plan['measurement_bursts'])
            for i,pair in enumerate(plan['pairs'],1) for j,arm in enumerate(pair,1)])
    for pair,index,arm,name,warmup,measured in order:
        path=folder/name
        if not (path/'assessment.json').is_file():continue
        result=audit_lane(folder,path,arm,plan,calibration,warmup,measured)
        rows.append({'pair':pair,'order':index,'arm':arm,'folder':name,'measurement_valid':result['measurement_valid'],
            'issues':result['issues'],'cohorts':result['cohorts'],'telemetry':result['telemetry'],
            'peak_http_per_1s_bucket':result['peak_http_per_1s_bucket'],
            'persisted_original_effects_checked':result['persisted_original_effects_checked'],
            'reset_fingerprint':result['checkpoint_reset']['restored_fingerprint']})
    assert len({r['reset_fingerprint'] for r in rows})<=1
    valid=len(rows)==len(order) and all(r['measurement_valid'] for r in rows)
    if phase=='campaign':
        status=read(folder/'campaign-status.json');valid=valid and status['status']=='passed' and bool(status.get('finished_at'))
    result={'status':'complete' if valid else 'incomplete','phase':phase,'completed_arms':len(rows),'planned_arms':len(order),
        'source_commit':manifest['commit'],'independently_reaudited':True,'export_files_verified':files,
        'source_manifest_sha256':sha(folder/'source-manifest.json'),'measurement_plan_sha256':sha(folder/'measurement-plan.json'),
        'auditor_sha256':sha(Path(__file__)),'rows':rows,'comparison':None,
        'functional':functional,
        'measured_offered':sum(r['cohorts']['measurement']['offered'] for r in rows),
        'measured_correct':sum(r['cohorts']['measurement']['correct'] for r in rows),
        'warmup_correct':sum(r['cohorts']['warmup']['correct'] for r in rows),
        'scope':'W01 synthetic burst study; reconstructed pacing control, not a current Fluxnova default',
        'limits':['Two-minute measured burst intervals; not a sustained-load or maximum-capacity estimate.',
                  'Observed latency includes the unchanged 100 ms completion observer.',
                  'Shared controller plus fresh single-node OCI PostgreSQL; not comparable absolute timings to P01 or W02.',
                  'Synthetic payments and exact effects, no live model or financial network.',
                  'Host/Docker/SQL telemetry, not managed DB host CPU or full distributed traces.',
                  'Actual billed cost is not measured.']}
    if valid and phase=='campaign':
        arms={}
        for arm in ('success_delay_100ms','idle_only'):
            cohorts=[r['cohorts']['measurement'] for r in rows if r['arm']==arm]
            arms[arm]={key:sum(c[key] for c in cohorts) for key in ('offered','correct','on_time_correct','deadline_misses','dropped')}
            arms[arm].update(deadline_goodput_per_s=arms[arm]['on_time_correct']/(5*plan['measurement_s']),
                median_run_p95_ms=statistics.median(c['observed_latency_s']['p95'] for c in cohorts)*1000,
                median_run_p99_ms=statistics.median(c['observed_latency_s']['p99'] for c in cohorts)*1000)
        pairs=[]
        for pair in range(1,6):
            c={r['arm']:r['cohorts']['measurement'] for r in rows if r['pair']==pair}
            before,after=c['success_delay_100ms'],c['idle_only']
            pairs.append({'pair':pair,'on_time_correct_before':before['on_time_correct'],'on_time_correct_after':after['on_time_correct'],
                'deadline_goodput_difference_per_s':(after['on_time_correct']-before['on_time_correct'])/plan['measurement_s'],
                'observed_p95_before_ms':before['observed_latency_s']['p95']*1000,'observed_p95_after_ms':after['observed_latency_s']['p95']*1000,
                'observed_p95_change_percent':100*(after['observed_latency_s']['p95']/before['observed_latency_s']['p95']-1)})
        result['comparison']={'primary_metric':'correct on-time payments per full measurement second','arms':arms,'pairs':pairs,
            'median_paired_deadline_goodput_difference_per_s':statistics.median(p['deadline_goodput_difference_per_s'] for p in pairs),
            'median_paired_p95_change_percent':statistics.median(p['observed_p95_change_percent'] for p in pairs),
            'paired_p95_change_range_percent':[min(p['observed_p95_change_percent'] for p in pairs),max(p['observed_p95_change_percent'] for p in pairs)]}
    return result


def markdown(value):
    rows=['# W01: worker pacing during payment bursts','',f"Status: **{value['status']}**; {value['completed_arms']}/{value['planned_arms']} {value['phase']} arms.",'']
    if value['comparison']:
        c=value['comparison'];rows+=['| Worker pacing | Correct / offered | Within 2 s | Late | Deadline goodput | Median run p95 |',
            '| --- | ---: | ---: | ---: | ---: | ---: |']
        for arm,s in c['arms'].items():rows.append(f"| {arm} | {s['correct']} / {s['offered']} | {s['on_time_correct']} | {s['deadline_misses']} | {s['deadline_goodput_per_s']:.3f}/s | {s['median_run_p95_ms']:.1f} ms |")
        rows+=['',f"Median paired change in correct on-time payments: **{c['median_paired_deadline_goodput_difference_per_s']:+.3f}/s**. Median paired observed-p95 change: **{c['median_paired_p95_change_percent']:+.1f}%**. Deadline misses are retained.",'']
    else:rows+=['No aggregate improvement claim from qualification or incomplete measurements.','']
    rows+=['Warmup is excluded from measured denominators. Median run percentiles are not pooled request percentiles.','']
    rows+=['- '+v for v in value['limits']]
    rows+=['',f"Executed source: `{value['source_commit']}`. Independently rechecked raw events, exact persisted effects/receipts, reset fingerprints and resource samples.",'']
    return '\n'.join(rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--phase',choices=('qualification','campaign'),default='campaign')
    args=parser.parse_args()
    if args.output.exists():parser.error('Use a new output folder; preserve earlier analyses.')
    value=recheck(args.evidence.resolve(),args.phase);args.output.mkdir(parents=True)
    (args.output/'summary.json').write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (args.output/'README.md').write_text(markdown(value),encoding='utf-8')
    print(json.dumps({'status':value['status'],'completed_arms':value['completed_arms'],'measured_correct':value['measured_correct']}))


if __name__=='__main__':main()
