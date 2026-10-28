"""Fluxnova REST external-task worker with durable simulator deduplication."""
import argparse
import base64
import hashlib
import json
import math
import os
import signal
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone


def timed_request(stages, stage, client, method, path, body=None):
    """Measure the full client call, including response reading and decoding."""
    if stages is None:
        return client.request(method, path, body)
    record = {'stage': stage, 'started_at': datetime.now(timezone.utc).isoformat()}
    start = time.monotonic_ns()
    try:
        result = client.request(method, path, body)
        record['status'] = 'ok'
        return result
    except Exception as error:
        record.update(status='error', error_type=type(error).__name__)
        raise
    finally:
        record['duration_s'] = (time.monotonic_ns() - start) / 1e9
        stages.append(record)


def emit_timing(event, stages, **fields):
    if stages is not None:
        try:
            print(json.dumps({'event': event, 'stages': stages, **fields}), flush=True)
        except OSError:
            # A diagnostic output failure must not turn a completed task into a retry.
            pass


class Client:
    def __init__(self, base, authorization=None, timeout=10, ca_file=None):
        self.base = base.rstrip('/')
        self.authorization = authorization
        self.timeout = timeout
        self.context = ssl.create_default_context(cafile=ca_file)

    def request(self, method, path, data=None):
        headers = {'Content-Type': 'application/json'}
        if self.authorization:
            headers['Authorization'] = self.authorization
        request = urllib.request.Request(self.base + path, data=None if data is None else json.dumps(data).encode(), headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=self.timeout, context=self.context) as response:
            body = response.read()
            return json.loads(body) if body else None

    def request_bytes(self, path):
        headers = {'Authorization': self.authorization} if self.authorization else {}
        with urllib.request.urlopen(urllib.request.Request(self.base + path, headers=headers), timeout=self.timeout, context=self.context) as response:
            return response.read()


def task_request(task):
    variables = {k: v.get('value') for k, v in task.get('variables', {}).items()}
    scenario = variables.get('scenario', 'async')
    operation = {'async': 'echo', 'kyc': 'kyc', 'risk': 'risk', 'settlement': 'settle'}.get(scenario)
    if operation is None:
        raise ValueError('unsupported_scenario')
    payload = {'payload': variables.get('payload', ''), 'authorized': variables.get('authorized', True), 'approved': variables.get('approved', False), 'sanctions_match': variables.get('sanctions_match', False), 'high_risk': variables.get('high_risk', False), 'amount_minor': variables.get('amount_minor', 100), 'limit_minor': variables.get('limit_minor', 1000), 'notional_minor': variables.get('notional_minor', 100), 'stale_market': variables.get('stale_market', False)}
    key = variables.get('logical_request_id') or task.get('businessKey')
    if not key:
        raise ValueError('missing_logical_request_id')
    return {'idempotency_key': key, 'operation': operation, 'payload': payload, 'fault': variables.get('fault', 'none'), 'delay_ms': variables.get('delay_ms', 0)}


