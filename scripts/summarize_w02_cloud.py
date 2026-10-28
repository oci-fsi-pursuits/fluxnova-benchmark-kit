#!/usr/bin/env python3
"""Re-audit a downloaded W02 stage and write a resource-ID-free paired summary."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchlib.w02_cloud import assess_cloud,require_matched_controls
from benchlib.w02_results import summarize_pairs


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8'))


def recheck(folder):
    manifest=read(folder/'source-manifest.json');plan=read(folder/'measurement-plan.json')
    if digest(folder/'runtime.py')!=manifest['runtime_sha256'] or manifest['commit']!=plan['source_commit']:
        raise ValueError('Runtime or source revision differs from retained evidence')
    for name,wanted in manifest['files'].items():
        path=(folder/'source'/name).resolve()
        if not path.is_relative_to((folder/'source').resolve()) or digest(path)!=wanted:
            raise ValueError('Archived source changed: '+name)
    qualification=read(folder/'qualification.json')
    if qualification['status']!='passed' or qualification['measurement_plan_sha256']!=digest(folder/'measurement-plan.json'):
        raise ValueError('The measurement protocol was not frozen after passing qualification')
    calibration=read(folder/'calibration/generator-calibration.json')
    records=[]
    for i,pair in enumerate(plan['pairs'],1):
        for j,arm in enumerate(pair,1):
            if arm not in ('sequential','parallel'):raise ValueError('Invalid arm')
            name=f'p{i}-{j}-{arm}';path=folder/name
            if not (path/'assessment.json').is_file():continue
            require_matched_controls(plan['controls'],read(path/'controls.json'))
            events=[json.loads(line) for line in (path/'events.ndjson').read_text().splitlines() if line.strip()]
            telemetry=[json.loads(line) for line in (path/'telemetry.ndjson').read_text().splitlines() if line.strip()]
            settings=read(path/'k6-summary.json')
            for key,expected in {'arm':arm,'rate':plan['rate_per_s'],'warmup_s':plan['warmup_s'],
                 'duration_s':plan['measurement_s'],'drain_s':plan['drain_s'],'fixed_vus':plan['fixed_vus'],
                 'deadline_s':plan['deadline_s'],'poll_interval_ms':plan['poll_interval_ms']}.items():
                if settings['w02'][key]!=expected:raise ValueError('Run differs from the frozen plan: '+key)
            for phase,seconds in [('warmup',plan['warmup_s']),('measurement',plan['measurement_s'])]:
                if settings['w02'][phase]['scheduled']!=seconds*plan['rate_per_s']:
                    raise ValueError('Offered cohort was changed')
            result=assess_cloud(events,settings,read(path/'execution.json'),calibration,read(path/'final-state.json'),telemetry,read(path/'reset.json'))
            saved=read(path/'assessment.json')
            for key in ['measurement_valid','issues','cohorts','final_state','telemetry']:
                if result[key]!=saved[key]:raise ValueError('Recomputed assessment differs: '+name+'/'+key)
            records.append({'folder':name,'assessment':result})
    summary=summarize_pairs(plan,records)
    summary.update(independently_reaudited=True,source_manifest_sha256=digest(folder/'source-manifest.json'),
                   measurement_plan_sha256=digest(folder/'measurement-plan.json'),
                   scope='OCI W02; not a live-model or production-payment benchmark')
    return summary


def markdown(value):
    lines=['# W02: sequential versus parallel checks','',
           f"Status: **{value['status']}** — {value['completed_arms']} of 10 measured arms available.",'',
           'Same payment and screening inputs, two worker slots and offered rate; only BPMN check ordering changes.','']
    comparison=value['comparison']
    if comparison:
        change=comparison['median_paired_p95_change_percent'];lo,hi=comparison['paired_p95_change_percent_range']
        lines += [f'Parallel observed p95 changed by **{change:+.1f}%** at the median of the five paired changes (pair range {lo:+.1f}% to {hi:+.1f}%). Negative means lower latency.','',
                  '| Workflow | Median run p95 | Median run p99 | Correct / offered | Correct within 2 s | Deadline goodput |',
                  '| --- | ---: | ---: | ---: | ---: | ---: |']
        for arm,stats in comparison['arms'].items():
            lines.append(f"| {arm} | {stats['median_of_run_p95_ms']:.1f} ms | {stats['median_of_run_p99_ms']:.1f} ms | {stats['correct']} / {stats['offered']} | {stats['on_time_correct']} | {stats['deadline_goodput_per_s']:.3f}/s |")
    else:
        lines += ['No aggregate improvement claim: the five-pair series is incomplete or includes an invalid arm.']
    lines+=['','The latency columns summarize run percentiles; they are not pooled request percentiles. Warmup work is excluded from the comparison denominator.','']
    lines += ['- '+limit for limit in value['limits']]
    lines += ['',f"Executed source: `{value['source_commit']}`. Raw outcomes, exact effects, reset receipts, clocks and telemetry were independently rechecked.",'']
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--evidence',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Use a new output directory; retain previous analyses.')
    result=recheck(args.evidence.resolve());args.output.mkdir(parents=True)
    (args.output/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (args.output/'README.md').write_text(markdown(result),encoding='utf-8')
    print(json.dumps({'status':result['status'],'completed_arms':result['completed_arms'],'summary':str(args.output/'README.md')}))


if __name__=='__main__':main()
