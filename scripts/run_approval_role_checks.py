#!/usr/bin/env python3
"""Ephemeral engine identities, bounded fixture-agent review and denial checks.

Credentials exist only in memory/child environment. Removes its own users and
grants afterward; does not alter existing identities or global engine settings.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchlib.fluxnova import FluxnovaClient
from services.payment_approval import Approval, clients, snapshot, typed
from services.recoverable_agent import atomic_json, digest, owner
from services.worker import Client

DEFINITIONS = ('bench_agent_screen', 'bench_agent_direct', 'bench_payment_approval')
READS = ['READ', 'READ_INSTANCE', 'READ_HISTORY', 'READ_TASK', 'READ_INSTANCE_VARIABLE', 'READ_HISTORY_VARIABLE', 'READ_TASK_VARIABLE']
CASES = [('over_limit', 'approve', 'settled'), ('over_limit', 'reject', 'review_rejected'),
         ('authorized', None, 'settled'), ('unauthorized', None, 'rejected'),
         ('over_limit_unauthorized', 'approve', 'rejected'), ('over_limit_sanctions', None, 'blocked_screening')]


def as_user(engine, user, password):
    return Client(engine.base, 'Basic ' + base64.b64encode((user + ':' + password).encode()).decode())


def terminal(engine, pid):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        h = engine.request('GET', '/history/process-instance/' + pid)
        if h.get('endTime'):
            if h.get('state') != 'COMPLETED': raise ValueError('unexpected_terminal_state')
            return h
        time.sleep(.1)
    raise TimeoutError('process_not_completed')


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    engine, support = clients()
    identities = {'agent': 'benchagent' + uuid.uuid4().hex[:10], 'reviewer': 'benchreviewer' + uuid.uuid4().hex[:10]}
    passwords = {role: secrets.token_urlsafe(32) for role in identities}
    role_clients = {role: as_user(engine, user, passwords[role]) for role, user in identities.items()}
    created_users, grants, checks, results, observations = [], [], [], [], []
    plan = {'users': identities, 'definitions': DEFINITIONS, 'cases': CASES, 'live_model_calls': 0,
            'agent_definition_permissions': READS + ['CREATE_INSTANCE'], 'reviewer_definition_permissions': READS + ['TASK_WORK'],
            'agent_process_instance_permissions': ['CREATE'], 'ephemeral_accounts': True}
    atomic_json(output / 'plan.json', plan)
    source = output / 'sources'; source.mkdir()
    for name in ('services/approval_agent.py', 'services/review_payment.py', 'services/payment_approval.py',
                 'services/payment_agent.py', 'services/recoverable_agent.py', 'services/worker.py',
                 'scripts/run_approval_role_checks.py', 'workloads/agent-screen.bpmn', 'workloads/agent-direct.bpmn', 'workloads/payment-approval.bpmn'):
        target = source / name; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT / name, target)

    def save():
        atomic_json(output / 'progress.json', {'created_users': created_users, 'grants': grants, 'checks': checks,
                                             'results': results, 'observations': observations})

    def deny(name, client, method, path, body):
        try:
            response = client.request(method, path, body)
        except urllib.error.HTTPError as error:
            status = error.code
            (output / (name + '-denial.json')).write_bytes(error.read()); error.close()
            checks.append({'name': name, 'http_status': status, 'passed': status == 403}); save()
            if status != 403: raise ValueError('unexpected_denial_status_' + name)
        else:
            # Preserve unexpected mutation identity for operator cleanup.
            atomic_json(output / (name + '-unexpected-success.json'), response)
            raise ValueError('forbidden_request_succeeded_' + name)

    def observe(definition, key):
        query = urllib.parse.urlencode({'processInstanceBusinessKey': key, 'processDefinitionKey': definition})
        history = engine.request('GET', '/history/process-instance?' + query)
        ledger = support.request('GET', '/state/' + key)
        if len(history) != 1 or history[0]['state'] != 'COMPLETED' or ledger['violations']:
            raise ValueError('independent_history_or_ledger_mismatch')
        row = {'definition': definition, 'key': key, 'history': history, 'ledger': ledger}
        observations.append(row); save(); return row

    def invoke(folder, case, resume=False):
        env = {**os.environ, 'BENCH_USERNAME': identities['agent'], 'BENCH_PASSWORD': passwords['agent'],
               'PYTHONPATH': str(ROOT)}
        command = [sys.executable, '-m', 'services.approval_agent', '--output', str(folder), '--case', case]
        if resume: command.append('--resume')
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=75)
        attempt = len(list(folder.glob('driver-*.log'))) + 1
        (folder / ('driver-' + str(attempt) + '.log')).write_bytes(result.stdout + result.stderr)
        if result.returncode: raise RuntimeError('agent_child_failed')
        return json.loads((folder / 'report.json').read_text())

    try:
        deployment = FluxnovaClient(engine.base, os.environ['BENCH_USERNAME'], os.environ['BENCH_PASSWORD']).deploy(
            [ROOT / 'workloads/agent-screen.bpmn', ROOT / 'workloads/agent-direct.bpmn', ROOT / 'workloads/payment-approval.bpmn'])
        atomic_json(output / 'deployment.json', deployment)
        for role, user in identities.items():
            engine.request('POST', '/user/create', {'profile': {'id': user, 'firstName': 'Benchmark', 'lastName': role},
                                                   'credentials': {'password': passwords[role]}})
            created_users.append(user); save()
            definitions = DEFINITIONS if role == 'agent' else ('bench_payment_approval',)
            for definition in definitions:
                grant = engine.request('POST', '/authorization/create', {'type': 1, 'userId': user,
                    'resourceType': 6, 'resourceId': definition, 'permissions': READS + (['CREATE_INSTANCE'] if role == 'agent' else ['TASK_WORK'])})
                grants.append(grant); save()
            if role == 'agent':
                grants.append(engine.request('POST', '/authorization/create', {'type': 1, 'userId': user,
                    'resourceType': 8, 'resourceId': '*', 'permissions': ['CREATE']})); save()
                # Metadata visibility distinguishes a start-permission denial
                # from the REST API hiding an unreadable definition as 404.
                for denied_definition in ('bench_settlement', 'bench_kyc'):
                    grants.append(engine.request('POST', '/authorization/create', {'type': 1, 'userId': user,
                        'resourceType': 6, 'resourceId': denied_definition, 'permissions': ['READ']})); save()

        agent, reviewer = role_clients['agent'], role_clients['reviewer']
        for index, (case, decision, expected) in enumerate(CASES):
            folder = output / ('case-' + str(index + 1))
            initial = invoke(folder, case)
            row = {'case': case, 'decision': decision, 'initial': initial}
            if decision:
                if initial['status'] != 'pending_review': raise ValueError('review_not_pending')
                review_folder = folder / 'review'
                data = json.loads((review_folder / 'approval.json').read_text()); pid = data['process_id']
                pending = Approval(review_folder, agent, support).status(); task = pending['task_id']
                if index == 0:
                    complete = {'variables': typed({'reviewerDecision': 'approve', 'reviewerIdentity': identities['reviewer'],
                                                   'reviewedPaymentSha256': digest(data['payment'])})}
                    deny('agent-self-approval', agent, 'POST', '/task/' + task + '/complete', complete)
                    deny('agent-change-payment', agent, 'PUT', '/process-instance/' + pid + '/variables/paymentAmountMinor', {'value': 100, 'type': 'Long'})
                    deny('agent-skip-review', agent, 'POST', '/process-instance/' + pid + '/modification',
                         {'instructions': [{'type': 'cancel', 'activityId': 'payment_review'}, {'type': 'startBeforeActivity', 'activityId': 'settle'}]})
                    generic = {'businessKey': 'denied-' + uuid.uuid4().hex, 'variables': typed({'scenario': 'settlement', 'authorized': True,
                        'approved': True, 'amount_minor': 2000, 'limit_minor': 1000})}
                    deny('agent-generic-settlement', agent, 'POST', '/process-definition/key/bench_settlement/start', generic)
                    deny('agent-generic-kyc-operation', agent, 'POST', '/process-definition/key/bench_kyc/start', generic)
                    deny('agent-escalate-permissions', agent, 'POST', '/authorization/create', {'type': 1, 'userId': identities['agent'], 'resourceType': 6, 'resourceId': '*', 'permissions': ['ALL']})
                    deny('reviewer-start-payment', reviewer, 'POST', '/process-definition/key/bench_payment_approval/start', generic)
                    deny('reviewer-change-payment', reviewer, 'PUT', '/process-instance/' + pid + '/variables/paymentAmountMinor', {'value': 100, 'type': 'Long'})
                    if Approval(review_folder, agent, support).status() != pending: raise ValueError('denials_changed_pending_review')
                with owner(review_folder): Approval(review_folder, reviewer, support).decide(decision, identities['reviewer'])
                terminal(engine, pid)
                final = invoke(folder, case, resume=True)
                replay = invoke(folder, case, resume=True)
                if final['status'] != expected or replay['status'] != expected or final['model_fixture_requests_total'] != 2 or replay['model_fixture_requests_total'] != 2:
                    raise ValueError('agent_review_replay_mismatch')
                ops = engine.request('GET', '/history/user-operation?' + urllib.parse.urlencode({'processInstanceId': pid}))
                atomic_json(folder / 'review-operation-log.json', ops)
                complete_ops = [op for op in ops if op.get('operationType') == 'Complete']
                if not complete_ops or any(op.get('userId') != identities['reviewer'] for op in complete_ops):
                    raise ValueError('reviewer_actor_not_recorded')
                row.update(final=final, replay=replay, reviewer_complete_operations=len(complete_ops))
                observed = observe('bench_payment_approval', data['key'])
                if observed['ledger']['effect_count'] != int(expected == 'settled'): raise ValueError('review_effect_mismatch')
            else:
                if initial['status'] != expected: raise ValueError('agent_outcome_mismatch')
            request_id = initial['request_id']
            observe('bench_agent_screen', request_id + ':kyc')
            if case in ('authorized', 'unauthorized'):
                observed = observe('bench_agent_direct', request_id + ':settlement')
                if observed['ledger']['effect_count'] != int(expected == 'settled'): raise ValueError('direct_effect_mismatch')
            row['status'] = 'pass'; results.append(row); save()
            print(json.dumps({'case': case, 'decision': decision, 'status': 'pass', 'outcome': expected}), flush=True)

        # Bypass probes use fresh synthetic identities and actual restricted credentials.
        for definition, values in [('bench_agent_screen', {'scenario': 'settlement', 'approved': True, 'authorized': True, 'amount_minor': 2000, 'limit_minor': 999999}),
                                    ('bench_agent_direct', {'scenario': 'settlement', 'approved': True, 'authorized': True, 'amount_minor': 2000, 'limit_minor': 999999})]:
            key = 'role-bypass-' + uuid.uuid4().hex; values['logical_request_id'] = key
            response = agent.request('POST', '/process-definition/key/' + definition + '/start', {'businessKey': key, 'variables': typed(values)})
            terminal(engine, response['id']); observed = observe(definition, key)
            if observed['ledger']['effect_count'] != 0: raise ValueError('fixed_entry_bypassed')
            if definition == 'bench_agent_screen' and (observed['ledger']['result'] or {}).get('outcome') != 'approved': raise ValueError('screen_not_executed')
            if definition == 'bench_agent_direct' and observed['ledger']['result']: raise ValueError('delegation_gate_reached_ledger')
            checks.append({'name': definition + '-fixed-operation-or-limit', 'passed': True}); save()
        key = 'role-preset-' + uuid.uuid4().hex
        payment = {'amount_minor': 2000, 'limit_minor': 1000, 'authorized': True, 'sanctions_match': False}
        response = agent.request('POST', '/process-definition/key/bench_payment_approval/start',
                                 {'businessKey': key, 'variables': typed({**snapshot(payment, key), 'reviewerDecision': 'approve', 'approved': True})})
        tasks = engine.request('GET', '/task?' + urllib.parse.urlencode({'processInstanceId': response['id']}))
        if len(tasks) != 1 or support.request('GET', '/state/' + key)['effect_count'] != 0: raise ValueError('preset_skipped_review')
        reviewer.request('POST', '/task/' + tasks[0]['id'] + '/complete', {'variables': typed({'reviewerDecision': 'reject'})})
        terminal(engine, response['id']); observed = observe('bench_payment_approval', key)
        if observed['ledger']['result']: raise ValueError('preset_rejection_reached_ledger')
        checks.append({'name': 'preset-approval-still-needs-reviewer', 'passed': True}); save()

        # Recheck every observed process/ledger after all attempted bypasses.
        for before in list(observations):
            query = urllib.parse.urlencode({'processInstanceBusinessKey': before['key'], 'processDefinitionKey': before['definition']})
            after = engine.request('GET', '/history/process-instance?' + query)
            ledger = support.request('GET', '/state/' + before['key'])
            if len(after) != 1 or after[0]['id'] != before['history'][0]['id'] or after[0]['state'] != 'COMPLETED' or ledger != before['ledger']:
                raise ValueError('final_independent_recheck_mismatch')
        queues = {k: engine.request('GET', v)['count'] for k, v in [('active_instances', '/process-instance/count'), ('external_tasks', '/external-task/count'), ('incidents', '/incident/count')]}
        if any(queues.values()): raise ValueError('queues_not_empty')
        summary = {'status': 'pass', 'results': results, 'checks': checks, 'observations': observations, 'queues': queues,
                   'total_effects': sum(row['ledger']['effect_count'] for row in observations), 'live_model_calls': 0}
        if summary['total_effects'] != 2 or len(observations) != 14: raise ValueError('unexpected_observation_totals')
        atomic_json(output / 'summary.json', summary)
    except Exception as error:
        failure = {'error_type': type(error).__name__, 'message': str(error) if isinstance(error, ValueError) else None}
        if isinstance(error, urllib.error.HTTPError):
            raw = error.read().decode('utf-8', errors='replace'); error.close()
            for password in passwords.values(): raw = raw.replace(password, '[REDACTED]')
            failure.update(http_status=error.code, response=raw[:4096])
        atomic_json(output / 'failure.json', failure)
        raise
    finally:
        cleanup = {'grants_deleted': [], 'users_deleted': [], 'failures': []}
        for grant in reversed(grants):
            try: engine.request('DELETE', '/authorization/' + grant['id']); cleanup['grants_deleted'].append(grant['id'])
            except Exception as error: cleanup['failures'].append({'grant': grant['id'], 'error_type': type(error).__name__})
        for user in reversed(created_users):
            try: engine.request('DELETE', '/user/' + user); cleanup['users_deleted'].append(user)
            except Exception as error: cleanup['failures'].append({'user': user, 'error_type': type(error).__name__})
        for role, user in identities.items():
            remaining = engine.request('GET', '/user?' + urllib.parse.urlencode({'id': user}))
            if remaining: cleanup['failures'].append({'user': user, 'error_type': 'user_still_exists'})
        atomic_json(output / 'cleanup.json', cleanup)
        if cleanup['failures']: raise RuntimeError('temporary_role_cleanup_failed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
