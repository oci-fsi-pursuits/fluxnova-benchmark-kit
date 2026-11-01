#!/usr/bin/env python3
"""Collect read-only metrics on the controller and push fresh snapshots over SSH.

Uses existing controller-to-role SSH access. The load generator receives numeric
metrics only; it receives neither SSH keys nor database credentials.
"""
import argparse
import base64
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchlib.cloud import sdk_client
from benchlib.remote import host_target, shell_command, SSH_OPTIONS, PYTHON, WORKSPACE
from benchlib.util import utc_now
from scripts.collect_metrics import postgres_metrics


def validate_release(path):
    value = PurePosixPath(path)
    if value.parent != PurePosixPath(WORKSPACE) or not value.name.startswith('release-') or '..' in value.parts:
        raise ValueError('Deployment directory must be a direct release child of the benchmark workspace')
    return str(value)


def publish(target, release, snapshots):
    code = """import json,os,sys
from pathlib import Path
root=Path(sys.argv[1])/'dist/telemetry-live'
root.mkdir(parents=True,exist_ok=True)
for name,value in json.load(sys.stdin).items():
    if name not in ('application','database'): raise ValueError('Unexpected snapshot name')
    temp=root/(name+'.tmp')
    temp.write_text(json.dumps(value)+'\\n')
    os.replace(temp,root/(name+'.json'))
"""
    subprocess.run(['ssh', *SSH_OPTIONS, target, shell_command([PYTHON, '-c', code, release])],
                   input=json.dumps(snapshots).encode(), check=True, capture_output=True, timeout=15)


def database_snapshot(settings, connection_attempts=1):
    """Optionally retry one connection timeout, retaining every attempt's metadata.

    Query failures and authentication errors are not retried. The final snapshot
    is either newly collected data or an explicit error; no cached values or
    timestamps are substituted. postgres_metrics bounds connection/query waits.
    """
    if type(connection_attempts) is not int or connection_attempts not in (1, 2):
        raise ValueError('Database connection attempts must be 1 or 2')
    attempts = []
    for number in range(1, connection_attempts + 1):
        observed = utc_now()
        try:
            snapshot = postgres_metrics(settings, engine=True)
        except Exception as exc:
            finished = utc_now()
            attempts.append({'attempt': number, 'started_at': observed, 'finished_at': finished,
                             'error_type': type(exc).__name__})
            if number < connection_attempts:
                from psycopg.errors import ConnectionTimeout
                if isinstance(exc, ConnectionTimeout):
                    continue
            return {'_meta': {'observed_at': finished, 'errors': {'database': type(exc).__name__},
                              'read_only': True, 'connection_attempt_limit': connection_attempts,
                              'collection_attempts': attempts}}
        else:
            attempts.append({'attempt': number, 'started_at': observed, 'finished_at': utc_now(),
                             'error_type': None})
            snapshot['_meta'] = {'observed_at': observed, 'errors': {}, 'read_only': True,
                                 'connection_attempt_limit': connection_attempts, 'collection_attempts': attempts}
            return snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--environment', type=Path, required=True)
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--db-secret-id', required=True, help='Vault secret reference, never a password')
    parser.add_argument('--db-user', default='fluxnova_metrics')
    parser.add_argument('--db-connection-attempts', type=int, choices=(1, 2), default=1,
                        help='Declared connection attempts per database sample; 2 permits one ConnectionTimeout retry and retains both attempts')
    parser.add_argument('--interval-s', type=float, default=10)
    parser.add_argument('--duration-s', type=int, default=1800)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not 5 <= args.interval_s <= 30 or not 10 <= args.duration_s <= 86400:
        parser.error('Interval must be 5-30 seconds; duration must be 10-86400 seconds')
    config = json.loads(args.config.read_text())
    environment = json.loads(args.environment.read_text())
    inventory = json.loads(args.deployment.read_text())
    if inventory.get('status') != 'completed':
        raise ValueError('A completed deployment inventory is required')
    for field in ('region', 'compartment_id'):
        if config['campaign'][field] != environment[field]:
            raise ValueError('Environment and configuration identity differ')
    release = validate_release(inventory['remote_root'])
    targets = {role: host_target(environment['roles'][role]) for role in ('app', 'loadgen')}
    for role, target in targets.items():
        if inventory['hosts'][role]['target'] != target:
            raise ValueError('Deployment inventory target mismatch')
    _, client = sdk_client(config, 'secrets')
    bundle = client.get_secret_bundle(args.db_secret_id, version_number=1).data.secret_bundle_content
    os.environ['BENCH_DB_PASSWORD'] = base64.b64decode(bundle.content).decode()
    db = environment['database']
    settings = {'host': db['host'], 'port': db['port'], 'database': db['database'],
                'user': args.db_user, 'ca_file': db['ca_file']}
    app_command = [PYTHON, release + '/scripts/collect_metrics.py', '--host', '--container', 'fluxnova-benchmark']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.duration_s
    with args.output.open('x', encoding='utf-8') as audit:
        while time.monotonic() < deadline:
            started = time.monotonic()
            snapshots = {}
            for name in ('application', 'database'):
                try:
                    if name == 'application':
                        response = subprocess.run(['ssh', *SSH_OPTIONS, targets['app'], shell_command(app_command)],
                                                  check=True, capture_output=True, text=True, timeout=10)
                        snapshot = json.loads(response.stdout)
                    else:
                        snapshot = database_snapshot(settings, args.db_connection_attempts)
                except Exception as exc:
                    snapshot = {'_meta': {'observed_at': utc_now(), 'errors': {name: type(exc).__name__}}}
                snapshots[name] = snapshot
            record = {'at': utc_now(), 'snapshots': snapshots}
            try:
                publish(targets['loadgen'], release, snapshots)
                record['published'] = True
            except Exception as exc:
                record.update(published=False, error_type=type(exc).__name__)
            record['collection_seconds'] = time.monotonic() - started
            audit.write(json.dumps(record) + '\n')
            audit.flush()
            print(json.dumps({'at': record['at'], 'published': record['published'],
                              'source_errors': {n: s['_meta'].get('errors', {}) for n, s in snapshots.items()},
                              'database_collection_attempts': snapshots['database']['_meta'].get('collection_attempts', [])}), flush=True)
            time.sleep(max(0, min(args.interval_s - (time.monotonic() - started), deadline - time.monotonic())))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
