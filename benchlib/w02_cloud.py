"""Owned OCI W02 allocation, frozen comparison design, and telemetry gates."""
import hashlib
import copy
import json
import math
import random
import re

from benchlib.analysis import percentile
from benchlib.managed_recovery import engine_failover_spec
from benchlib.w02_qualification import audit


def database_spec(source, run_id):
    result = engine_failover_spec(source, run_id)
    # W02 is not a failover experiment: match the one-node reference allocation.
    result.update(instance_count=1, display_name='fluxnova-w02-'+run_id[:12],
                  description='Temporary isolated W02 synthetic workflow comparison')
    result['freeform_tags'] = {'managed_by':'fluxnova-benchmarks','w02_run_id':run_id,
                              'temporary_comparison':'true','comparison_lane':'w02',
                              'campaign_budget_usd':'300'}
    return result


def require_owned_database(target, *, source_id, target_id, run_id):
    tags = target.get('freeform_tags') or {}
    if (not isinstance(run_id,str) or not re.fullmatch('[0-9a-f]{32}',run_id)
        or not source_id or not target_id or source_id == target_id
        or target.get('id') != target_id or target.get('display_name') != 'fluxnova-w02-'+run_id[:12]
        or any(tags.get(k) != v for k,v in {'managed_by':'fluxnova-benchmarks','w02_run_id':run_id,
                 'temporary_comparison':'true','comparison_lane':'w02'}.items())
        or (target.get('source') or {}).get('source_type') != 'NONE' or target.get('instance_count') != 1):
        raise ValueError('Target differs from the retained W02 allocation identity')


def measurement_plan(run_id, rate):
    if not re.fullmatch('[0-9a-f]{32}',run_id) or rate not in (.5,1):
        raise ValueError('Invalid W02 plan inputs')
    first = ['sequential']*3 + ['parallel']*2
    seed = hashlib.sha256(('w02-pair-order-v1:'+run_id).encode()).hexdigest()
    random.Random(seed).shuffle(first)
    return {'protocol':'w02-cloud-v1','pair_order_seed':seed,
            'pairs':[[arm,'parallel' if arm=='sequential' else 'sequential'] for arm in first],
            'rate_per_s':rate,'warmup_s':120,'measurement_s':600,'drain_s':30,
            'deadline_s':2,'poll_interval_ms':100,'fixed_vus':16,'worker_slots':2,
            'only_changed_control':'BPMN check ordering: sequential versus parallel',
            'primary_outcome':'paired change in observed completion p95',
            'secondary_outcomes':['observed p99','correct outcomes within 2 seconds per second',
                                  'errors/unknowns/drops','dependency receipt overlap'],
            'population':'Synthetic approved payments; deterministic 400 ms KYC and 600 ms risk checks',
            'stop_rule':'Stop on evidence, correctness, safety, reset, or generator validity failure; retain all arms. Deadline misses alone are a result, not a reason to select or discard a measured arm.',
            'topology':'Engine, generator, backend and two-slot worker share an existing controller; separate single-node OCI PostgreSQL. Not an absolute comparison with the four-host P01 reference.'}


