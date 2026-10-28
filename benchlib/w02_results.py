"""Paired W02 reporting; incomplete/invalid series never become improvement claims."""
from statistics import median


def summarize_pairs(plan, records):
    if len(plan['pairs'])!=5 or any(set(p)!= {'sequential','parallel'} or len(p)!=2 for p in plan['pairs']):
        raise ValueError('Expected the complete five-pair W02 design')
    expected={f'p{i}-{j}-{arm}':(i,arm) for i,pair in enumerate(plan['pairs'],1) for j,arm in enumerate(pair,1)}
    if len({r['folder'] for r in records})!=len(records) or any(r['folder'] not in expected for r in records):
        raise ValueError('Duplicate or undeclared measurement arm')
    arms=[]
    for record in records:
        result=record['assessment'];measurement=result['cohorts']['measurement'];warmup=result['cohorts']['warmup']
        pair,arm=expected[record['folder']]
        if result['arm']!=arm or result['offered_rate']!=plan['rate_per_s']:
            raise ValueError('Arm or rate differs from the frozen plan')
        latency=measurement['observed_latency_s']
        arms.append({'pair':pair,'arm':arm,'measurement_valid':result['measurement_valid'],'issues':result['issues'],
            'offered':measurement['offered'],'correct':measurement['correct'],'on_time_correct':measurement['on_time_correct'],
            'dropped':measurement['dropped'],'unknown_or_invalid':len(measurement['invalid_or_unknown']),
            'observed_p95_ms':latency['p95']*1000 if latency['p95'] is not None else None,
            'observed_p99_ms':latency['p99']*1000 if latency['p99'] is not None else None,
            'deadline_goodput_per_s':measurement['on_time_correct']/plan['measurement_s'],
            'warmup_correct':warmup['correct'],'warmup_offered':warmup['offered']})
    complete=len(arms)==10 and all(r['measurement_valid'] for r in arms)
    result={'status':'complete' if complete else 'incomplete_or_invalid','completed_arms':len(arms),'planned_arms':10,
            'population':plan['population'],'rate_per_s':plan['rate_per_s'],'measurement_s_per_arm':plan['measurement_s'],
            'deadline_s':plan['deadline_s'],'poll_interval_ms':plan['poll_interval_ms'],'source_commit':plan['source_commit'],
            'arms':arms,'comparison':None,
            'limits':['Synthetic deterministic approved-payment trace; negative cases are qualified separately.',
                      'Observed latency includes 100 ms completion polling.',
                      'Five pairs; report their range without a significance or production-capacity claim.',
                      'Shared-controller OCI topology is not an absolute comparison with P01.']}
    if not complete:return result
    paired=[]
    for i in range(1,6):
        rows={r['arm']:r for r in arms if r['pair']==i};a,b=rows['sequential'],rows['parallel']
        if a['observed_p95_ms'] is None or a['observed_p95_ms']<=0 or b['observed_p95_ms'] is None:
            raise ValueError('Missing valid paired p95 observations')
        paired.append({'pair':i,'sequential_p95_ms':a['observed_p95_ms'],'parallel_p95_ms':b['observed_p95_ms'],
                       'parallel_p95_change_percent':100*(b['observed_p95_ms']/a['observed_p95_ms']-1),
                       'parallel_p95_change_ms':b['observed_p95_ms']-a['observed_p95_ms']})
    changes=[r['parallel_p95_change_percent'] for r in paired]
    per_arm={}
    for arm in ('sequential','parallel'):
        rows=[r for r in arms if r['arm']==arm]
        per_arm[arm]={'median_of_run_p95_ms':median(r['observed_p95_ms'] for r in rows),
                      'run_p95_range_ms':[min(r['observed_p95_ms'] for r in rows),max(r['observed_p95_ms'] for r in rows)],
                      'median_of_run_p99_ms':median(r['observed_p99_ms'] for r in rows),
                      'correct':sum(r['correct'] for r in rows),'offered':sum(r['offered'] for r in rows),
                      'on_time_correct':sum(r['on_time_correct'] for r in rows),'dropped':sum(r['dropped'] for r in rows),
                      'deadline_goodput_per_s':sum(r['on_time_correct'] for r in rows)/(5*plan['measurement_s'])}
    result['comparison']={'paired_runs':paired,'arms':per_arm,'median_paired_p95_change_percent':median(changes),
                          'paired_p95_change_percent_range':[min(changes),max(changes)],
                          'interpretation':'Negative change means lower observed parallel p95; positive means higher. Range is across five pairs, not a confidence interval.'}
    return result
