#!/usr/bin/env python3
"""Independently audit all twelve retained PAY-05 delay-profile study lanes."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.audit_pay05_load import read, sha, digest, check_ledger
from benchlib.checkpoint import sqlite_fingerprint
from benchlib.pay05_performance import resolve_corpus, designs, assess_profile_lane, qualified_gate, summarize_pairs
from benchlib.runner import extract_event, normalize_event
from benchlib.w02_cloud import require_matched_controls


def seal(folder):
    target = folder / 'checksums.json'
    assert not target.exists()
    target.write_text(json.dumps({'algorithm': 'sha256', 'files': {p.relative_to(folder).as_posix(): sha(p)
        for p in sorted(folder.rglob('*')) if p.is_file() and p != target}}, indent=2) + '\n')


def verify_seal(folder):
    folder = folder.resolve(); target = folder / 'checksums.json'; inventory = read(target)
    names = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() and p != target}
    assert names == set(inventory['files']) and inventory['algorithm'] == 'sha256'
    for name, expected in inventory['files'].items():
        path = folder / name
        assert path.resolve().is_relative_to(folder) and not path.is_symlink() and sha(path) == expected
    return len(names)


def check_store(folder):
    files = read(folder / 'files.json')
    required = {'engine.dump', 'study.sqlite', 'logical.json'}
    # SQLite's backup/read APIs may retain WAL/shared-memory companions. They
    # are part of the sealed store, not evidence to omit or silently discard.
    assert required <= set(files) <= required | {'study.sqlite-wal', 'study.sqlite-shm'}
    assert {p.name for p in folder.iterdir() if p.is_file() and p.name != 'files.json'} == set(files)
    assert all(sha(folder / file) == expected for file, expected in files.items())
    assert sqlite_fingerprint(folder / 'study.sqlite') == read(folder / 'logical.json')['backend']
    assert all(sha(folder / file) == expected for file, expected in files.items())


def check_lane(folder, name, settings, plan, corpus, baseline, checkpoint, calibration):
    lane = folder / name
    s, execution = read(lane / 'k6-summary.json'), read(lane / 'execution.json')
    assert s['pay05']['arm'] == settings['arm'] and s['pay05']['rate'] == settings['rate'] == .5
    for field in ('drain_s', 'fixed_vus', 'deadline_s', 'poll_interval_ms', 'case_mode'):
        assert s['pay05'][field] == plan[field]
    assert s['pay05']['warmup_s'] * .5 == plan['warmup_arrivals']
    assert s['pay05']['duration_s'] * .5 == plan['measurement_arrivals']
    assert s['pay05']['corpus_sha256'] == baseline['corpus_sha256']
    require_matched_controls(baseline, read(lane / 'controls.json'))
    restored, reset = read(lane / 'restored-logical.json'), read(lane / 'reset.json')
    assert restored == checkpoint['logical']
    assert reset['expected_fingerprint'] == reset['restored_fingerprint'] == digest(restored)
    assert reset['verified_before_resuming_writers'] is True
    assert datetime.fromisoformat(reset['at']) < datetime.fromisoformat(execution['started_at'])
    for store in ('previous-state', 'stores'):
        check_store(lane / store)
    events = [json.loads(line) for line in (lane / 'events.ndjson').read_text().splitlines()]
    extracted = [normalize_event(event) for line in (lane / 'k6.log').read_text(errors='replace').splitlines() if (event := extract_event(line))]
    assert events == extracted
    rows = [json.loads(line) for line in (lane / 'telemetry.ndjson').read_text().splitlines()]
    result = assess_profile_lane(events, s, execution, calibration, read(lane / 'final-state.json'), rows, reset, corpus, read(lane / 'worker-records.json'))
    result.update(run_id=s['pay05']['run_id'], folder=name)
    assert result == read(lane / 'assessment.json')
    assert check_ledger(lane / 'stores/study.sqlite', events, read(lane / 'sql-history.json')) == sum(c['completed'] for c in result['cohorts'].values())
    return result


def audit(folder):
    folder = folder.resolve(); files = verify_seal(folder)
    source = read(folder / 'source-manifest.json')
    for name, expected in source['files'].items(): assert sha(folder / 'inputs' / name) == expected
    assert source['runtime_sha256'] == sha(folder / 'inputs/scripts/run_pay05_performance_cloud.py')
    corpus, protocol = read(folder / 'performance-corpus.json'), read(folder / 'inputs/workloads/pay05/performance-protocol.json')
    assert corpus == resolve_corpus(protocol, read(folder / 'inputs/workloads/pay05/cases.json'))
    assert source['performance_corpus_sha256'] == sha(folder / 'performance-corpus.json')
    summary = read(folder / 'summary.json'); qplan, mplan = designs(protocol, corpus, summary['run_id'])
    for name, expected in [('plan.json', qplan), ('candidate-measurement-plan.json', mplan), ('measurement-plan.json', mplan)]:
        saved = read(folder / name)
        assert {k: saved[k] for k in expected} == expected and saved['source_commit'] == source['commit']
    candidate, frozen = read(folder / 'candidate-measurement-plan.json'), read(folder / 'measurement-plan.json')
    assert {k: frozen[k] for k in candidate} == candidate
    assert read(folder / 'measurement-freeze.json')['sha256'] == sha(folder / 'measurement-plan.json')
    assert frozen['qualification_sha256'] == sha(folder / 'qualification-gate.json')
    assert frozen['controls_sha256'] == sha(folder / 'controls.json')
    closeout = read(folder / 'engine-closeout.json')
    assert summary['status'] == 'passed' and summary['cloud_resources_accessed'] is True and summary['owned_engine_stopped']
    assert summary['run_id'] == closeout['run_id'] and not summary['cleanup_errors'] and not closeout['cleanup_errors']
    assert closeout['owned_engine_stopped']
    for role in ('engine', 'backend', 'worker'):
        assert closeout['containers'][role]['stopped'] and not closeout['containers'][role]['oom_killed']
    runtime = read(folder / 'runtime.json')
    assert runtime['jdbc_tls_verify_full'] and runtime['internal_network'] and runtime['engine']['version'] == '3.0.1'
    assert len(runtime['database_tls']) > 1 and all(r[0] for r in runtime['database_tls'])
    probes = read(folder / 'isolation.json')['forbidden_endpoints_blocked']
    assert len(probes) == 5 and all(p['blocked'] for p in probes)
    calibration = read(folder / 'calibration/generator-calibration.json'); raw = read(folder / 'calibration/generator-calibration-summary.json')['metrics']
    assert calibration['valid'] and calibration['requests'] == raw['http_reqs']['values']['count']
    assert calibration['requests'] >= calibration['requested_http_rate'] * calibration['duration_s']
    assert raw.get('dropped_iterations', {}).get('values', {}).get('count', 0) == calibration['dropped'] == 0
    assert raw['http_req_failed']['values']['rate'] == calibration['error_fraction'] == 0
    baseline, checkpoint = read(folder / 'controls.json'), read(folder / 'checkpoint.json')
    assert baseline['worker_slots'] == 3 and baseline['worker_idle_s'] == .1 and baseline['poll_ms'] == 100
    assert baseline['db_nodes'] == 1 and baseline['db_ocpu'] == 2 and baseline['db_memory_gib'] == 32
    assert baseline['corpus_sha256'] == sha(folder / 'performance-corpus.json')
    assert baseline['source_manifest_sha256'] == sha(folder / 'source-manifest.json')
    assert len(summary['qualification']) == 2 and len(summary['measurements']) == 10
    results, qualifications, worker_ids = [], [], set()
    previous_end = datetime.fromisoformat(candidate['frozen_at'])
    previous_ledger = checkpoint['logical']['backend']
    schedule = [(f'q{i}-{lane["arm"]}', lane, qplan, False) for i, lane in enumerate(qplan['lanes'], 1)]
    schedule += [(f'p{i}-{j}-{arm}', {'arm': arm, 'rate': .5}, mplan, True) for i, pair in enumerate(mplan['pairs'], 1) for j, arm in enumerate(pair, 1)]
    for name, settings, plan, measured in schedule:
        lane = folder / name; verify_seal(lane)
        execution = read(lane / 'execution.json'); start = datetime.fromisoformat(execution['started_at'])
        assert previous_end < start < datetime.fromisoformat(execution['finished_at']) < datetime.fromisoformat(candidate['stage_stop_before'])
        previous_end = datetime.fromisoformat(execution['finished_at'])
        assert read(lane / 'previous-state/logical.json')['backend'] == previous_ledger
        previous_ledger = read(lane / 'stores/logical.json')['backend']
        if measured: assert datetime.fromisoformat(frozen['qualified_at']) < start
        else:
            assert read(folder / 'qualification-gate.json')['lanes'][name] == sha(lane / 'checksums.json')
            assert previous_end < datetime.fromisoformat(frozen['qualified_at'])
        worker_id = read(lane / 'worker-records.json')['execution_id']; assert worker_id not in worker_ids; worker_ids.add(worker_id)
        result = check_lane(folder, name, settings, plan, corpus, baseline, checkpoint, calibration)
        assert result['status'] == 'passed', result['issues']
        (results if measured else qualifications).append(result)
    assert qualifications == summary['qualification'] and qualified_gate(qualifications, qplan)
    assert results == summary['measurements']
    comparison = summarize_pairs(mplan, results)
    assert comparison == summary['comparison'] and comparison['performance_comparison_complete'] and summary['performance_comparison_complete']
    assert sqlite_fingerprint(folder / 'final-ledger.sqlite') == previous_ledger
    assert verify_seal(folder) == files
    return {'status': 'passed', 'study_id': 'PAY-05', 'source_commit': source['commit'], 'sealed_files_verified': files,
            'source_checksums_sha256': sha(folder / 'checksums.json'), 'auditor_sha256': sha(Path(__file__)),
            'qualification_lanes': 2, 'paired_measurement_arms': 10, 'measured_workflows': 3960,
            'workflow_executions_including_qualification_and_warmup': 4884, 'model_calls': 0,
            'performance_comparison_complete': True, 'comparison': comparison,
            'scope': 'Synthetic declared delay profiles on the retained OCI topology, not customer traffic, maximum capacity or live model performance.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(args.source.resolve()): parser.error('Use a fresh audit folder outside sealed evidence.')
    result = audit(args.source); args.output.mkdir(parents=True)
    (args.output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'comparison'}))


if __name__ == '__main__': main()
