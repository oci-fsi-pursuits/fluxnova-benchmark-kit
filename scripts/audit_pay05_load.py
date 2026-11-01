#!/usr/bin/env python3
"""Audit sealed PAY-05 qualification from events, SQL history and saved ledgers."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchlib.checkpoint import sqlite_fingerprint
from benchlib.pay05_load import assess_cloud, qualification_plan
from benchlib.w02_cloud import require_matched_controls


def read(path): return json.loads(path.read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def digest(value): return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def check_ledger(path, events, sql_history):
    completed = {e['logical_request_id']: e['details'] for e in events if e['event'] == 'completed'}
    assert len(sql_history) == len(completed)
    history = {r['business_key']: r for r in sql_history}
    assert set(history) == set(completed) and len({r['id'] for r in sql_history}) == len(completed)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        assert [r[0] for r in db.execute('PRAGMA integrity_check')] == ['ok']
        cases = {r['key']: dict(r) for r in db.execute('SELECT * FROM cases')}
        assert set(cases) == set(completed)
        for table in ('receipts', 'effects', 'audit'):
            assert {r[0] for r in db.execute('SELECT DISTINCT key FROM ' + table)} <= set(cases)
        for key, data in completed.items():
            case, backend = cases[key], data['backend']
            assert json.loads(case['body']) == data['original']
            assert case['process_id'] == data['process_instance_id'] == backend['process_id']
            assert case['payment_id'] == data['original']['payment']['payment_id']
            assert case['fingerprint'] == backend['input_sha256'] and case['registered_ns'] == backend['registered_ns']
            assert {r['operation']: json.loads(r['body']) for r in db.execute('SELECT * FROM receipts WHERE key=?', (key,))} == backend['receipts']
            assert [dict(r) for r in db.execute('SELECT * FROM effects WHERE key=?', (key,))] == backend['effects']
            assert [dict(r) for r in db.execute('SELECT operation,event FROM audit WHERE key=? ORDER BY id', (key,))] == backend['audit']
            assert history[key] == {'id': data['process_instance_id'], 'business_key': key,
                'definition_id': data['history']['instance']['processDefinitionId'], 'state': 'COMPLETED'}
    return len(cases)


def audit(folder):
    folder = folder.resolve(); inventory = read(folder / 'checksums.json')
    names = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() and p.name != 'checksums.json'}
    assert names == set(inventory['files'])
    for name, expected in inventory['files'].items():
        path = folder / name
        assert path.resolve().is_relative_to(folder) and not path.is_symlink() and sha(path) == expected
    source = read(folder / 'source-manifest.json')
    for name, expected in source['files'].items(): assert sha(folder / 'inputs' / name) == expected
    corpus = read(folder / 'inputs/workloads/pay05/cases.json'); plan = read(folder / 'plan.json')
    assert {k: plan[k] for k in qualification_plan(corpus)} == qualification_plan(corpus)
    assert plan['source_commit'] == source['commit']
    summary, closeout = read(folder / 'summary.json'), read(folder / 'engine-closeout.json')
    assert summary['status'] == 'passed' and summary['cloud_resources_accessed'] is True
    assert summary['performance_comparison_complete'] is False and summary['owned_engine_stopped']
    assert summary['run_id'] == closeout['run_id'] and not summary['cleanup_errors'] and not closeout['cleanup_errors']
    assert closeout['owned_engine_stopped']
    for role in ('engine', 'backend', 'worker'):
        assert closeout['containers'][role]['stopped'] and not closeout['containers'][role]['oom_killed']
    runtime = read(folder / 'runtime.json')
    assert runtime['jdbc_tls_verify_full'] and runtime['internal_network'] and runtime['engine']['version'] == '3.0.1'
    assert len(runtime['database_tls']) > 1 and all(r[0] for r in runtime['database_tls'])
    probes = read(folder / 'isolation.json')['forbidden_endpoints_blocked']
    assert len(probes) == 5 and all(p['blocked'] for p in probes)
    calibration = read(folder / 'calibration/generator-calibration.json')
    raw = read(folder / 'calibration/generator-calibration-summary.json')['metrics']
    assert calibration['valid'] and calibration['requests'] == raw['http_reqs']['values']['count']
    assert calibration['requests'] >= calibration['requested_http_rate'] * calibration['duration_s']
    assert raw.get('dropped_iterations', {}).get('values', {}).get('count', 0) == calibration['dropped'] == 0
    assert raw['http_req_failed']['values']['rate'] == calibration['error_fraction'] == 0
    baseline = read(folder / 'controls.json'); checkpoint = read(folder / 'checkpoint.json')
    assert baseline['worker_slots'] == 3 and baseline['poll_ms'] == 100 and baseline['worker_idle_s'] == .1
    assert baseline['db_nodes'] == 1 and baseline['db_ocpu'] == 2 and baseline['db_memory_gib'] == 32
    assert baseline['corpus_sha256'] == sha(folder / 'inputs/workloads/pay05/cases.json')
    assert len(summary['lanes']) == 4
    results = []; executions = 0
    for index, settings in enumerate(plan['lanes'], 1):
        lane = folder / ('q' + str(index) + '-' + settings['arm'])
        s, execution = read(lane / 'k6-summary.json'), read(lane / 'execution.json')
        assert s['pay05']['arm'] == settings['arm'] and s['pay05']['rate'] == settings['rate']
        assert s['pay05']['warmup_s'] * settings['rate'] == plan['warmup_arrivals']
        assert s['pay05']['duration_s'] * settings['rate'] == plan['measurement_arrivals']
        assert s['pay05']['drain_s'] == plan['drain_s'] and s['pay05']['fixed_vus'] == plan['fixed_vus']
        assert s['pay05']['corpus_sha256'] == baseline['corpus_sha256']
        require_matched_controls(baseline, read(lane / 'controls.json'))
        restored = read(lane / 'restored-logical.json'); reset = read(lane / 'reset.json')
        assert restored == checkpoint['logical']
        assert reset['expected_fingerprint'] == reset['restored_fingerprint'] == digest(restored)
        assert reset['verified_before_resuming_writers'] is True
        for store in ('previous-state', 'stores'):
            files = read(lane / store / 'files.json')
            assert all(sha(lane / store / name) == expected for name, expected in files.items())
            assert sqlite_fingerprint(lane / store / 'study.sqlite') == read(lane / store / 'logical.json')['backend']
        events = [json.loads(line) for line in (lane / 'events.ndjson').read_text().splitlines()]
        rows = [json.loads(line) for line in (lane / 'telemetry.ndjson').read_text().splitlines()]
        result = assess_cloud(events, s, execution, calibration, read(lane / 'final-state.json'), rows, reset, corpus)
        assert result['status'] == 'passed', result['issues']
        saved = read(lane / 'assessment.json')
        assert {k: saved[k] for k in result} == result
        assert summary['lanes'][index - 1] == saved
        executions += check_ledger(lane / 'stores/study.sqlite', events, read(lane / 'sql-history.json'))
        results.append(result)
    assert executions == 264
    assert sqlite_fingerprint(folder / 'final-ledger.sqlite') == read(lane / 'stores/logical.json')['backend']
    assert all(sha(folder / n) == h for n, h in inventory['files'].items())
    cohorts = [c for r in results for c in r['cohorts'].values()]
    return {'status': 'passed', 'study_id': 'PAY-05', 'scope': 'Four short OCI offered-load/reset/telemetry qualification lanes; not a performance comparison.',
            'sealed_files_verified': len(names), 'source_checksums_sha256': sha(folder / 'checksums.json'),
            'auditor_sha256': sha(Path(__file__)), 'source_commit': source['commit'], 'qualification_lanes': 4,
            'workflow_executions_including_warmup': executions, 'correct_outcomes': sum(c['correct'] for c in cohorts),
            'on_time_correct': sum(c['on_time_correct'] for c in cohorts), 'deadline_misses_retained': sum(c['correct'] - c['on_time_correct'] for c in cohorts),
            'synthetic_effects': sum(r['final_state']['effects'] for r in results), 'database_and_ledger_resets_verified': 4,
            'telemetry_headroom_passed': all(r['telemetry']['headroom'] for r in results),
            'owned_engine_stopped': True, 'cloud_resources_accessed': True, 'performance_comparison_complete': False, 'model_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(args.source.resolve()): parser.error('Use a fresh audit folder outside sealed evidence.')
    result = audit(args.source); args.output.mkdir(parents=True)
    (args.output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__': main()
