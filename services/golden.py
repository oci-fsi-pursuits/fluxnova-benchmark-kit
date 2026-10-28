"""Functional evidence against a running Fluxnova engine and support service.

These checks run real BPMN/REST, but synthetic tools. They make no cloud capacity,
database compatibility, live-model quality, or infrastructure-failover claims.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import time
import urllib.parse
import uuid

from benchlib.fluxnova import FluxnovaClient
from services.worker import Client


def typed(value):
    return {'value': value, 'type': 'Boolean' if isinstance(value, bool) else 'Integer' if isinstance(value, int) else 'String'}


def run_golden(engine_url, support_url, output_dir, timeout_s=30, *, username=None, password=None, ca_file=None):
    if (Path(output_dir) / 'correctness.json').exists():
        raise ValueError('Refusing to overwrite existing correctness evidence; choose a new output directory')
    username = username if username is not None else os.environ.get('BENCH_USERNAME')
    password = password if password is not None else os.environ.get('BENCH_PASSWORD')
    engine = FluxnovaClient(engine_url, username, password, ca_file)
    raw_engine = Client(engine_url, engine.auth, ca_file=ca_file)
    support = Client(support_url, 'Bearer ' + os.environ['BENCH_SUPPORT_TOKEN'] if os.environ.get('BENCH_SUPPORT_TOKEN') else None, ca_file=os.environ.get('BENCH_SUPPORT_CA_FILE'))
    run_id = 'golden-' + uuid.uuid4().hex
    results = []

    def case(name, scenario, expected=None, overrides=None, payload_size=1024, duplicate=False, manual=False):
        logical = run_id + ':' + name
        values = {'anotherVariable': True, 'payload': 'x' * payload_size, 'scenario': scenario, 'logical_request_id': logical, 'run_id': run_id}
        values.update(overrides or {})
        variables = {key: typed(value) for key, value in values.items()}
        variables['payload'] = {'value': base64.b64encode(values['payload'].encode()).decode(), 'type': 'Bytes'}
        variables['aVariable'] = {'value': 'aStringValue', 'type': 'String', 'valueInfo': {'transient': True}}
        evidence = {'name': name, 'scenario': scenario, 'logical_request_id': logical, 'expected': expected, 'status': 'failed', 'process_instance_ids': []}
        try:
            instances = [engine.start('bench_' + scenario, logical, variables)]
            if duplicate:
                instances.append(engine.start('bench_' + scenario, logical, variables))
            evidence['process_instance_ids'] = [item['id'] for item in instances]
            for instance in instances:
                instance_id = instance['id']
                if manual:
                    end = time.monotonic() + timeout_s
                    tasks = []
                    while time.monotonic() < end:
                        tasks = engine.request('GET', '/task?processInstanceId=' + instance_id)
                        if tasks:
                            break
                        time.sleep(0.1)
                    if len(tasks) != 1 or tasks[0].get('taskDefinitionKey') != 'manual_review':
                        raise AssertionError('expected_manual_review_wait_state')
                    engine.request('POST', '/task/' + tasks[0]['id'] + '/complete', {'variables': {'reviewerDecision': typed('reviewed')}})
                    evidence['manual_review_simulated'] = True
                observation = engine.observe(instance_id, timeout_s)
                if not observation:
                    raise AssertionError('terminal_state_not_observed')
                state = observation['instance']
                stored = observation['variables']
                if state.get('state') != 'COMPLETED' or state.get('businessKey') != logical:
                    raise AssertionError('incorrect_process_terminal_state')
                if stored.get('anotherVariable') is not values['anotherVariable']:
                    raise AssertionError('persistent_variables_not_preserved')
                payload_vars = engine.request('GET', '/history/variable-instance?processInstanceId=' + instance_id + '&variableName=payload')
                if len(payload_vars) != 1 or payload_vars[0]['type'] != 'Bytes':
                    raise AssertionError('persistent_payload_type_mismatch')
                payload_bytes = raw_engine.request_bytes('/history/variable-instance/' + payload_vars[0]['id'] + '/data')
                if payload_bytes != values['payload'].encode():
                    raise AssertionError('persistent_payload_bytes_not_preserved')
                if 'aVariable' in stored:
                    raise AssertionError('transient_value_was_persisted')
                if scenario == 'variables':
                    target = 'true_end' if values['anotherVariable'] else 'false_end'
                    activities = engine.request('GET', '/history/activity-instance?processInstanceId=' + instance_id + '&activityId=' + target)
                    if not any(x.get('endTime') for x in activities):
                        raise AssertionError('incorrect_boolean_branch')
                if expected is not None:
                    if stored.get('workerResult') != expected or stored.get('workerTransientAbsent') is not True:
                        raise AssertionError('worker_result_or_transient_boundary_failed')
                    receipt = support.request('GET', '/state/' + urllib.parse.quote(logical, safe=''))
                    evidence['ledger'] = receipt
                    if not receipt.get('result') or receipt['result']['outcome'] != expected or receipt.get('violations'):
                        raise AssertionError('authoritative_state_mismatch')
                    expected_effects = 1 if scenario == 'settlement' and expected == 'settled' else 0
                    if receipt['effect_count'] != expected_effects:
                        raise AssertionError('incorrect_effect_count')
            evidence['status'] = 'passed'
        except Exception as error:
            evidence['error_type'] = type(error).__name__
            evidence['reason'] = str(error) if isinstance(error, AssertionError) else 'operation_failed_see_service_logs'
        results.append(evidence)

    case('minimal', 'minimal')
    supplied = json.loads((Path(__file__).resolve().parents[1] / 'workloads/smoke-start.json').read_text())
    smoke = {'name': 'supplied-json-smoke', 'scenario': 'variables', 'status': 'failed', 'business_key': supplied['businessKey']}
    try:
        instance = engine.start('bench_variables', supplied['businessKey'], supplied['variables'])
        observation = engine.observe(instance['id'], timeout_s)
        if not observation or observation['instance'].get('state') != 'COMPLETED':
            raise AssertionError('supplied_request_did_not_complete')
        if observation['variables'].get('anotherVariable') is not True or 'aVariable' in observation['variables']:
            raise AssertionError('supplied_request_persistence_assertion_failed')
        smoke.update(status='passed', process_instance_ids=[instance['id']])
    except Exception as error:
        smoke.update(error_type=type(error).__name__, reason='supplied_request_failed')
    results.append(smoke)
    for flag in (False, True):
        for size in (1024, 32768, 262144):
            case('variables-' + str(flag).lower() + '-' + str(size), 'variables', overrides={'anotherVariable': flag}, payload_size=size)
    for fault in ('none', 'before_effect_once', 'after_effect_once'):
        case('async-' + fault, 'async', 'ok', {'fault': fault})
    case('kyc-approved', 'kyc', 'approved')
    case('kyc-sanctions', 'kyc', 'rejected', {'sanctions_match': True})
    case('kyc-review', 'kyc', 'review', {'high_risk': True}, manual=True)
    case('risk-accepted', 'risk', 'accepted')
    case('risk-limit', 'risk', 'rejected', {'notional_minor': 2000})
    case('risk-stale', 'risk', 'rejected', {'stale_market': True})
    case('PAY-01', 'settlement', 'settled')
    case('PAY-02-block-unapproved', 'settlement', 'rejected', {'amount_minor': 2000, 'approved': False})
    case('PAY-02-approved', 'settlement', 'settled', {'amount_minor': 2000, 'approved': True})
    case('PAY-03', 'settlement', 'rejected', {'sanctions_match': True})
    case('unauthorized', 'settlement', 'rejected', {'authorized': False})
    case('PAY-04', 'settlement', 'settled', duplicate=True)
    case('PAY-06', 'settlement', 'settled', {'fault': 'before_effect_once'})
    case('PAY-07', 'settlement', 'settled', {'fault': 'after_effect_once'})
    report = {'schema_version': 1, 'run_id': run_id, 'kind': 'functional_correctness', 'engine_version': engine.readiness(), 'passed': all(r['status'] == 'passed' for r in results), 'cases': results, 'deferred': [
        {'case': 'PAY-05', 'requires': 'parallel FX quote worker and freshness policy'},
        {'case': 'PAY-08/PAY-09/PAY-10/PAY-11/PAY-14', 'requires': 'live agent orchestration, adversarial corpus, repeated trials; mock modes alone are not agent evidence'},
        {'case': 'PAY-12', 'requires': 'controlled engine/worker crash at effect boundaries and durable restart'},
        {'case': 'PAY-13', 'requires': 'real database failover adapter and observed failure evidence'}
    ], 'limitations': ['synthetic policy and ledger only', 'PAY-02 tests blocking and an already approved input; it does not implement a payment human-approval lifecycle', 'KYC manual-review completion is explicitly simulated', 'not a load/performance run', 'no cloud or production database certification']}
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'correctness.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-url', default=os.environ.get('BENCH_BASE_URL', 'http://127.0.0.1:18080/engine-rest'))
    parser.add_argument('--support-url', default=os.environ.get('BENCH_SUPPORT_URL', 'http://127.0.0.1:18081'))
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--timeout-s', type=float, default=30)
    parser.add_argument('--ca-file', default=os.environ.get('BENCH_ENGINE_CA_FILE'))
    args = parser.parse_args()
    report = run_golden(args.engine_url, args.support_url, args.output_dir, args.timeout_s, ca_file=args.ca_file)
    print(json.dumps({'passed': report['passed'], 'cases': len(report['cases']), 'report': str(Path(args.output_dir) / 'correctness.json')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
