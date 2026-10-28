"""Bounded retries for operator status reads, outside measured workload paths."""
import json
import subprocess
import time


def read_status_with_retries(read, *, attempts=3, backoff_s=5, wait=time.sleep):
    """Retry only a read-only callback's transport or incomplete-JSON failures.

    The callback must impose its own per-call timeout. Do not pass provisioning,
    workload execution, payment submission or other mutation callbacks here.
    A returned failed-workload status is data and is never retried.
    """
    if type(attempts) is not int or not 1<=attempts<=5 or not 0<=backoff_s<=30:
        raise ValueError('Invalid status polling bound')
    for attempt in range(attempts):
        try:return read()
        except (ConnectionError,TimeoutError,subprocess.TimeoutExpired,json.JSONDecodeError):
            if attempt+1==attempts:raise
            wait(backoff_s)
