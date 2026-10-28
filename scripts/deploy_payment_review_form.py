#!/usr/bin/env python3
"""Deploy just the bound-review BPMN and its embedded form to an idle definition.

Uses the existing trusted BENCH_* environment. Idempotent when the current
deployment already contains these exact resources. Never migrates instances.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import urllib.request
from urllib.parse import urlencode, urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.bound_payment import DEFINITION
from services.payment_approval import clients
from services.recoverable_agent import atomic_json


def deploy(expected_version):
    engine, _ = clients()
    parsed = urlsplit(engine.base)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.username:
        raise ValueError('deployment_requires_private_local_forward')
    resources = {name: (ROOT / 'workloads' / name).read_bytes().replace(b'\r\n', b'\n')
                 for name in ('bound-payment-approval.bpmn', 'forms/payment-review.html')}
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in resources.items()}
    current = engine.request('GET', '/process-definition/key/' + DEFINITION)

    def resource_hashes(deployment_id):
        rows = engine.request('GET', '/deployment/' + deployment_id + '/resources')
        return {row['name']: hashlib.sha256(engine.request_bytes('/deployment/' + deployment_id + '/resources/' + row['id'] + '/data')).hexdigest()
                for row in rows}

    previous_hashes = resource_hashes(current['deploymentId'])
    if previous_hashes == hashes:
        return {'changed': False, 'definition': current, 'resource_sha256_lf': hashes}
    if current['version'] != expected_version:
        raise ValueError('current_review_definition_version_changed')
    queues = {name: engine.request('GET', path + '?' + urlencode({'processDefinitionKey': DEFINITION}))['count']
              for name, path in [('instances', '/process-instance/count'), ('external_tasks', '/external-task/count')]}
    if any(queues.values()):
        raise ValueError('drain_bound_review_before_form_deployment')
    boundary = 'fluxnova-form-' + uuid.uuid4().hex
    parts = []
    for name, value in {'deployment-name': 'benchmark-bound-payment-review-form',
                        'enable-duplicate-filtering': 'true', 'deploy-changed-only': 'false'}.items():
        parts.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="' + name + '"\r\n\r\n' + value + '\r\n').encode())
    for index, (name, raw) in enumerate(resources.items()):
        parts.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="resource' + str(index) + '"; filename="' + name + '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode() + raw + b'\r\n')
    parts.append(('--' + boundary + '--\r\n').encode())
    request = urllib.request.Request(engine.base + '/deployment/create', data=b''.join(parts),
        headers={'Authorization': engine.authorization, 'Content-Type': 'multipart/form-data; boundary=' + boundary}, method='POST')
    # A lost response is ambiguous: inspect the current definition/resources;
    # do not issue an automatic retry of deployment creation.
    with urllib.request.urlopen(request, timeout=30) as response:
        deployment = json.load(response)
    after = engine.request('GET', '/process-definition/key/' + DEFINITION)
    if (after['deploymentId'] != deployment['id'] or after['version'] != current['version'] + 1
            or resource_hashes(deployment['id']) != hashes):
        raise ValueError('deployed_review_form_does_not_match_sources')
    return {'changed': True, 'previous_definition': current, 'previous_resource_sha256': previous_hashes,
            'definition': after, 'deployment_id': deployment['id'], 'resource_sha256_lf': hashes,
            'queues_before': queues, 'instances_migrated': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-current-version', type=int, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    if args.receipt.exists():
        parser.error('receipt already exists; retain it and choose a new path')
    receipt = deploy(args.expected_current_version)
    atomic_json(args.receipt, receipt)
    print(json.dumps({'changed': receipt['changed'], 'definition_version': receipt['definition']['version'],
                      'resource_sha256_lf': receipt['resource_sha256_lf']}))
