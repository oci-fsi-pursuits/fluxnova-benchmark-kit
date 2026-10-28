"""W01 accounting and independent original-payment outcome checks."""
from collections import Counter, defaultdict
import hashlib
import json
import math

from benchlib.analysis import distribution, percentile


def payload():
    return {'payload':'w01-synthetic-payment','authorized':True,'approved':False,
            'sanctions_match':False,'high_risk':False,'amount_minor':125,
            'limit_minor':1000,'notional_minor':100,'stale_market':False}


def require_outcome(event):
    d=event['details']; key=event['logical_request_id']; h=d['history']
    assert h['id']==d['process_id'] and h['businessKey']==key and h['state']=='COMPLETED'
    variables={v['name']:v['value'] for v in d['variables']}
    assert len(variables)==len(d['variables']), 'duplicate_history_variable'
    expected={**payload(),'scenario':'settlement','logical_request_id':key,'fault':'none','delay_ms':0}
    assert all(variables.get(k)==v for k,v in expected.items()), 'original_input_mismatch'
    assert variables['workerResult']=='settled'
    ends=[x['activityId'] for x in d['activities'] if x['activityType'].lower().endswith('endevent') and x.get('endTime')]
    assert ends==['settled'], 'wrong_terminal_state'
    state=d['backend']
    fingerprint=hashlib.sha256(json.dumps({'operation':'settle','payload':payload()},
        sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert state['idempotency_key']==key and state['fingerprint']==fingerprint
    assert state['result']=={'idempotency_key':key,'operation':'settle','outcome':'settled'}
    assert state['effect_count']==1 and not state['violations']
    effects=[x['details'] for x in state['events'] if x['event']=='payment_effect']
    assert effects==[{'amount_minor':125,'beneficiary':'synthetic-beneficiary'}], 'wrong_original_effect'
    decisions=[x['details'] for x in state['events'] if x['event']=='policy_decision']
    assert decisions==[{'allowed':True,'authorized':True,'approved':False,'approval_required':False}]
    assert event['status']=='correct'


def audit(events, summary, final_state, returncode):
    settings=summary['w01'];issues=[];cohorts={}
    all_processes=[]
    if any(r.get('run_id')!=settings['run_id'] or r.get('arm')!=settings['arm'] for r in events):
        issues.append('mixed_run_or_arm')
    if settings['burst_rate']!=20 or settings['burst_duration_s']!=1 or settings['period_s']!=10:
        issues.append('burst_schedule_changed')
    if settings['deadline_s']!=2 or settings['poll_interval_ms']!=100 or settings['fixed_vus_per_burst']!=32:
        issues.append('observation_or_generator_controls_changed')
    for phase,bursts in [('warmup',settings['warmup_bursts']),('measurement',settings['measurement_bursts'])]:
        rows=[r for r in events if r['phase']==phase];group=defaultdict(list)
        for row in rows:group[row['logical_request_id']].append(row)
        expected={f'{settings["run_id"]}:{phase}:{b}:{i}' for b in range(bursts) for i in range(20)}
        if set(group)!=expected:issues.append(phase+'_arrival_identity_mismatch')
        valid=[];lag=[];invalid=[]
        for key,items in group.items():
            try:
                assert Counter(x['event'] for x in items)=={'started':1,'accepted':1,'completed':1}, 'incomplete_lifecycle'
                start=next(x for x in items if x['event']=='started')
                accepted=next(x for x in items if x['event']=='accepted')
                done=next(x for x in items if x['event']=='completed')
                burst,index=map(int,key.rsplit(':',2)[1:])
                scheduled=burst*10+index/20-(settings['warmup_s'] if phase=='warmup' else 0)
                assert all(abs(x['scheduled_elapsed_s']-scheduled)<1e-6 for x in items), 'arrival_clock_mismatch'
                assert math.isfinite(done['latency_s']) and done['latency_s']>=0
                assert abs(done['elapsed_s']-scheduled-done['latency_s'])<1e-6
                assert start['elapsed_s']<=accepted['elapsed_s']<=done['elapsed_s'], 'lifecycle_clock_order'
                assert accepted['details']['process_id']==done['details']['process_id'], 'process_identity_changed'
                require_outcome(done)
                all_processes.append(done['details']['process_id'])
                valid.append(done);lag.append(max(0,start['elapsed_s']-scheduled))
            except (AssertionError,KeyError,TypeError,ValueError) as e:
                invalid.append({'key':key,'reason':str(e) or type(e).__name__})
        dropped=sum(x['dropped'] for x in settings['scenarios'].values() if x['phase']==phase)
        if len(valid)!=len(expected) or dropped or invalid:issues.append(phase+'_outcome_or_count_failure')
        if not lag or percentile(lag,.99)>.1:issues.append(phase+'_arrival_lag_over_100ms')
        cohorts[phase]={'offered':len(expected),'correct':len(valid),'dropped':dropped,'invalid':invalid,
            'on_time_correct':sum(x['latency_s']<=2 for x in valid),
            'deadline_misses':sum(x['latency_s']>2 for x in valid),
            'arrival_lag_s':distribution(lag),'observed_latency_s':distribution([x['latency_s'] for x in valid])}
    total=sum(x['offered'] for x in cohorts.values())
    if len(set(all_processes))!=len(all_processes):issues.append('process_reused_across_arrivals')
    if final_state!={'active_instances':0,'external_tasks':0,'incidents':0,
                     'completed_instances':total,'effects':total,'receipts':total}:
        issues.append('final_state_mismatch')
    if returncode:issues.append('load_process_failed')
    return {'status':'passed' if not issues else 'failed','issues':issues,'cohorts':cohorts,
        'scope':'Accounting/outcome qualification; local H2 is not OCI performance or maximum capacity',
        'deadline_misses_are_retained_outcomes':True}