def process_task(task, engine, support, worker_id, retry_limit=3, timing=False):
    task_id = urllib.parse.quote(task['id'], safe='')
    stages = [] if timing else None
    completed = False
    try:
        request = task_request(task)
        try:
            result = timed_request(stages, 'tool_execute', support, 'POST', '/tools/execute', request)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as failure:
            if isinstance(failure, urllib.error.HTTPError) and failure.code < 500:
                raise
            # Reconcile all ambiguous transport/server errors before allowing a retry.
            state = timed_request(stages, 'tool_reconcile', support, 'GET', '/state/' + urllib.parse.quote(request['idempotency_key'], safe=''))
            if not state or not state.get('result'):
                raise RuntimeError('dependency_failure_without_confirmed_effect')
            canonical = json.dumps({'operation': request['operation'], 'payload': request['payload']}, sort_keys=True, separators=(',', ':'))
            if state.get('fingerprint') != hashlib.sha256(canonical.encode()).hexdigest():
                raise ValueError('reconciliation_payload_conflict')
            result = state['result']
        timed_request(stages, 'engine_complete', engine, 'POST', '/external-task/' + task_id + '/complete', {'workerId': worker_id, 'variables': {'workerResult': {'value': result['outcome'], 'type': 'String'}, 'workerTransientAbsent': {'value': 'aVariable' not in task.get('variables', {}), 'type': 'Boolean'}}})
        completed = True
        return True
    except Exception as error:
        retries = task.get('retries')
        remaining = max(0, (retry_limit if retries is None else retries) - 1)
        # Do not log payloads, secrets, or exception messages that can contain URLs.
        print(json.dumps({'event': 'worker_failure', 'task_id': task.get('id'), 'error_type': type(error).__name__, 'retries_remaining': remaining}), flush=True)
        try:
            timed_request(stages, 'engine_failure', engine, 'POST', '/external-task/' + task_id + '/failure', {'workerId': worker_id, 'errorMessage': 'benchmark_worker_' + type(error).__name__, 'retries': remaining, 'retryTimeout': min(1000 * (2 ** (retry_limit - remaining)), 10000)})
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            # Lock expiry permits a later worker to reconcile; never blind-repeat starts.
            pass
        return False
    finally:
        emit_timing('worker_task_timing', stages, task_id=task.get('id'),
                    process_instance_id=task.get('processInstanceId'), completed=completed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-url', default=os.environ.get('BENCH_BASE_URL', 'http://127.0.0.1:18080/engine-rest'))
    parser.add_argument('--support-url', default=os.environ.get('BENCH_SUPPORT_URL', 'http://127.0.0.1:18081'))
    parser.add_argument('--worker-id', default='bench-' + uuid.uuid4().hex)
    parser.add_argument('--poll-s', type=float, default=0.1,
                        help='Delay after an empty poll or failure; successful work continues immediately')
    parser.add_argument('--success-delay-s', type=float, default=0,
                        help='Research control: optional delay after successful work; default remains zero')
    parser.add_argument('--max-tasks', type=int, default=1)
    parser.add_argument('--retries', type=int, default=3)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--timing', action='store_true', default=os.environ.get('BENCH_WORKER_TIMING') == '1',
                        help='Emit diagnostic stage durations; disabled by default')
    args = parser.parse_args()
    if args.max_tasks != 1:
        parser.error('max-tasks must be 1: sequential processing must not allow queued locks to expire')
    if (args.retries < 1 or not math.isfinite(args.poll_s) or args.poll_s < 0
            or not math.isfinite(args.success_delay_s) or not 0 <= args.success_delay_s <= 1):
        parser.error('retries must be positive; poll-s finite/nonnegative; success-delay-s between zero and one')
    auth = None
    if os.environ.get('BENCH_USERNAME'):
        auth = 'Basic ' + base64.b64encode((os.environ['BENCH_USERNAME'] + ':' + os.environ.get('BENCH_PASSWORD', '')).encode()).decode()
    support_auth = 'Bearer ' + os.environ['BENCH_SUPPORT_TOKEN'] if os.environ.get('BENCH_SUPPORT_TOKEN') else None
    engine = Client(args.engine_url, auth, ca_file=os.environ.get('BENCH_ENGINE_CA_FILE'))
    support = Client(args.support_url, support_auth, ca_file=os.environ.get('BENCH_SUPPORT_CA_FILE'))
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while running:
        completed = False
        stages = [] if args.timing else None
        try:
            try:
                tasks = timed_request(stages, 'engine_fetch', engine, 'POST', '/external-task/fetchAndLock', {'workerId': args.worker_id, 'maxTasks': 1, 'usePriority': True, 'topics': [{'topicName': 'bench-service', 'lockDuration': 60000}]})
            finally:
                emit_timing('worker_poll_timing', stages)
            for task in tasks or []:
                completed = process_task(task, engine, support, args.worker_id, args.retries, args.timing)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            print(json.dumps({'event': 'worker_poll_failure'}), flush=True)
        if args.once:
            break
        delay = args.success_delay_s if completed else args.poll_s
        if running and (not completed or delay > 0):
            time.sleep(delay)


if __name__ == '__main__':
    main()
