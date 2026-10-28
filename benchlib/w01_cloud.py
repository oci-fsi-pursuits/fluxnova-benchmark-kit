"""Frozen W01 controls and validity rules for an isolated OCI burst study."""
import copy
import hashlib
import random
import re
from collections import Counter

from benchlib.w01_pacing import audit
from benchlib.w02_cloud import database_spec as base_database_spec, assess_telemetry
from benchlib.w02_cloud import require_matched_controls as compare_controls

ARMS=('success_delay_100ms','idle_only')


def database_spec(source,run_id):
    result=base_database_spec(source,run_id)
    result.update(display_name='fluxnova-w01-'+run_id[:12],
                  description='Temporary isolated W01 worker pacing comparison')
    result['freeform_tags']={'managed_by':'fluxnova-benchmarks','w01_run_id':run_id,
        'temporary_comparison':'true','comparison_lane':'w01','campaign_budget_usd':'300'}
    return result


def require_owned_database(target,*,source_id,target_id,run_id):
    tags=target.get('freeform_tags') or {}
    if (not isinstance(run_id,str) or not re.fullmatch('[0-9a-f]{32}',run_id)
        or not source_id or not target_id or source_id==target_id
        or target.get('id')!=target_id or target.get('display_name')!='fluxnova-w01-'+run_id[:12]
        or any(tags.get(k)!=v for k,v in {'managed_by':'fluxnova-benchmarks','w01_run_id':run_id,
                'temporary_comparison':'true','comparison_lane':'w01'}.items())
        or (target.get('source') or {}).get('source_type')!='NONE' or target.get('instance_count')!=1):
        raise ValueError('Target differs from the retained W01 allocation identity')


def measurement_plan(run_id):
    if not re.fullmatch('[0-9a-f]{32}',run_id):raise ValueError('Invalid W01 run identity')
    first=[ARMS[0]]*3+[ARMS[1]]*2
    seed=hashlib.sha256(('w01-pair-order-v1:'+run_id).encode()).hexdigest()
    random.Random(seed).shuffle(first)
    return {'protocol':'w01-cloud-v1','pair_order_seed':seed,
        'pairs':[[arm,ARMS[1] if arm==ARMS[0] else ARMS[0]] for arm in first],
        'warmup_bursts':12,'measurement_bursts':12,
        'warmup_s':120,'measurement_s':120,'starts_per_burst':20,
        'burst_duration_s':1,'burst_period_s':10,'mean_offered_rate_per_s':2,
        'deadline_s':2,'poll_interval_ms':100,'drain_s':20,'worker_slots':1,
        'fixed_vus_per_burst':32,'worker_idle_error_backoff_s':.1,
        'only_changed_control':'Worker delay after successful task: 100 ms versus zero',
        'primary_outcome':'paired difference in correct on-time payments per measurement second',
        'secondary_outcomes':['paired observed p95 and p99','late outcomes','offered/started/correct/dropped',
                              'observed outstanding work','resource samples'],
        'population':'Approved synthetic settlement bursts; reconstructed historical pacing control, not a current recommended default',
        'stop_rule':'Stop on invalid evidence, unsafe/wrong/unknown outcomes, reset or generator failure. Retain deadline misses as outcomes.',
        'topology':'Colocated controller engine/generator/backend/worker and fresh single-node OCI PostgreSQL; not a comparison to P01 or W02 absolute timings.'}


def require_matched_controls(left,right):
    def normalize(value):
        value=copy.deepcopy(value)
        args=value['containers']['worker']['arguments']
        if args.count('--success-delay-s')!=1:raise ValueError('Missing or ambiguous pacing factor')
        index=args.index('--success-delay-s')+1
        if index>=len(args) or args[index] not in ('0','0.0','0.1'):raise ValueError('Unexpected pacing value')
        args[index]='DECLARED_PACING_FACTOR'
        return value
    compare_controls(normalize(left),normalize(right))


def assess_cloud(events,summary,execution,calibration,final_state,telemetry,reset,http):
    result=audit(events,summary,final_state,execution['returncode'])
    observed=assess_telemetry(telemetry,execution)
    issues=list(result['issues'])+observed['issues']
    if (reset.get('verified') is not True or not reset.get('expected_fingerprint')
            or reset.get('expected_fingerprint')!=reset.get('restored_fingerprint')):
        issues.append('checkpoint_reset_not_verified')
    buckets=Counter(int(r['elapsed_s']//1) for r in http)
    peak=max(buckets.values(),default=0)
    calibrated=bool(peak and calibration.get('valid') and calibration['requested_http_rate']>=2*peak)
    if not calibrated:issues.append('calibration_below_twice_peak_observed_http_bucket')
    for phase,cohort in result['cohorts'].items():
        starts={r['logical_request_id']:r['elapsed_s'] for r in events if r['phase']==phase and r['event']=='started'}
        ends={r['logical_request_id']:r['elapsed_s'] for r in events if r['phase']==phase and r['event']=='completed'}
        points=sorted(set(starts.values())|set(ends.values()))
        maximum=max((sum(t<=now<ends.get(key,float('inf')) for key,t in starts.items()) for now in points),default=0)
        cohort['max_observed_outstanding']=maximum
        # 20 arrivals per burst, ten-second separation. More than 40 outstanding
        # means at least two complete bursts have failed to drain.
        if maximum>40:issues.append(phase+'_backlog_over_declared_40')
    result.update(status='passed' if not issues else 'failed',measurement_valid=not issues,
        issues=issues,scope='OCI W01 synthetic payment burst comparison',
        telemetry=observed,cloud_generator_headroom_validated=observed['headroom'] and calibrated,
        cloud_telemetry_complete=observed['complete'],
        peak_http_per_1s_bucket=peak,loopback_http_headroom_passed=calibrated,
        checkpoint_reset=reset,performance_claim=None)
    return result
