"""Frozen W02-M2 mixed-cohort design and outcome-specific paired reporting."""
import math
from statistics import median

from benchlib.w02_cloud import measurement_plan
from benchlib.workflow_corpus import resolve_sensitivity
from services.workflow_checks import fingerprint

OUTCOMES=('settled','rejected','blocked')
QUALIFICATION_LANES=[{'arm':arm,'rate':rate} for rate,arms in ((.5,('sequential','parallel')),(1,('parallel','sequential'))) for arm in arms]


def resolve_cloud_corpus(sensitivity,base):
    corpus=resolve_sensitivity(sensitivity,base)
    counts={kind:sum(c['expected_outcome']==kind for c in corpus['cases']) for kind in OUTCOMES}
    if len(corpus['cases'])!=50 or counts!=dict(settled=20,rejected=20,blocked=10):
        raise ValueError('Mixed cloud corpus inventory changed')
    return corpus


def mixed_measurement_plan(run_id,rate,corpus):
    if type(rate) not in (int,float):raise ValueError('Invalid mixed offered rate')
    counts={kind:sum(c['expected_outcome']==kind for c in corpus['cases']) for kind in OUTCOMES}
    if len(corpus['cases'])!=50 or counts!=dict(settled=20,rejected=20,blocked=10):
        raise ValueError('Mixed cloud corpus inventory changed')
    plan=measurement_plan(run_id,rate)
    plan.update(protocol='w02-mixed-cloud-v1',study_id='w02_m2',case_mode='mixed_sensitivity',warmup_s=100,
        corpus_fingerprint=fingerprint(corpus),corpus_size=50,outcomes_per_cycle=counts,
        qualification_warmup_s=100,qualification_measurement_s=100,qualification_lanes=[dict(row) for row in QUALIFICATION_LANES],
        deadline_miss_action='report',
        primary_outcome='Paired change in correct settlements observed within 2 seconds per measurement second',
        secondary_outcomes=['On-time fraction and observed p95/p99 separately for settlements, refusals and safe stops',
                            'Incorrect/unknown outcomes and dropped arrivals','Dependency profile outcomes'],
        population='Repeated seeded 50-case synthetic grid: 40% settled, 40% refused, 20% safe unknown-check stops; five delay profiles and one-time faults',
        rate_selection='Highest common rate passing correctness, reset, telemetry and generator guards; deadline misses remain outcomes at both rates',
        interpretation='No representative-customer, live-model or sustainable-capacity claim. Expected refusals and safe stops are not settlements.')
    return plan


