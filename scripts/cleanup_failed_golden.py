#!/usr/bin/env python3
"""Cancel only active instances explicitly owned by a failed golden report.

Defaults to a dry run. Preserves engine history and writes diagnostic snapshots
before cancellation. This is incident cleanup, not a full baseline state reset.
"""
import argparse
import json
from pathlib import Path
import sys
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchlib.fluxnova import FluxnovaClient
from benchlib.util import sha256, utc_now, write_json


def owned_instances(report, config):
    if report.get('passed') is not False or report.get('endpoint') != config['runtime']['base_url']:
        raise ValueError('A failed golden report for this exact endpoint is required')
    if report.get('artifact', {}).get('image') != config['artifact']['image']:
        raise ValueError('Golden report image does not match the configured runtime')
    run_id = report.get('run_id', '')
    if not run_id.startswith('golden-'):
        raise ValueError('Unexpected golden run identity')
    owned = {}
    for case in report['cases']:
        if case['status'] != 'failed':
            continue
        logical = case.get('logical_request_id', '')
        if not logical.startswith(run_id + ':'):
            raise ValueError('Failed case does not belong to this golden run')
        for identity in case.get('process_instance_ids', []):
            if identity in owned and owned[identity] != logical:
                raise ValueError('Conflicting process ownership')
            owned[identity] = logical
    return owned


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError('Choose a new cleanup receipt path')
    config = json.loads(args.config.read_text())
    report = json.loads(args.report.read_text())
    owned = owned_instances(report, config)
    client = FluxnovaClient.from_config(config)
    active = client.request('GET', '/process-instance')
    snapshots = []
    for item in active:
        identity = item['id']
        if identity not in owned:
            continue
        if item.get('businessKey') != owned[identity]:
            raise ValueError('Live instance does not match recorded ownership')
        suffix = '?processInstanceId=' + urllib.parse.quote(identity, safe='')
        snapshots.append({'instance': item, 'incidents': client.request('GET', '/incident' + suffix),
                          'external_tasks': client.request('GET', '/external-task' + suffix)})
    receipt = {'created_at': utc_now(), 'report_sha256': sha256(args.report), 'apply': args.apply,
               'scope': 'Cancel recorded failed golden instances; preserve engine history',
               'snapshots': snapshots, 'cancelled_ids': [], 'status': 'planned'}
    write_json(args.output, receipt)
    if args.apply:
        for snapshot in snapshots:
            identity = snapshot['instance']['id']
            path = '/process-instance/' + urllib.parse.quote(identity, safe='')
            # Recheck immediately before each mutation; never match a broad prefix.
            live = client.request('GET', path)
            if live.get('businessKey') != owned[identity]:
                raise ValueError('Ownership changed before cancellation')
            client.request('DELETE', path + '?skipCustomListeners=true&skipIoMappings=true')
            receipt['cancelled_ids'].append(identity)
            write_json(args.output, receipt)
        remaining = client.request('GET', '/process-instance')
        if any(item['id'] in owned for item in remaining):
            raise RuntimeError('Recorded failed instances remain active')
        receipt['status'] = 'completed'
        write_json(args.output, receipt)
    print(json.dumps({'status': receipt['status'], 'selected_instances': len(snapshots),
                      'cancelled': len(receipt['cancelled_ids']), 'history_preserved': True}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