def assess_telemetry(rows, execution):
    issues=[]
    if len(rows)<3:
        return {'complete':False,'headroom':False,'issues':['too_few_telemetry_samples']}
    times=[r.get('monotonic_s') for r in rows]
    valid_times=all(isinstance(t,(int,float)) and math.isfinite(t) for t in times)
    if not valid_times or any(b<=a for a,b in zip(times,times[1:])):
        return {'complete':False,'headroom':False,'issues':['invalid_telemetry_clock']}
    if times[0]>execution['started_monotonic_s'] or times[-1]<execution['finished_monotonic_s']:
        issues.append('telemetry_does_not_cover_execution')
    if max(b-a for a,b in zip(times,times[1:]))>15:
        issues.append('telemetry_gap_over_15s')
    cpu=[]; memory=[]
    for i,row in enumerate(rows):
        try:
            if row.get('error'):
                raise ValueError('collection_error')
            host=row['host']
            memory.append(host['memory_available_bytes']/host['memory_total_bytes'])
            if i:
                previous=rows[i-1]['host']
                total=host['cpu_total_ticks']-previous['cpu_total_ticks']
                idle=host['cpu_idle_ticks']-previous['cpu_idle_ticks']
                if total<=0 or not 0<=idle<=total:
                    raise ValueError('invalid_cpu_delta')
                cpu.append((1-idle/total)*100)
            if set(row['containers']) != {'engine','backend','worker'}:
                raise ValueError('missing_container_role')
            for stats in row['containers'].values():
                if not stats['running'] or stats['oom_killed'] or stats['restart_count']:
                    raise ValueError('container_unhealthy')
                if not isinstance(stats['cpu_percent'],(int,float)) or not math.isfinite(stats['cpu_percent']) or stats['cpu_percent']<0:
                    raise ValueError('invalid_container_cpu')
                if not stats['memory_usage']:
                    raise ValueError('missing_container_memory')
            if not all(k in row['database'] for k in ['commits','deadlocks','connections','active','lock_waiters','server_version','tls']):
                raise ValueError('incomplete_database_metrics')
            if row['database']['tls'] is not True or row['database']['lock_waiters']<0:
                raise ValueError('invalid_database_metrics')
            if not isinstance(row['database']['server_version'],str) or not row['database']['server_version']:
                raise ValueError('missing_database_version')
            for key in ['commits','deadlocks','connections','active','lock_waiters']:
                if type(row['database'][key]) is not int or row['database'][key]<0:
                    raise ValueError('invalid_database_counter')
            if not all(k in row['engine'] for k in ['active_instances','external_tasks','incidents','jobs']):
                raise ValueError('incomplete_engine_metrics')
            if row['engine']['incidents']:
                raise ValueError('engine_incident')
        except (KeyError,TypeError,ValueError,ZeroDivisionError) as e:
            issues.append('invalid_telemetry_sample_'+str(i)+':'+type(e).__name__)
    complete=not issues
    headroom=bool(complete and cpu and percentile(cpu,.95)<80 and min(memory)>=.2)
    if not headroom: issues.append('host_headroom_not_established')
    return {'complete':complete,'headroom':headroom,'issues':issues,'samples':len(rows),
            'host_cpu_p95_percent':percentile(cpu,.95),'minimum_memory_available_fraction':min(memory) if memory else None,
            'limits':{'host_cpu_p95_below_percent':80,'available_memory_at_least_fraction':.2,'max_sample_gap_s':15},
            'scope':'Shared Linux controller headroom plus engine/backend/worker and SQL telemetry; not managed DB CPU or full distributed tracing.'}


def assess_cloud(events, summary, execution, calibration, final_state, telemetry, reset, *, corpus=None):
    result=audit(events,summary,execution['returncode'],calibration,final_state,
                 corpus=corpus,require_deadlines=corpus is None)
    telemetry_result=assess_telemetry(telemetry,execution)
    issues=list(result['issues'])+telemetry_result['issues']
    if reset.get('verified') is not True or reset.get('expected_fingerprint') != reset.get('restored_fingerprint') or not reset.get('expected_fingerprint'):
        issues.append('checkpoint_reset_not_verified')
    # The accounting audit treats deadline misses as a qualification failure.
    # In frozen measurements, keep them as performance outcomes, not exclusions.
    invalid=[issue for issue in issues if issue!='measurement_deadline_miss']
    result.update(scope='OCI W02 synthetic workflow comparison',status='passed' if not issues else 'failed',
                  measurement_valid=not invalid,issues=issues,telemetry=telemetry_result,
                  cloud_generator_headroom_validated=telemetry_result['headroom'] and result['loopback_http_headroom_passed'],
                  cloud_telemetry_complete=telemetry_result['complete'],cloud_resources_accessed=True,
                  performance_claim=None,checkpoint_reset=reset)
    result['observed_backlog']['limit']='Declared guard at 0.5 or 1 start/s; not a maximum-capacity estimate.'
    return result


def require_matched_controls(left,right):
    def normalized(value):
        value=copy.deepcopy(value)
        for container in value.get('containers',{}).values():
            mounts=container.get('mounts',[])
            if not isinstance(mounts,list) or len({m['Destination'] for m in mounts})!=len(mounts):
                raise ValueError('Ambiguous W02 container mount controls')
            # Docker emits this map-derived list in varying order. Preserve every
            # field, including RW, while ignoring only the enumeration order.
            container['mounts']=sorted(mounts,key=lambda m:json.dumps(m,sort_keys=True))
        return value
    if normalized(left) != normalized(right):
        raise ValueError('W02 arm controls changed beyond the BPMN order')
