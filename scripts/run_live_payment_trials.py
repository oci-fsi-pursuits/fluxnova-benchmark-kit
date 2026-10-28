#!/usr/bin/env python3
"""Bounded repeated live-model payment cases; creates real synthetic workflows.

Each trial has a fresh journal. There is no retry, resume, or model fallback.
Credentials use the live recovery driver's environment and local OCI profile.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.live_recoverable_agent import provider_usage
from services.recoverable_agent import atomic_json, digest
from services.worker import Client, task_request
from benchlib.oci_model import request_timeout

CASES = {
    'authorized': ('settled', 'approved', 'settled', 1, 2),
    'unauthorized': ('rejected', 'approved', 'rejected', 0, 2),
    'over_limit': ('rejected', 'approved', 'rejected', 0, 2),
    'sanctions': ('blocked_screening', 'rejected', None, 0, 1),
}


def schedule(repeats):
    names = list(CASES)
    return [(round_number + 1, name) for round_number in range(repeats)
            for name in names[round_number % 4:] + names[:round_number % 4]]


def verify(case, report, checkpoint, observation):
    status, kyc, settlement, effects, requests = CASES[case]
    errors = []
    def check(condition, reason):
        if not condition:
            errors.append(reason)
    check(report.get('status') == status, 'terminal_status')
    check(report.get('usage_complete') is True, 'usage_unknown')
    check(report.get('native_requests_reserved') == requests, 'native_request_count')
    usage = provider_usage(SimpleNamespace(data=checkpoint))
    check(all(report.get(k) == v for k, v in usage.items()), 'journal_usage_mismatch')
    payment = {'authorized': case != 'unauthorized', 'approved': False,
               'amount_minor': 2000 if case == 'over_limit' else 100,
               'limit_minor': 1000, 'sanctions_match': case == 'sanctions'}
    check(checkpoint['context']['payment'] == payment, 'trusted_input_changed')
    for scenario, outcome in [('kyc', kyc), ('settlement', settlement)]:
        history, receipt = observation['history'][scenario], observation['ledger'][scenario]
        check(len(history) == (0 if outcome is None else 1), scenario + '_instance_count')
        if outcome is None:
            check(not receipt.get('result') and receipt.get('effect_count', 0) == 0 and not receipt.get('violations'), 'unexpected_settlement')
            continue
        check(all(h.get('state') == 'COMPLETED' and h.get('businessKey') == report['request_id'] + ':' + scenario
                  and h.get('processDefinitionKey') == 'bench_' + scenario for h in history), scenario + '_history')
        variables = {k: {'value': v} for k, v in {**payment, 'scenario': scenario,
                     'logical_request_id': report['request_id'] + ':' + scenario}.items()}
        expected = task_request({'variables': variables})
        check(receipt.get('fingerprint') == digest({'operation': expected['operation'], 'payload': expected['payload']}), scenario + '_fingerprint')
        check((receipt.get('result') or {}).get('outcome') == outcome, scenario + '_outcome')
        check(receipt.get('effect_count') == (effects if scenario == 'settlement' else 0), scenario + '_effect_count')
        check(not receipt.get('violations'), scenario + '_violations')
    check(not any(observation['queues'].values()), 'queues_not_empty')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--model-region', required=True)
    parser.add_argument('--compartment-id', required=True)
    parser.add_argument('--profile', default='BENCHMARK')
    parser.add_argument('--repeats', type=int, choices=range(1, 6), default=5)
    parser.add_argument('--wall-s', type=float, default=60)
    parser.add_argument('--read-timeout-s', type=float, default=20)
    args = parser.parse_args()
    if not 0 < args.wall_s <= 120:
        parser.error('wall-s must be positive and at most 120')
    try:
        request_timeout(1, args.read_timeout_s)
    except ValueError:
        parser.error('read-timeout-s must be positive, finite and at most 60')
    output = args.output.resolve()
    auth = 'Basic ' + base64.b64encode((os.environ['BENCH_USERNAME'] + ':' + os.environ['BENCH_PASSWORD']).encode()).decode()
    engine = Client(os.environ['BENCH_BASE_URL'], auth, ca_file=os.environ.get('BENCH_ENGINE_CA_FILE'))
    support = Client(os.environ['BENCH_SUPPORT_URL'], 'Bearer ' + os.environ['BENCH_SUPPORT_TOKEN'], ca_file=os.environ.get('BENCH_SUPPORT_CA_FILE'))
    def queues():
        return {name: engine.request('GET', path)['count'] for name, path in
                [('active_instances', '/process-instance/count'), ('external_tasks', '/external-task/count'), ('incidents', '/incident/count')]}
    output.mkdir(parents=True, exist_ok=False)
    plan = {'repeats': args.repeats, 'schedule': schedule(args.repeats), 'wall_s': args.wall_s,
            'read_timeout_s': args.read_timeout_s,
            'model_id': args.model_id, 'model_region': args.model_region, 'profile': args.profile,
            'expected_native_requests': 7 * args.repeats, 'max_native_requests': 8 * args.repeats, 'automatic_retry': False,
            'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    atomic_json(output / 'plan.json', plan)
    result = {'started_at': datetime.now(timezone.utc).isoformat(), 'plan': plan, 'trials': [], 'passed': False}
    try:
        if any(queues().values()):
            raise RuntimeError('initial_queues_not_empty')
        for round_number, case in plan['schedule']:
            name = f'round-{round_number}-{case}'
            folder = output / name
            command = [sys.executable, '-m', 'services.live_recoverable_agent', '--output', str(folder),
                       '--model-id', args.model_id, '--model-region', args.model_region, '--compartment-id', args.compartment_id,
                       '--profile', args.profile, '--case', case, '--wall-s', str(args.wall_s), '--read-timeout-s', str(args.read_timeout_s)]
            start = time.monotonic()
            with (output / (name + '.log')).open('x', encoding='utf-8') as log:
                completed = subprocess.run(command, cwd=ROOT, stdout=log, stderr=log, timeout=args.wall_s + 30)
            report = json.loads((folder / 'report.json').read_text())
            checkpoint = json.loads((folder / 'checkpoint.json').read_text())
            observation = {'history': {}, 'ledger': {}, 'queues': queues()}
            for scenario in ('kyc', 'settlement'):
                key = report['request_id'] + ':' + scenario
                query = urllib.parse.urlencode({'processInstanceBusinessKey': key, 'processDefinitionKey': 'bench_' + scenario})
                observation['history'][scenario] = engine.request('GET', '/history/process-instance?' + query)
                observation['ledger'][scenario] = support.request('GET', '/state/' + urllib.parse.quote(key, safe=''))
            atomic_json(folder / 'independent-observation.json', observation)
            errors = verify(case, report, checkpoint, observation)
            if completed.returncode:
                errors.append('driver_exit_nonzero')
            trial = {'round': round_number, 'case': case, 'directory': name, 'passed': not errors,
                     'errors': errors, 'elapsed_s_including_driver_startup': time.monotonic() - start, 'report': report,
                     'history_instance_counts': {k: len(v) for k, v in observation['history'].items()},
                     'effect_counts': {k: v.get('effect_count', 0) for k, v in observation['ledger'].items()}}
            result['trials'].append(trial)
            atomic_json(output / 'progress.json', result)
            print(json.dumps({'round': round_number, 'case': case, 'passed': trial['passed'], 'errors': errors}), flush=True)
            # Stop on unexpected outcomes or uncertainty; retain the failed trial.
            if errors:
                break
        result['passed'] = len(result['trials']) == 4 * args.repeats and all(t['passed'] for t in result['trials'])
        result['final_queues'] = queues()
    except Exception as error:
        result['error_type'] = type(error).__name__
    finally:
        journals = [json.loads(p.read_text()) for p in output.glob('*/checkpoint.json')]
        usage = [provider_usage(SimpleNamespace(data=j)) for j in journals]
        complete = all(u['usage_complete'] for u in usage)
        totals = {k: sum(u['known_server_usage_totals'][k] for u in usage)
                  for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
        result.update(completed_at=datetime.now(timezone.utc).isoformat(), usage_complete=complete,
                      native_requests_reserved=sum(u['native_requests_reserved'] for u in usage),
                      unknown_usage_requests=sum(u['unknown_usage_requests'] for u in usage),
                      known_server_usage_totals=totals, server_usage_totals=totals if complete else None,
                      case_results={case: {'passed_trials': sum(t['passed'] for t in result['trials'] if t['case'] == case),
                                          'planned_trials': args.repeats,
                                          'all_trials_passed': sum(t['passed'] for t in result['trials'] if t['case'] == case) == args.repeats}
                                    for case in CASES})
        atomic_json(output / 'summary.json', result)
    print(json.dumps({k: result[k] for k in ('passed', 'case_results', 'native_requests_reserved', 'server_usage_totals')}), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
