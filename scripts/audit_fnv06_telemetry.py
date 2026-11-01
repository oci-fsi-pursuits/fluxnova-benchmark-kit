#!/usr/bin/env python3
"""Independently bind saved resource observations to actual FNV-06 work."""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.audit_fnv06_load_qualification import audit as audit_outcomes
from scripts.audit_fnv06_resources import audit_window


def read(p): return json.loads(Path(p).read_text())
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def finite(v): return type(v) in (int,float) and math.isfinite(v)
def rows(p): return [json.loads(line) for line in Path(p).read_text().splitlines()]


def check_clocks(sequence, origin, *, max_read_s, max_gap_s):
    assert sequence
    previous = None
    for row in sequence:
        assert not row.get('error')
        for side in ('started','finished'):
            wall,mono = row[side+'_epoch_ms'],row[side+'_monotonic_s']
            assert finite(wall) and finite(mono)
            assert abs(wall/1000-mono-origin) <= .05, 'Clock disagreement'
        assert 0 <= row['finished_monotonic_s']-row['started_monotonic_s'] <= max_read_s
        assert row['started_epoch_ms'] <= row['finished_epoch_ms']
        if previous:
            assert previous['finished_monotonic_s'] < row['started_monotonic_s']
            if max_gap_s is not None:
                assert row['started_monotonic_s']-previous['started_monotonic_s'] <= max_gap_s
        previous = row


def bracket(sequence, start, finish):
    before = [i for i,r in enumerate(sequence) if r['finished_epoch_ms'] <= start]
    after = [i for i,r in enumerate(sequence) if r['started_epoch_ms'] >= finish]
    assert before and after, 'Missing resource window coverage'
    return sequence[max(before):min(after)+1]


def work_window(folder, fixture, origin, requests):
    observer = read(folder/'observer.json'); actions = read(folder/'actions.json')
    c = observer['clock_origin']; release = fixture['release_epoch_ms']
    assert all(finite(v) for v in c.values())
    assert 0 <= c['finished_monotonic_s']-c['started_monotonic_s'] <= .01
    midpoint = (c['started_monotonic_s']+c['finished_monotonic_s'])/2
    assert abs(c['epoch_ms']/1000-midpoint-origin) <= .05
    assert abs(c['release_monotonic_s']-(midpoint+(release-c['epoch_ms'])/1000)) < .000001
    for poll in observer['polls']:
        request = requests[poll['request_index']]
        for side in ('start','finish'):
            timestamp = datetime.fromisoformat(request[side+'ed_at'])
            assert timestamp.tzinfo is not None
            assert abs(timestamp.timestamp()*1000-(release+poll[side+'_elapsed_s']*1000)) <= 50
    finish = max(release+observer['polls'][-1]['finish_elapsed_s']*1000,
                 max(a['finished_epoch_ms'] for a in actions))
    ended = observer['finished_clock']
    assert finite(ended['epoch_ms']) and finite(ended['monotonic_s'])
    assert abs(ended['epoch_ms']/1000-ended['monotonic_s']-origin) <= .05
    assert release < finish <= ended['epoch_ms']+50
    return release,finish


def database_window(sequence, start, finish, origin, role):
    selected = bracket(sequence,start,finish)
    check_clocks(selected,origin,max_read_s=.9,max_gap_s=1)
    pid = selected[0]['value']['backend_pid']; assert type(pid) is int and pid > 0
    previous = None; waits = set(); maximum_sessions = 0
    for row in selected:
        # Query bytes are checked by audit() against the sealed, reviewed source.
        value = row['value']
        assert value['database'] == 'postgres' and value['user'] == role and value['backend_pid'] == pid
        assert value['tls'] is True and value['track_activities'] == value['track_counts'] == 'on'
        assert finite(value['server_epoch_ms'])
        assert row['started_epoch_ms']-50 <= value['server_epoch_ms'] <= row['finished_epoch_ms']+50
        activity = value['activity']; assert isinstance(activity,list)
        keys = set(); sessions = 0
        for item in activity:
            key = (item['state'],item['wait_event_type'],item['wait_event'])
            assert key not in keys and isinstance(key[0],str)
            assert all(v is None or isinstance(v,str) for v in key[1:])
            assert (key[1] is None) == (key[2] is None)
            keys.add(key); assert type(item['sessions']) is int and item['sessions'] > 0
            sessions += item['sessions']
            if key[1] is not None: waits.add(key[1]+':'+key[2])
        maximum_sessions = max(maximum_sessions,sessions)
        stats = value['statistics']; assert set(stats) == {'stats_reset','xact_commit','xact_rollback','blks_read','blks_hit','deadlocks','temp_bytes'}
        for key,v in stats.items():
            if key == 'stats_reset':
                assert v is None or isinstance(v,str)
                if previous is not None: assert v == previous[key], 'Database statistics reset'
            else:
                assert type(v) is int and v >= 0
                if previous is not None: assert v >= previous[key], 'Database counter regressed'
        previous = stats
    first = selected[0]['value']['statistics']; last = selected[-1]['value']['statistics']
    return {'status':'observations_verified','samples_covering_window':len(selected),
            'maximum_observed_owned_role_sessions':maximum_sessions,'observed_wait_events':sorted(waits),
            'trial_database_counter_deltas':{k:last[k]-first[k] for k in first if k != 'stats_reset'},
            'scope':'Sampled role activity and lagging database-wide counters, including observer overhead. No database CPU, memory, or maximum-capacity conclusion.'}


