"""Dedicated W02 external-task worker; identical slot count for both BPMN arms."""
import argparse
import base64
import json
import os
import signal
import threading
import time
import urllib.error
import urllib.parse
import uuid

from services.worker import Client

TOPIC = 'bench-w02-checks'
ACTIVITIES = {'kyc_check': ('kyc', 'w02Kyc'), 'risk_check': ('risk', 'w02Risk'),
              'verify_settle': ('settle', 'w02Outcome')}


def process_task(task, engine, support, worker_id):
    task_path = '/external-task/' + urllib.parse.quote(task['id'], safe='')
    operation = 'unknown'
    result = None
    try:
        operation, variable = ACTIVITIES[task['activityId']]
        key = task['variables']['studyCaseKey']['value']
        request = {'case_key': key, 'process_id': task['processInstanceId'], 'operation': operation}
        # A newly started process can be fetched just before its trusted harness
        # registration arrives. Wait briefly, without executing an unbound case.
        ready_deadline = time.monotonic() + 3
        while result is None:
            try:
                result = support.request('POST', '/tools/execute', request)
            except urllib.error.HTTPError as error:
                if error.code == 409:
                    reason = json.loads(error.read()).get('error')
                    if reason == 'case_not_registered' and time.monotonic() < ready_deadline:
                        time.sleep(.025)
                        continue
                    raise
                if error.code < 500:
                    raise
                state = support.request('GET', '/state/' + urllib.parse.quote(key, safe=''))
                result = state['receipts'].get(operation)
                if result is None:
                    raise RuntimeError('dependency_unconfirmed') from None
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                state = support.request('GET', '/state/' + urllib.parse.quote(key, safe=''))
                result = state['receipts'].get(operation)
                if result is None:
                    raise RuntimeError('dependency_unconfirmed') from None
        if any(result.get(k) != v for k, v in request.items()):
            raise ValueError('receipt_identity_mismatch')
        engine.request('POST', task_path + '/complete', {
            'workerId': worker_id, 'variables': {variable: {'value': result['outcome'], 'type': 'String'}}})
        return {'status': 'completed', 'operation': operation, 'task_id': task['id']}
    except Exception as error:
        retries = max(0, (3 if task.get('retries') is None else task['retries']) - 1)
        try:
            engine.request('POST', task_path + '/failure', {
                'workerId': worker_id, 'errorMessage': 'w02_' + type(error).__name__,
                'retries': retries, 'retryTimeout': 100})
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        return {'status': 'retry_or_incident', 'operation': operation,
                'task_id': task['id'], 'error_type': type(error).__name__, 'retries': retries}


class WorkerPool:
    def __init__(self, engine_factory, support_factory, slots=2, idle_s=.1):
        if type(slots) is not int or slots < 1 or slots > 32 or idle_s < 0:
            raise ValueError('invalid_worker_pool')
        self.stop_event = threading.Event()
        self.threads = []
        self.records = []
        self.record_lock = threading.Lock()
        self.engine_factory, self.support_factory = engine_factory, support_factory
        self.slots, self.idle_s = slots, idle_s

    def run(self, index):
        worker_id = 'w02-' + uuid.uuid4().hex + '-' + str(index)
        engine, support = self.engine_factory(), self.support_factory()
        while not self.stop_event.is_set():
            successful = False
            try:
                tasks = engine.request('POST', '/external-task/fetchAndLock', {
                    'workerId': worker_id, 'maxTasks': 1, 'usePriority': False,
                    'topics': [{'topicName': TOPIC, 'lockDuration': 60000,
                                'variables': ['studyCaseKey']}]})
                for task in tasks or []:
                    record = process_task(task, engine, support, worker_id)
                    with self.record_lock:
                        self.records.append(record)
                    successful = record['status'] == 'completed'
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                with self.record_lock:
                    self.records.append({'status': 'fetch_error'})
            except Exception as error:
                with self.record_lock:
                    self.records.append({'status':'fatal_worker_error','error_type':type(error).__name__})
                self.stop_event.set()
            if not successful:
                self.stop_event.wait(self.idle_s)

    def start(self):
        for i in range(self.slots):
            thread = threading.Thread(target=self.run, args=(i,), daemon=True)
            self.threads.append(thread)
            thread.start()
        return self

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=15)
        if any(thread.is_alive() for thread in self.threads):
            raise RuntimeError('worker_shutdown_incomplete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-url', default='http://127.0.0.1:28180/engine-rest')
    parser.add_argument('--support-url', default='http://127.0.0.1:28181')
    parser.add_argument('--slots', type=int, default=2)
    args = parser.parse_args()
    auth = None
    if os.environ.get('BENCH_USERNAME'):
        auth = 'Basic ' + base64.b64encode((os.environ['BENCH_USERNAME'] + ':' + os.environ['BENCH_PASSWORD']).encode()).decode()
    token = os.environ['W02_WORKER_TOKEN']
    pool = WorkerPool(lambda: Client(args.engine_url, auth),
                      lambda: Client(args.support_url, 'Bearer ' + token), args.slots)
    signal.signal(signal.SIGTERM, lambda *_: pool.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: pool.stop_event.set())
    pool.start()
    try:
        while not pool.stop_event.wait(.5):
            pass
    finally:
        pool.stop()


if __name__ == '__main__':
    main()
