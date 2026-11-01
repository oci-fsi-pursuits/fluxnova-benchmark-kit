#!/usr/bin/env python3
"""Independently check saved PAY-05 outcomes against fixed fixture expectations."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def read(path): return json.loads(path.read_text(encoding='utf-8'))
def digest(value): return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
def sha(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def resolved_case(source, payment_id):
    case = json.loads(json.dumps(source))
    old_id = case['payment']['payment_id']; case['payment']['payment_id'] = payment_id
    for schedule in case['quotes'].values():
        quote = schedule['quote']
        if isinstance(quote, dict) and quote.get('payment_id') == old_id:
            quote['payment_id'] = payment_id
    return case


def check_outcome(case, arm, key, pid, history, activities, before):
    """Check declared outcomes and raw receipts without calling the selector."""
    assert history and history['instance']['id'] == pid and history['instance']['endTime']
    assert before['process_id'] == pid and before['input_sha256'] == digest(case)
    assert before['case_key'] == key
    assert set(before['receipts']) == {'quote_a', 'quote_b', 'quote_c', 'select'}
    assert type(before['registered_ns']) is int and before['registered_ns'] >= 0
    for op, receipt in before['receipts'].items():
        assert all(type(receipt[k]) is int for k in ('started_ns', 'finished_ns'))
        assert receipt['input_sha256'] == digest(case) and receipt['process_id'] == pid
        assert receipt['case_key'] == key and receipt['operation'] == op
        assert receipt['finished_ns'] >= receipt['started_ns'] >= before['registered_ns']
    for route in ('a', 'b', 'c'):
        receipt, fixture = before['receipts']['quote_' + route], case['quotes'][route]
        assert receipt['quote'] == fixture['quote'] and receipt['outcome'] == fixture['outcome']
        assert history['variables']['pay05Quote' + route.upper()] == fixture['outcome']
    selected = before['receipts']['select']; expected = case['expected']
    assert all(type(selected[k]) is int for k in ('decision_ns', 'decision_ms'))
    assert selected['started_ns'] <= selected['decision_ns'] <= selected['finished_ns']
    assert {k: selected[k] for k in expected} == expected
    assert history['variables']['pay05Outcome'] == expected['outcome']
    assert selected['decision_ms'] == (selected['decision_ns'] - before['registered_ns']) // 1000000
    quotes = [before['receipts']['quote_' + r] for r in ('a', 'b', 'c')]
    assert selected['started_ns'] >= max(q['finished_ns'] for q in quotes)
    ends = [a['activityId'] for a in activities if a['activityType'].lower().endswith('endevent') and a.get('endTime')]
    assert ends == [expected['outcome']]
    expected_effects = []
    if expected['outcome'] == 'settled':
        p = case['payment']; q = case['quotes'][expected['selected_route']]['quote']
        assert p['authorized'] is True and expected['selected_route'] in p['allowed_routes']
        assert q['issued_ms'] <= selected['decision_ms'] < q['expires_ms']
        assert q['source_amount_minor'] + q['fee_minor'] == expected['source_total_minor'] <= p['max_source_total_minor']
        assert all(type(q[k]) is type(p[k]) and q[k] == p[k]
                   for k in ('payment_id', 'beneficiary', 'target_amount_minor', 'source_currency', 'target_currency'))
        expected_effects = [{'key': key, **{k: p[k] for k in ('payment_id', 'beneficiary', 'target_amount_minor', 'source_currency', 'target_currency')},
                             'route_id': expected['selected_route'], 'source_total_minor': expected['source_total_minor']}]
    assert before['effects'] == expected_effects
    overlap = min(q['finished_ns'] for q in quotes) > max(q['started_ns'] for q in quotes)
    if arm == 'sequential':
        assert quotes[1]['started_ns'] >= quotes[0]['finished_ns'] and quotes[2]['started_ns'] >= quotes[1]['finished_ns']
    elif arm == 'parallel' and case['require_parallel_overlap']:
        assert overlap, 'three_quote_overlap_not_observed'
    assert arm in ('sequential', 'parallel')
    return {'case_id': case['case_id'], 'arm': arm, 'status': 'passed',
            'outcome': expected['outcome'], 'selected_route': expected['selected_route'],
            'effects': len(expected_effects), 'all_three_quotes_overlap': overlap}



def check_record(case, record):
    before, after = record['backend_before_replay'], record['backend_after_replay']
    assert before['receipts'] == after['receipts'] and before['effects'] == after['effects']
    assert record['definition']['key'] == 'bench_pay05_' + record['arm']
    assert record['history']['instance']['processDefinitionId'] == record['definition']['id']
    result = check_outcome(case, record['arm'], record['case_key'], record['process_id'],
                           record['history'], record['activities'], before)
    assert record['premature_effect_refused'] and record['replay_calls'] == 8
    for op in before['receipts']:
        first = sum(a['operation'] == op and a['event'] == 'replay' for a in before['audit'])
        last = sum(a['operation'] == op and a['event'] == 'replay' for a in after['audit'])
        assert last - first == 2
    return result


def audit(folder):
    folder = folder.resolve(); inventory = read(folder / 'checksums.json')
    names = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() and p.name != 'checksums.json'}
    assert names == set(inventory['files'])
    for name, expected in inventory['files'].items():
        p = folder / name
        assert p.resolve().is_relative_to(folder) and not p.is_symlink() and sha(p) == expected
    manifest, summary = read(folder / 'manifest.json'), read(folder / 'summary.json')
    assert summary['status'] == 'passed' and summary['owned_engine_stopped'] and not summary['cleanup_errors']
    assert manifest['worker_slots'] == 3 and manifest['study_id'] == 'PAY-05'
    cloud = manifest.get('cloud_resources_accessed', False)
    assert type(cloud) is bool and summary['cloud_resources_accessed'] == cloud
    if cloud:
        runtime, closeout = read(folder / 'runtime.json'), read(folder / 'engine-closeout.json')
        assert manifest['database'] == 'OCI managed PostgreSQL' and manifest['runtime'] == runtime
        assert runtime['jdbc_tls_verify_full'] and runtime['internal_network']
        assert len(runtime['database_tls']) > 1 and all(row[0] for row in runtime['database_tls'])
        assert runtime['engine']['version'] == '3.0.1'
        assert closeout['run_id'] == manifest['run_id'] and not closeout['cleanup_errors']
        assert closeout['owned_engine_stopped'] and set(closeout['containers']) == {'engine', 'job'}
        assert all(row['stopped'] and not row['oom_killed'] and row['label'] == manifest['run_id']
                   for row in closeout['containers'].values())
        assert closeout['containers']['job']['exit_code'] == 0
        isolation = read(folder / 'isolation.json')['forbidden_endpoints_blocked']
        assert len(isolation) == 5 and all(row['blocked'] for row in isolation)
        assert summary['collector_stopped'] and not summary['collector_cleanup_errors']
    for name, expected in manifest['source_sha256'].items(): assert sha(folder / 'inputs' / name) == expected
    corpus = read(folder / 'inputs/workloads/pay05/cases.json')
    order = [(c, arm) for c in corpus['cases'] for arm in manifest['arm_order_by_case'][c['case_id']]]
    assert len(order) == 44 and len(summary['cases']) == 44
    rows, keys = [], set()
    with sqlite3.connect('file:' + (folder / 'study.sqlite').as_posix() + '?mode=ro&immutable=1', uri=True) as db:
        db.row_factory = sqlite3.Row
        for (source, arm), result in zip(order, summary['cases'], strict=True):
            record = read(folder / (arm + '-' + source['case_id'] + '.json'))
            expected_payment_id = 'pay05-' + manifest['run_id'][:12] + '-' + arm + '-' + source['case_id']
            case = resolved_case(source, expected_payment_id)
            assert record['case'] == case and record['arm'] == arm
            checked = check_record(case, record); assert checked == result
            key = record['case_key']; keys.add(key)
            stored = db.execute('SELECT * FROM cases WHERE key=?', (key,)).fetchone()
            assert json.loads(stored['body']) == case and stored['fingerprint'] == digest(case)
            receipts = {r['operation']: json.loads(r['body']) for r in db.execute('SELECT * FROM receipts WHERE key=?', (key,))}
            assert receipts == record['backend_before_replay']['receipts']
            assert [dict(r) for r in db.execute('SELECT * FROM effects WHERE key=?', (key,))] == record['backend_before_replay']['effects']
            rows.append(checked)
        assert {r[0] for r in db.execute('SELECT key FROM cases')} == keys
        assert db.execute('SELECT count(*) FROM receipts').fetchone()[0] == 176
        assert db.execute('SELECT count(*) FROM effects').fetchone()[0] == sum(r['effects'] for r in rows)
    assert all(v == 0 for v in read(folder / 'final-engine-counts.json').values())
    assert len(summary['replay_probes']) == 2 and all(v['blocked'] and v['original_effects_unchanged'] for v in summary['replay_probes'])
    assert {p['arm'] for p in summary['replay_probes']} == {'sequential', 'parallel'}
    for probe in summary['replay_probes']:
        replay = read(folder / ('replay-' + probe['arm'] + '.json'))
        assert replay['probe'] == probe and replay['tasks'] and probe['incidents']
        assert all(t['processInstanceId'] == probe['process_id'] and t['retries'] == 0 for t in replay['tasks'])
        assert all(i['processInstanceId'] == probe['process_id'] for i in probe['incidents'])
        assert len(probe['incidents']) == len(replay['tasks'])
        expected = next(r for r in rows if r['arm'] == probe['arm'] and r['outcome'] == 'settled')
        original = read(folder / (probe['arm'] + '-' + expected['case_id'] + '.json'))
        assert replay['backend_after']['effects'] == original['backend_before_replay']['effects']
        assert replay['backend_after']['receipts'] == original['backend_before_replay']['receipts']
        assert probe['process_id'] != original['process_id']
    assert all(sha(folder / n) == h for n, h in inventory['files'].items())
    return {'status': 'passed', 'study_id': 'PAY-05', 'scope': ('OCI/managed PostgreSQL' if cloud else 'Local Fluxnova/H2') + ' functional qualification; no performance or live FX/model claim.',
            'source_checksums_sha256': sha(folder / 'checksums.json'), 'auditor_sha256': sha(Path(__file__)),
            'sealed_files_verified': len(names), 'unique_scenarios': 22, 'functional_executions': 44,
            'synthetic_effects': sum(r['effects'] for r in rows), 'replay_calls': 352,
            'cross_process_replays_blocked': 2, 'owned_engine_stopped': True,
            'corpus_revision': corpus.get('corpus_revision', 1),
            'performance_comparison_complete': False, 'cloud_resources_accessed': cloud, 'model_calls': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(args.source.resolve()): p.error('Use a fresh audit folder outside sealed evidence.')
    result = audit(args.source); args.output.mkdir(parents=True)
    (args.output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__': main()