def audit(folder, *, control=None):
    folder = Path(folder).resolve()
    outcomes = audit_outcomes(folder,outcome_accounting=True)
    assert outcomes['independent_outcomes_verified']
    meta = read(folder/'identity.json'); source = read(folder/'source-manifest.json')
    fault = None
    if meta.get('controlled_fault') == 'timeout_capture_C01' and meta['qualification_cell']['id'] == 'C01':
        assert control is not None, 'Declared fault requires its sealed independent control'
        from scripts.fnv06_timeout_evidence import verify_control
        fault = verify_control(control,folder,for_outcome_reset=True)
        assert outcomes['request_accounting']['timeout'] == 4 and outcomes['request_accounting']['failed'] == 4
    else: assert control is None
    path = 'workloads/fnv06/resource-observation.json'; protocol_file = folder/'inputs'/path
    canonical = Path(__file__).resolve().parents[1]/path
    assert protocol_file.read_bytes() == canonical.read_bytes().replace(b'\r\n',b'\n')
    assert sha(protocol_file) == meta['resource_protocol_sha256'] == source['files'][path]
    assert meta['resource_phase'] == 'workload_resources_v1'
    resource = folder/'resources'; finished = read(resource/'host-finished.json')
    assert finished['status'] == 'collected' and finished['readers_stopped'] is True
    assert not finished['stop_errors'] and not finished['sampler_errors'] and finished['error_type'] is None
    identity = {k:meta[k] for k in ('run_id','source_commit','qualification_cell','resource_protocol_sha256')}
    containers = rows(resource/'containers.ndjson'); database = rows(resource/'database.ndjson')
    assert len(containers) == finished['samples']['containers.ndjson'] and len(database) == finished['samples']['database.ndjson']
    assert len(containers) >= 3 and len(database) >= 3
    origin = containers[0]['started_epoch_ms']/1000-containers[0]['started_monotonic_s']
    # Gaps outside work remain reported, but only work-window gaps decide coverage.
    check_clocks(containers,origin,max_read_s=.1,max_gap_s=None)
    check_clocks(database,origin,max_read_s=.9,max_gap_s=None)
    stamps = []
    for name in ('job-ready.json','host-ready.json','job-finished.json','host-finished.json'):
        item = read(resource/name); assert item['binding'] == identity
        c = item['clock']; assert finite(c['epoch_ms']) and finite(c['monotonic_s'])
        assert abs(c['epoch_ms']/1000-c['monotonic_s']-origin) <= .05
        stamps.append(c['monotonic_s'])
    assert stamps == sorted(stamps) and len(set(stamps)) == 4
    assert read(resource/'host-ready.json')['readers_ready'] is True
    job_finish = read(resource/'job-finished.json')['clock']['epoch_ms']
    assert containers[-1]['started_epoch_ms'] > job_finish and database[-1]['started_epoch_ms'] > job_finish
    close = read(folder/'engine-closeout.json')
    assert finished['database_binding'] == {'target_id':meta['target_id'],'host':meta['connection']['primary_db_endpoint']['fqdn'],
                                           'role':'fnv06_'+meta['run_id'][:12]}
    for role, sample_role in (('engine','engine'),('job','generator')):
        declared = finished['container_bindings'][role]
        assert declared['container_id'] == close['containers'][role]['id']
        assert re.fullmatch('[0-9a-f]{64}',declared['container_id'])
        assert declared['owner'] == meta['run_id'] and declared['name'] == 'fnv06-'+meta['run_id'][:12]+'-'+role
        assert declared['image'] == meta['images']['engine' if role == 'engine' else 'services']
        assert (declared['nano_cpus'],declared['memory_bytes']) == ((2000000000,4294967296) if role == 'engine' else (1000000000,536870912))
        for row in containers:
            b = row['containers'][sample_role]['binding']
            assert b['container_id'] == declared['container_id'] and b['pid'] == declared['host_pid']
            assert b == containers[0]['containers'][sample_role]['binding']
            raw = row['containers'][sample_role]['files']
            quota,period = map(int,raw['cpu.max'].split())
            assert quota*1000000000 == period*declared['nano_cpus'] and int(raw['memory.max']) == declared['memory_bytes']
    # Read the SQL literal without importing or executing the retained collector.
    import ast
    tree = ast.parse((folder/'inputs/benchlib/fnv06_telemetry.py').read_text())
    query = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                 and any(isinstance(t,ast.Name) and t.id == 'DB_QUERY' for t in n.targets))
    canonical_tree = ast.parse((Path(__file__).resolve().parents[1]/'benchlib/fnv06_telemetry.py').read_text())
    canonical_query = next(ast.literal_eval(n.value) for n in canonical_tree.body if isinstance(n,ast.Assign)
                 and any(isinstance(t,ast.Name) and t.id == 'DB_QUERY' for t in n.targets))
    assert query == canonical_query and all(row['query'] == query for row in database)
    requests = rows(folder/'requests.ndjson')
    windows = []
    for diagnostic in outcomes['diagnostics']:
        case = diagnostic['id']; obs = folder/'observations'/case
        fixture = read(obs/'driver-input.json')
        start,finish = work_window(obs,fixture,origin,requests)
        generator = audit_window(containers,start_epoch_ms=start,finish_epoch_ms=finish)
        engine = audit_window(containers,start_epoch_ms=start,finish_epoch_ms=finish,generator_role='engine',role_kind='engine')
        db = database_window(database,start,finish,origin,finished['database_binding']['role'])
        windows.append({'id':case,'phase':'warmup' if case.endswith('W') else 'test',
                        'window_epoch_ms':[start,finish], 'generator_and_observer':generator,'engine':engine,'database':db})
    valid = all(w['generator_and_observer']['generator_headroom_passed'] for w in windows)
    return {'study_id':'FNV-06-RESOURCE-OBSERVATION','status':'observations_verified' if valid else 'observations_verified_generator_constrained',
            'source_commit':meta['source_commit'],'resource_evidence_verified':True,'generator_headroom_passed':valid,
            'resource_protocol_sha256':sha(protocol_file),'source_checksums_sha256':sha(folder/'checksums.json'),
            'maximum_full_stream_gap_s':{name:max(b['started_monotonic_s']-a['started_monotonic_s'] for a,b in zip(seq,seq[1:]))
                                         for name,seq in (('containers',containers),('database',database))},
            'auditor_sha256':sha(Path(__file__)),'resource_window_auditor_sha256':sha(Path(__file__).with_name('audit_fnv06_resources.py')),
            'request_failures_retained':outcomes['request_accounting']['failed'],'windows':windows,
            'controlled_fault':fault,
            'scope':'Actual finite-work observations; generator includes Python collection. No database-host headroom, maximum capacity or repeated-performance claim.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True); parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--control',type=Path,help='Sealed deliberate-fault control, only for declared C01')
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(args.source.resolve()): parser.error('Use a fresh output outside sealed evidence.')
    try: result = audit(args.source,control=args.control)
    except (AssertionError,KeyError,ValueError,TypeError,FileNotFoundError,IndexError,StopIteration) as error:
        result = {'study_id':'FNV-06-RESOURCE-OBSERVATION','status':'not_verified','resource_evidence_verified':False,
                  'generator_headroom_passed':None,'error_type':type(error).__name__}
    args.output.mkdir(parents=True); (args.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'windows'}))
    if not result['resource_evidence_verified']: raise SystemExit(1)


if __name__ == '__main__': main()
