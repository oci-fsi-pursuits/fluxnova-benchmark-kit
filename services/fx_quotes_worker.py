"""Three equal worker slots for the PAY-05 sequential and parallel models."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
import queue
import signal
import uuid

from services.fx_quotes import ACTIVITIES, TOPIC
from services.worker import Client
from services.workflow_checks_worker import WorkerPool


def main():
    started_at = datetime.now(timezone.utc).isoformat()
    execution_id = uuid.uuid4().hex
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-url', required=True)
    parser.add_argument('--support-url', required=True)
    args = parser.parse_args()
    auth = 'Basic ' + base64.b64encode((os.environ['BENCH_USERNAME'] + ':' + os.environ['BENCH_PASSWORD']).encode()).decode()
    engines, dependencies = queue.SimpleQueue(), queue.SimpleQueue()
    for _ in range(3):
        engines.put(Client(args.engine_url, auth))
        dependencies.put(Client(args.support_url, 'Bearer ' + os.environ['PAY05_WORKER_TOKEN']))
    pool = WorkerPool(engines.get_nowait, dependencies.get_nowait, slots=3, idle_s=.1,
                      topic=TOPIC, activities=ACTIVITIES, error_prefix='pay05')
    for sig in (signal.SIGTERM, signal.SIGINT): signal.signal(sig, lambda *_: pool.stop_event.set())
    pool.start()
    try:
        while not pool.stop_event.wait(.5): pass
    finally:
        pool.stop()
        print(json.dumps({'type': 'pay05_worker_run', 'execution_id': execution_id, 'started_at': started_at,
                          'finished_at': datetime.now(timezone.utc).isoformat(), 'worker_slots': 3,
                          'idle_s': .1, 'records': pool.records}), flush=True)


if __name__ == '__main__': main()