def summarize_mixed_pairs(plan,records):
    if plan.get('protocol')!='w02-mixed-cloud-v1' or len(plan['pairs'])!=5 or any(sorted(p)!=['parallel','sequential'] for p in plan['pairs']):
        raise ValueError('Expected the frozen five-pair mixed design')
    expected={f'p{i}-{j}-{arm}':(i,arm) for i,pair in enumerate(plan['pairs'],1) for j,arm in enumerate(pair,1)}
    if len({r['folder'] for r in records})!=len(records) or any(r['folder'] not in expected for r in records):
        raise ValueError('Duplicate or undeclared mixed arm')
    arms=[];duration=plan['measurement_s'];offered=int(duration*plan['rate_per_s'])
    if offered%plan['corpus_size']:raise ValueError('Partial mixed measurement cycle')
    cycles=offered//plan['corpus_size']
    for record in records:
        r=record['assessment'];m=r['cohorts']['measurement'];pair,arm=expected[record['folder']]
        if r['arm']!=arm or r['offered_rate']!=plan['rate_per_s'] or r.get('case_mode')!='mixed_sensitivity':
            raise ValueError('Mixed arm differs from the frozen plan')
        categories={}
        for kind in OUTCOMES:
            row=m['outcomes'][kind];wanted=cycles*plan['outcomes_per_cycle'][kind]
            if row['expected']!=wanted or not 0<=row['within_deadline']<=row['verified']<=wanted:
                raise ValueError('Mixed outcome denominator changed')
            latency=row['observed_latency_s']
            if latency['n']!=row['verified'] or any(latency[k] is not None and (not math.isfinite(latency[k]) or latency[k]<0) for k in ('p95','p99')):
                raise ValueError('Mixed outcome latency inventory changed')
            categories[kind]={'expected':wanted,'verified':row['verified'],'within_deadline':row['within_deadline'],
                'deadline_misses':row['verified']-row['within_deadline'],
                'observed_p95_ms':None if latency['p95'] is None else 1000*latency['p95'],
                'observed_p99_ms':None if latency['p99'] is None else 1000*latency['p99']}
        if (m['offered']!=offered or m['correct']!=sum(v['verified'] for v in categories.values()) or
                m['on_time_correct']!=sum(v['within_deadline'] for v in categories.values())):
            raise ValueError('Mixed totals do not reconcile')
        if r['measurement_valid'] and (m['correct']!=offered or m['dropped'] or m['invalid_or_unknown']):
            raise ValueError('Invalid mixed outcome labeled valid')
        arms.append({'pair':pair,'arm':arm,'measurement_valid':r['measurement_valid'],'issues':r['issues'],
            'offered':offered,'verified':m['correct'],'dropped':m['dropped'],'unknown_or_invalid':len(m['invalid_or_unknown']),
            'outcomes':categories,'on_time_settlements_per_s':categories['settled']['within_deadline']/duration,
            'warmup_offered':r['cohorts']['warmup']['offered'],'warmup_verified':r['cohorts']['warmup']['correct']})
    complete=len(arms)==10 and all(r['measurement_valid'] for r in arms)
    result={'status':'complete' if complete else 'incomplete_or_invalid','study_id':'w02_m2',
        'completed_arms':len(arms),'planned_arms':10,'source_commit':plan['source_commit'],
        'population':plan['population'],'rate_per_s':plan['rate_per_s'],'measurement_s_per_arm':duration,
        'primary_outcome':plan['primary_outcome'],'deadline_s':plan['deadline_s'],'poll_interval_ms':plan['poll_interval_ms'],
        'arms':arms,'comparison':None,'limits':[plan['interpretation'],'Observed completion includes 100 ms history polling.',
        'Five paired changes; their range is not a confidence interval.','Shared-controller OCI topology differs from P01.',
        'Run percentiles are not pooled request percentiles; warmup outcomes are separate.']}
    if not complete:return result
    paired=[]
    for pair in range(1,6):
        a,b=({r['arm']:r for r in arms if r['pair']==pair}[arm] for arm in ('sequential','parallel'))
        categories={kind:{'sequential_within_deadline':a['outcomes'][kind]['within_deadline'],
            'parallel_within_deadline':b['outcomes'][kind]['within_deadline'],
            'parallel_on_time_fraction_change_percentage_points':100*(b['outcomes'][kind]['within_deadline']-a['outcomes'][kind]['within_deadline'])/a['outcomes'][kind]['expected'],
            'sequential_p95_ms':a['outcomes'][kind]['observed_p95_ms'],'parallel_p95_ms':b['outcomes'][kind]['observed_p95_ms']} for kind in OUTCOMES}
        paired.append({'pair':pair,'sequential_on_time_settlements_per_s':a['on_time_settlements_per_s'],
                       'parallel_on_time_settlements_per_s':b['on_time_settlements_per_s'],
                       'parallel_on_time_settlement_change_per_s':b['on_time_settlements_per_s']-a['on_time_settlements_per_s'],
                       'outcomes':categories})
    delta=[p['parallel_on_time_settlement_change_per_s'] for p in paired]
    per_arm={}
    for arm in ('sequential','parallel'):
        rows=[r for r in arms if r['arm']==arm]
        per_arm[arm]={'offered':sum(r['offered'] for r in rows),'verified':sum(r['verified'] for r in rows),
            'on_time_settlements_per_s':sum(r['outcomes']['settled']['within_deadline'] for r in rows)/(5*duration),
            'outcomes':{kind:{**{key:sum(r['outcomes'][kind][key] for r in rows) for key in ('expected','verified','within_deadline','deadline_misses')},
                'median_of_run_p95_ms':median(r['outcomes'][kind]['observed_p95_ms'] for r in rows),
                'median_of_run_p99_ms':median(r['outcomes'][kind]['observed_p99_ms'] for r in rows)} for kind in OUTCOMES}}
    result['comparison']={'paired_runs':paired,'arms':per_arm,'median_paired_on_time_settlement_change_per_s':median(delta),
        'paired_on_time_settlement_change_per_s_range':[min(delta),max(delta)],
        'interpretation':'Positive change means more timely settlements in parallel; negative means fewer. Refusals and safe stops remain separate outcomes.'}
    return result
