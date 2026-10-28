"""Bounded synthetic model -> Fluxnova KYC -> model -> settlement experiment.

Uses the existing deterministic /model fixture. No live model or automatic crash
resume is implemented. Payment authority comes from trusted caller inputs.
"""
import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import uuid
from datetime import datetime, timezone

from services.worker import Client, task_request


class AgentStopped(Exception):
    pass


def run_agent(model, tool, *, mode='normal', max_turns=4, max_throttles=2,
              wall_s=30, clock=time.monotonic, sleep=time.sleep, emit=lambda event: None):
    """Run a bounded provider-neutral loop; tool is a trusted workflow adapter.

    All clients/adapters must bound each operation by the supplied deadline.
    The core checks the deadline before and after every external operation.
    """
    if (type(max_turns) is not int or not 1 <= max_turns <= 16 or
            type(max_throttles) is not int or not 0 <= max_throttles <= 8 or
            isinstance(wall_s, bool) or not isinstance(wall_s, (int, float)) or
            not math.isfinite(wall_s) or not 0 < wall_s <= 120):
        raise ValueError('invalid_agent_bounds')
    deadline = clock() + wall_s
    history = []
    screening = None
    throttles = 0

    def remaining():
        if clock() >= deadline:
            raise AgentStopped('wall_budget')

    for turn in range(max_turns):
        while True:
            remaining()
            emit({'event': 'model_request', 'turn': turn})
            try:
                response = model({'turn': turn, 'mode': mode, 'tool_results': history}, deadline)
                remaining()
                break
            except urllib.error.HTTPError as error:
                if error.code != 429:
                    raise
                throttles += 1
                emit({'event': 'model_throttled', 'count': throttles})
                if throttles > max_throttles:
                    raise AgentStopped('throttle_budget') from None
                sleep(min(.1 * 2 ** (throttles - 1), max(0, deadline - clock())))
        if not isinstance(response, dict) or len(json.dumps(response).encode()) > 16384:
            raise AgentStopped('invalid_model_response')
        if type(response.get('final')) is not bool or not isinstance(response.get('tool_calls'), list):
            raise AgentStopped('invalid_model_response')
        calls = response['tool_calls']
        if response['final']:
            if calls or screening != 'approved':
                raise AgentStopped('final_without_verified_screening')
            remaining()
            result = tool('settlement', deadline)
            remaining()
            emit({'event': 'payment_review_pending' if result == 'pending_review' else 'settlement_verified', 'outcome': result})
            return {'status': result, 'model_turns': turn + 1, 'throttles': throttles}
        if len(calls) != 1:
            raise AgentStopped('invalid_tool_count')
        call = calls[0]
        if (not isinstance(call, dict) or call.get('name') != 'screen_beneficiary' or
                call.get('arguments') != {'beneficiary': 'synthetic-beneficiary'} or
                not isinstance(call.get('id'), str) or not 0 < len(call['id']) <= 128):
            raise AgentStopped('invalid_tool')
        # Repeated calls reuse the independently verified result, not a new start.
        if screening is None:
            remaining()
            screening = tool('kyc', deadline)
            remaining()
        history.append({'tool_call_id': call['id'], 'outcome': screening})
        emit({'event': 'screening_verified', 'turn': turn, 'outcome': screening})
        if screening != 'approved':
            return {'status': 'blocked_screening', 'model_turns': turn + 1, 'throttles': throttles}
    raise AgentStopped('turn_budget')


