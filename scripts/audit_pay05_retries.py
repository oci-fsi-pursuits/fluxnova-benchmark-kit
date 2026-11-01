#!/usr/bin/env python3
"""Post-run PAY-05 retry diagnosis: bind engine HTTP errors to audited receipts."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.audit_pay05_performance import verify_seal


def read(path): return json.loads(path.read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def reconcile(engine_log, records):
    """Every logged HTTP 500 must match one cached replay in a known execution.

    Counts bind by process identity and generator interval, not just grand totals.
    Worker error types and task identities are checked separately. This explains
    retained retries; it neither re-runs nor changes the performance experiment.
    """
    known, expected, lanes, tasks = {}, Counter(), {}, set()
    for record in records:
        name, arm, stage = record['folder'], record['arm'], record['stage']
        assert name not in lanes and arm in ('sequential', 'parallel')
        assert stage in ('qualification', 'performance'), 'Unknown study stage'
        completed = [e for e in record['events'] if e['event'] == 'completed']
        worker = record['worker']['records']; done = [r for r in worker if r['status'] == 'completed']
        failed = [r for r in worker if r['status'] == 'retry_or_incident']
        assert len(worker) == len(done) + len(failed), 'Unclassified worker failures'
        assert len(done) == 4 * len(completed) and len({r['task_id'] for r in done}) == len(done)
        by_task = {r['task_id']: r for r in done}
        assert not tasks.intersection(by_task); tasks.update(by_task)
        assert all(r['error_type'] == 'HTTPError' and r['task_id'] in by_task
                   and r['operation'] == by_task[r['task_id']]['operation'] for r in failed)
        phases, operations = Counter(), Counter(r['operation'] for r in failed)
        for event in completed:
            pid = event['details']['process_instance_id']; assert pid not in known
            assert event['phase'] in ('warmup', 'measurement')
            history = event['details']['history']['instance']
            assert history['id'] == pid and history['processDefinitionId'].startswith('bench_pay05_' + arm + ':')
            known[pid] = {'lane': name, 'phase': event['phase'], 'definition': history['processDefinitionId'],
                          'start': datetime.fromisoformat(record['execution']['started_at']),
                          'end': datetime.fromisoformat(record['execution']['finished_at'])}
            count = sum(r['event'] == 'replay' for r in event['details']['backend']['audit'])
            if count: expected[pid] = count
            phases[event['phase']] += count
        assert sum(phases.values()) == len(failed), 'Worker retry/backend replay counts differ'
        lanes[name] = {'arm': arm, 'stage': stage, 'worker_retries_including_warmup': len(failed),
                       'worker_retry_operations': dict(operations), 'backend_replays_by_phase': dict(phases),
                       'engine_optimistic_lock_http500_by_phase': {'warmup': 0, 'measurement': 0}}
    observed = Counter(); failures = []
    pattern = re.compile(r'^(\S+) .*ENGINE-REST-HTTP500 org\.finos\.fluxnova\.bpm\.engine\.OptimisticLockingException: ENGINE-03005 .*'
                         r'ProcessDefinitionId: ([^,]+), ProcessInstanceId: ([^,]+),')
    for line in engine_log.splitlines():
        if 'ENGINE-REST-HTTP500' not in line: continue
        match = pattern.search(line)
        assert match, 'Unclassified engine HTTP 500'
        at, definition, pid = match.groups(); assert pid in known, 'Unknown failed process'
        entry = known[pid]; instant = datetime.fromisoformat(at.replace('Z', '+00:00'))
        assert definition == entry['definition'] and entry['start'] <= instant <= entry['end'], 'Error outside bound process/window'
        observed[pid] += 1
        lanes[entry['lane']]['engine_optimistic_lock_http500_by_phase'][entry['phase']] += 1
        failures.append(pid)
    assert observed == expected, 'Engine errors and backend replays differ by process identity'
    assert all(not v['worker_retries_including_warmup'] for v in lanes.values() if v['arm'] == 'sequential')
    measured = {arm: sum(v['backend_replays_by_phase'].get('measurement', 0) for v in lanes.values()
                        if v['arm'] == arm and v['stage'] == 'performance')
                for arm in ('sequential', 'parallel')}
    grouped = {stage: {phase: {arm: sum(v['backend_replays_by_phase'].get(phase, 0) for v in lanes.values()
                       if v['arm'] == arm and v['stage'] == stage) for arm in ('sequential', 'parallel')}
                       for phase in ('warmup', 'measurement')} for stage in ('qualification', 'performance')}
    return {'status': 'passed', 'engine_optimistic_lock_http500': len(failures), 'affected_processes': len(observed),
            'all_errors_matched_by_process_identity_and_time': True, 'measured_backend_replays': measured,
            'measured_scope': 'Five performance pairs, excluding qualification and warmup',
            'backend_replays_by_stage_phase_arm': grouped,
            'lanes': lanes, 'interpretation': 'Concurrent execution-entity updates produced engine HTTP 500 optimistic-lock errors. Worker HTTP-error retries match cached backend replays, with outcomes verified by the separate performance audit.',
            'scope': 'Post-run diagnosis of the retained attempt; not a new optimization comparison or a count of every network request.'}


def audit(source, engine_log):
    verify_seal(source); summary = read(source / 'summary.json')
    assert summary['status'] == 'passed' and summary['performance_comparison_complete']
    records = []
    for stage, item in ([('qualification', v) for v in summary['qualification']]
                        + [('performance', v) for v in summary['measurements']]):
        folder = source / item['folder']
        records.append({'folder': item['folder'], 'arm': item['arm'], 'stage': stage, 'worker': read(folder / 'worker-records.json'),
            'execution': read(folder / 'execution.json'),
            'events': [json.loads(line) for line in (folder / 'events.ndjson').read_text().splitlines()]})
    result = reconcile(engine_log.read_text(encoding='utf-8'), records)
    result.update(source_commit=read(source / 'source-manifest.json')['commit'],
                  source_checksums_sha256=sha(source / 'checksums.json'), engine_log_sha256=sha(engine_log), auditor_sha256=sha(Path(__file__)))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True); p.add_argument('--engine-log', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); args = p.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(args.source.resolve()): p.error('Use a fresh output outside the sealed study.')
    result = audit(args.source, args.engine_log); args.output.mkdir(parents=True)
    (args.output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'lanes'}))


if __name__ == '__main__': main()
