#!/usr/bin/env python3
"""Bounded real-engine approval lifecycle checks; no live model calls.

Synthetic reviewer uses existing administrator credentials. The three crash
cases terminate a separate owner process after durable engine boundaries.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchlib.fluxnova import FluxnovaClient
from services.payment_approval import Approval, clients, typed
from services.recoverable_agent import atomic_json, owner

PAYMENT = {'amount_minor': 2000, 'limit_minor': 1000, 'authorized': True, 'sanctions_match': False}
CASES = ['approve', 'reject', 'unauthorized', 'sanctions', 'kill_pending', 'kill_complete_response',
         'kill_start_response', 'changed_payment', 'preset_approval']


def settled(review):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        result = review.status()
        if result['state'] == 'completed': return result
        time.sleep(.2)
    raise TimeoutError('approval_completion_not_observed')


def crash_child(folder, boundary, action):
    marker = folder / 'boundary.json'
    code = '''import json,sys,time
from pathlib import Path
from services.payment_approval import Approval,clients
from services.recoverable_agent import atomic_json,owner
folder=Path(sys.argv[1]); boundary=sys.argv[2]; action=sys.argv[3]
def checkpoint(event):
    if event==boundary:
        atomic_json(folder/'boundary.json',{'event':event})
        while True:time.sleep(1)
with owner(folder):
    review=Approval(folder,*clients(),checkpoint=checkpoint)
    if action=='start':
        result=review.start()
        checkpoint('pending')
    else:result=review.decide(action)
'''
    with (folder / 'child.log').open('wb') as log:
        process = subprocess.Popen([sys.executable, '-c', code, str(folder), boundary, action],
                                   cwd=ROOT, stdout=log, stderr=log, env={**os.environ, 'PYTHONPATH': str(ROOT)})
        deadline = time.monotonic() + 60
        try:
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.1)
            if not marker.exists(): raise RuntimeError('child_boundary_not_reached')
        finally:
            if process.poll() is None: process.kill()
            process.wait(timeout=10)
    return {'boundary': boundary, 'returncode': process.returncode, 'termination': 'subprocess.kill', 'platform': sys.platform}


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    sources = output / 'sources'; sources.mkdir()
    for source in ('services/payment_approval.py', 'scripts/run_payment_approval_checks.py', 'workloads/payment-approval.bpmn'):
        shutil.copyfile(ROOT / source, sources / Path(source).name)
    engine, support = clients()
    deploy = FluxnovaClient(engine.base, os.environ['BENCH_USERNAME'], os.environ['BENCH_PASSWORD']).deploy(
        [ROOT / 'workloads/payment-approval.bpmn'])
    atomic_json(output / 'deployment.json', deploy)
    atomic_json(output / 'manifest.json', {'cases': CASES, 'model_calls': 0, 'simulated_reviewer': True})
    results = []
    for name in CASES:
        folder = output / name; folder.mkdir()
        payment = {**PAYMENT, 'authorized': name != 'unauthorized', 'sanctions_match': name == 'sanctions'}
        with owner(folder): review = Approval(folder, engine, support, payment)
        record = {'case': name}
        try:
            if name.startswith('kill_'):
                boundary = name.removeprefix('kill_')
                if boundary == 'complete_response':
                    with owner(folder): record['pending'] = review.start()
                record['crash'] = crash_child(folder, boundary, 'approve' if boundary == 'complete_response' else 'start')
                review = Approval(folder, engine, support)
                with owner(folder):
                    current = review.start()
                    record['after_restart'] = current
                    if boundary != 'complete_response' and current['state'] != 'pending':
                        raise ValueError('pending_review_not_restored')
            else:
                with owner(folder): record['pending'] = review.start()
            if 'pending' in record and record['pending']['state'] != 'pending':
                raise ValueError('review_did_not_wait')
            pid = review.data['process_id']
            if name == 'changed_payment':
                engine.request('PUT', '/process-instance/' + pid + '/variables/paymentAmountMinor',
                               {'value': 3000, 'type': 'Long'})
                with owner(folder):
                    try: review.decide('approve')
                    except ValueError as error:
                        if str(error) != 'payment_snapshot_changed': raise
                        record['tamper_blocked'] = str(error)
                    else: raise ValueError('tamper_accepted')
                engine.request('PUT', '/process-instance/' + pid + '/variables/paymentAmountMinor',
                               {'value': 2000, 'type': 'Long'})
            if name == 'preset_approval':
                engine.request('POST', '/process-instance/' + pid + '/variables',
                               {'modifications': typed({'approved': True, 'reviewerDecision': 'approve'})})
                record['after_preset'] = review.status()
                if record['after_preset']['state'] != 'pending': raise ValueError('preset_bypassed_review')
            decision = 'reject' if name in ('reject', 'changed_payment', 'preset_approval') else 'approve'
            with owner(folder):
                review.decide(decision)
                result = settled(review)
                replay = review.decide(decision)
            if replay != result: raise ValueError('replay_changed_result')
            task = review.data['task_id']
            try: engine.request('POST', '/task/' + task + '/complete', {'variables': typed({'reviewerDecision': decision})})
            except Exception as error:
                import urllib.error
                if not isinstance(error, urllib.error.HTTPError) or error.code not in (400, 404, 500): raise
                record['raw_completion_replay_http'] = error.code
                # This engine can return 500 for an already absent task. Retain
                # the response; only subsequent history/ledger verification
                # establishes that replay caused no additional effect.
                (folder / 'raw-replay-error.json').write_bytes(error.read())
                error.close()
            else: raise ValueError('completed_task_accepted_replay')
            with owner(folder): final = review.status()
            record.update(status='pass', result=final,
                          ledger=support.request('GET', '/state/' + review.data['key']), history=review.history())
            results.append(record); atomic_json(folder / 'result.json', record)
            atomic_json(output / 'summary.json', {'status': 'running', 'results': results})
            print(json.dumps({'case': name, 'status': 'pass', 'outcome': final['outcome'], 'effects': final['effect_count']}), flush=True)
        except Exception as error:
            record.update(status='failed', error_type=type(error).__name__)
            atomic_json(folder / 'failure.json', record)
            atomic_json(output / 'summary.json', {'status': 'failed', 'results': results, 'failure': record})
            raise
    queues = {key: engine.request('GET', path)['count'] for key, path in
              [('active_instances', '/process-instance/count'), ('external_tasks', '/external-task/count'), ('incidents', '/incident/count')]}
    summary = {'status': 'pass' if not any(queues.values()) else 'failed', 'results': results, 'queues': queues,
               'model_calls': 0, 'simulated_reviewer': True}
    atomic_json(output / 'summary.json', summary)
    if summary['status'] != 'pass': raise ValueError('queues_not_empty')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