class WorkflowTools:
    def __init__(self, engine, support, request_id, payment, emit):
        self.engine, self.support = engine, support
        self.request_id, self.payment, self.emit = request_id, dict(payment), emit

    @staticmethod
    def request(client, method, path, body, deadline):
        left = deadline - time.monotonic()
        if left <= 0:
            raise AgentStopped('wall_budget')
        client.timeout = min(10, left)
        return client.request(method, path, body)

    def definition(self, scenario):
        return 'bench_' + scenario

    def __call__(self, scenario, deadline):
        if scenario not in ('kyc', 'settlement'):
            raise ValueError('unsupported_workflow')
        key = self.request_id + ':' + scenario
        values = {**self.payment, 'scenario': scenario, 'logical_request_id': key, 'anotherVariable': True}
        variables = {k: {'value': v, 'type': 'Boolean' if type(v) is bool else 'Integer' if type(v) is int else 'String'} for k, v in values.items()}
        # Persist the intent before a potentially ambiguous start. Never retry it.
        self.emit({'event': 'workflow_start_intent', 'scenario': scenario, 'logical_request_id': key})
        instance = self.request(self.engine, 'POST', '/process-definition/key/' + self.definition(scenario) + '/start', {'businessKey': key, 'variables': variables}, deadline)
        identity = urllib.parse.quote(instance['id'], safe='')
        self.emit({'event': 'workflow_started', 'scenario': scenario, 'process_instance_id': instance['id']})
        while True:
            state = self.request(self.engine, 'GET', '/history/process-instance/' + identity, None, deadline)
            if state and state.get('endTime'):
                break
            time.sleep(min(.1, max(0, deadline - time.monotonic())))
        if state.get('state') != 'COMPLETED' or state.get('businessKey') != key:
            raise ValueError('incorrect_workflow_terminal_state')
        stored = self.request(self.engine, 'GET', '/history/variable-instance?processInstanceId=' + identity, None, deadline)
        outcome = {v['name']: v.get('value') for v in stored}.get('workerResult')
        receipt = self.request(self.support, 'GET', '/state/' + urllib.parse.quote(key, safe=''), None, deadline)
        expected = task_request({'id': 'oracle', 'variables': variables})
        fingerprint = hashlib.sha256(json.dumps({'operation': expected['operation'], 'payload': expected['payload']}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        allowed = ('approved', 'rejected') if scenario == 'kyc' else ('settled', 'rejected')
        if (outcome not in allowed or receipt.get('fingerprint') != fingerprint or
                (receipt.get('result') or {}).get('outcome') != outcome or receipt.get('violations') or
                receipt.get('effect_count') != (1 if scenario == 'settlement' and outcome == 'settled' else 0)):
            raise ValueError('independent_oracle_mismatch')
        self.emit({'event': 'workflow_verified', 'scenario': scenario, 'process_instance_id': instance['id'], 'outcome': outcome, 'effect_count': receipt['effect_count']})
        return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-url', default=os.environ.get('BENCH_BASE_URL'))
    parser.add_argument('--support-url', default=os.environ.get('BENCH_SUPPORT_URL'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=('normal', 'throttle', 'invalid_tool', 'loop'), default='normal')
    parser.add_argument('--case', choices=('authorized', 'unauthorized', 'over_limit', 'sanctions'), default='authorized')
    parser.add_argument('--wall-s', type=float, default=30)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    auth = 'Basic ' + base64.b64encode((os.environ['BENCH_USERNAME'] + ':' + os.environ['BENCH_PASSWORD']).encode()).decode()
    engine = Client(args.engine_url, auth, ca_file=os.environ.get('BENCH_ENGINE_CA_FILE'))
    support = Client(args.support_url, 'Bearer ' + os.environ['BENCH_SUPPORT_TOKEN'], ca_file=os.environ.get('BENCH_SUPPORT_CA_FILE'))
    payment = {'authorized': args.case != 'unauthorized', 'approved': False,
               'amount_minor': 2000 if args.case == 'over_limit' else 100,
               'limit_minor': 1000, 'sanctions_match': args.case == 'sanctions'}
    with (args.output / 'events.ndjson').open('x', encoding='utf-8') as stream:
        def emit(event):
            stream.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(), **event}) + '\n');stream.flush();os.fsync(stream.fileno())
        request_id = 'agent-' + uuid.uuid4().hex
        emit({'event': 'agent_started', 'request_id': request_id, 'case': args.case, 'mode': args.mode})
        tools = WorkflowTools(engine, support, request_id, payment, emit)
        try:
            result = run_agent(lambda body, deadline: tools.request(support, 'POST', '/model', body, deadline), tools, mode=args.mode, wall_s=args.wall_s, emit=emit)
        except AgentStopped as error:
            result = {'status': 'stopped', 'reason': str(error)}
        except Exception as error:
            result = {'status': 'unknown', 'error_type': type(error).__name__}
        emit({'event': 'agent_finished', **result})
    report = {'request_id': request_id, 'mode': args.mode, 'case': args.case, **result,
              'scope': 'Deterministic model fixture with real Fluxnova KYC/settlement workflows and independent ledger verification; no live model, token accounting, durable agent restart or human approval lifecycle'}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))
    return 0 if result['status'] in ('settled', 'rejected', 'blocked_screening') else 1


if __name__ == '__main__':
    raise SystemExit(main())
