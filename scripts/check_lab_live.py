#!/usr/bin/env python3
"""Exercise the interactive lab against an owned, pinned Fluxnova engine.

No cloud access. Requires Java 21 and the verified distribution used by `bench lab`.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchlib.lab import Engine, EXAMPLES, Lab, connection
from benchlib.lab_model import parse_model
from benchlib.lab_runtime import LocalRuntime
from benchlib.util import write_json


def check(condition, label):
    if not condition:
        raise AssertionError(label)


def wait(lab, condition, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = lab.state()
        if condition(state):
            return state
        time.sleep(.1)
    raise AssertionError('Live lab check timed out')


def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    owned = LocalRuntime(output / 'runtime', args.runtime, args.distribution_archive, args.java, args.port)
    lab, engine, sentinel_id = None, None, None
    old_user, old_password = os.environ.get('LAB_CHECK_USERNAME'), os.environ.get('LAB_CHECK_PASSWORD')
    report = {'scope': 'Local functional integration, not a performance result', 'status': 'running', 'checks': []}
    try:
        client = owned.start()
        # Exercise the existing-engine adapter against an independently owned runtime.
        os.environ['LAB_CHECK_USERNAME'], os.environ['LAB_CHECK_PASSWORD'] = 'lab', owned.password
        connect = output / 'environment.local.json'
        write_json(connect, {'schema_version': '1.0', 'kind': 'existing-engine', 'base_url': client.base_url,
                            'auth_username_env': 'LAB_CHECK_USERNAME', 'auth_password_env': 'LAB_CHECK_PASSWORD'})
        engine = connection(connect)
        sentinel = parse_model((EXAMPLES / 'approval.bpmn').read_text(), 'outside_lab_' + uuid.uuid4().hex[:10])
        sentinel_id = engine.deploy(sentinel)['id']
        lab = Lab(engine, output / 'session', mode='existing-engine')
        report['checks'].append('existing-engine credentials and connection')
        for name in ('sequential', 'parallel'):
            model = lab.upload((EXAMPLES / (name + '.bpmn')).read_text(), name)
            lab.start({'model': model['id'], 'count': 2, 'delay_ms': 500, 'poll_ms': 100, 'deadline_s': 30, 'expected_end': 'done'})
            state = wait(lab, lambda s: s['runs'][-1]['status'] != 'running')
            run = state['runs'][-1]
            check(run['status'] == 'finished', 'starter run failed')
            check(all(i['status'] == 'completed' and i['expected_end_matches'] is True for i in run['instances']), 'expected ends not verified')
            if name == 'parallel':
                for i in run['instances']:
                    acts = {a['activityId']: a for a in i['activities']}
                    # Prove overlap from engine history, not a wall-time speed claim.
                    check(datetime.fromisoformat(acts['kyc']['startTime']) < datetime.fromisoformat(acts['risk']['endTime']) and
                          datetime.fromisoformat(acts['risk']['startTime']) < datetime.fromisoformat(acts['kyc']['endTime']), 'parallel checks did not overlap')
            report['checks'].append(name + ' execution and end-event check')
        model = lab.upload((EXAMPLES / 'approval.bpmn').read_text(), 'approval')
        for approved, expected, match in ((True, 'approved', True), (False, 'rejected', True), (False, 'approved', False)):
            lab.start({'model': model['id'], 'expected_end': expected, 'variables': {'approved': False}})
            state = wait(lab, lambda s: bool(s['runs'][-1]['instances']) and bool(s['runs'][-1]['instances'][0]['tasks']))
            instance = state['runs'][-1]['instances'][0]
            lab.complete({'instance': instance['id'], 'task': instance['tasks'][0]['id'], 'variables': {'approved': approved}})
            state = wait(lab, lambda s: s['runs'][-1]['status'] != 'running')
            check(state['runs'][-1]['instances'][0]['expected_end_matches'] is match, 'approval expectation check')
        report['checks'].append('approve, reject and retained negative expectation')
        lab.start({'model': model['id'], 'deadline_s': 1})
        state = wait(lab, lambda s: s['runs'][-1]['status'] != 'running')
        check(state['runs'][-1]['instances'][0]['status'] == 'deadline_exceeded', 'deadline not preserved')
        report['checks'].append('deadline cancellation')
        lab.start({'model': model['id']})
        wait(lab, lambda s: bool(s['runs'][-1]['instances']) and bool(s['runs'][-1]['instances'][0]['tasks']))
        lab.stop.set()
        state = wait(lab, lambda s: s['runs'][-1]['status'] != 'running')
        check(state['runs'][-1]['status'] == 'stopped', 'stop failed')
        report['checks'].append('explicit stop')
        write_json(output / 'session.json', lab.state())
        lab.close()
        lab = None
        check(engine.request('GET', '/deployment/' + sentinel_id)['id'] == sentinel_id, 'unrelated deployment was changed')
        check(owned.process.poll() is None and engine.request('GET', '/version'), 'attached engine was stopped')
        report['checks'].append('cleanup preserves other deployments and existing engine')
        report['status'] = 'pass'
    finally:
        try:
            if lab:
                lab.close()
            if engine and sentinel_id:
                engine.request('DELETE', '/deployment/' + sentinel_id + '?cascade=true')
        finally:
            owned.close()
            for key, value in [('LAB_CHECK_USERNAME', old_user), ('LAB_CHECK_PASSWORD', old_password)]:
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            report['owned_engine_stopped'] = bool(owned.process and owned.process.poll() is not None)
            write_json(output / 'check.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, default=ROOT / '.tools/fluxnova-3.0.1')
    parser.add_argument('--distribution-archive', type=Path, default=ROOT / '.tools/fluxnova-3.0.1.zip')
    parser.add_argument('--java', type=Path)
    parser.add_argument('--port', type=int, default=18188)
    run(parser.parse_args())
