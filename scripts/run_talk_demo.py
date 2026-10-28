#!/usr/bin/env python3
"""Visible synthetic payment review using the prepared OCI cell, with optional recording.

Runs one approval by default. Retrieves credentials into the child environment,
creates temporary restricted users, invokes the existing browser check and cleans
up its users/grants/filter. No native model requests or infrastructure deployment.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--profile', default='BENCHMARK')
    p.add_argument('--secret-references', type=Path, default=ROOT / 'dist/setup-20261013/application-secrets.local.json')
    p.add_argument('--verifier-config', type=Path, default=ROOT / 'dist/setup-20261013/persistent-verifier-config.local.json')
    p.add_argument('--playwright-path', type=Path, default=ROOT / 'dist/browser-review-tools')
    p.add_argument('--admin-username', default='benchmark')
    p.add_argument('--cases', choices=('approve', 'reject', 'both'), default='approve')
    p.add_argument('--record', action='store_true')
    p.add_argument('--headless', action='store_true', help='Record without a visible browser')
    args = p.parse_args(argv)
    if args.output.exists():
        p.error('Use a new output directory. Inspect previous evidence before another run.')
    for path in [args.secret_references, args.verifier_config, args.playwright_path]:
        if not path.exists():
            p.error('Prepared demo prerequisites are missing. See docs/presenter-demo.md.')
    for port in [19080, 19081, 19082]:
        with socket.create_connection(('127.0.0.1', port), timeout=2):
            pass
    import oci
    from services.live_recoverable_agent import identity
    cfg, signer, _ = identity(args.profile)
    vault = oci.secrets.SecretsClient(cfg, signer=signer, timeout=(10, 30),
                                    retry_strategy=oci.retry.NoneRetryStrategy())
    refs = json.loads(args.secret_references.read_text(encoding='utf-8-sig'))

    def secret(name):
        bundle = vault.get_secret_bundle(refs[name], stage='CURRENT',
                                         retry_strategy=oci.retry.NoneRetryStrategy())
        return base64.b64decode(bundle.data.secret_bundle_content.content).decode()

    env = {**os.environ, 'BENCH_USERNAME': args.admin_username,
           'BENCH_PASSWORD': secret('admin_password_secret_id'),
           'BENCH_SUPPORT_TOKEN': secret('support_token_secret_id'),
           'BENCH_BASE_URL': 'http://127.0.0.1:19080/engine-rest',
           'BENCH_SUPPORT_URL': 'http://127.0.0.1:19081',
           'PYTHONPATH': str(ROOT), 'PYTHONUNBUFFERED': '1'}
    command = [sys.executable, str(ROOT / 'scripts/run_browser_review_checks.py'),
               '--output', str(args.output.resolve()), '--profile', args.profile,
               '--verifier-config', str(args.verifier_config.resolve()),
               '--playwright-path', str(args.playwright_path.resolve()),
               '--form-mode', 'payment', '--cases', args.cases,
               '--slow-mo-ms', '300', '--demo-hold-s', '4']
    if not args.headless:
        command.append('--headed')
    if args.record:
        command.append('--record-video')
    try:
        result = subprocess.run(command, cwd=ROOT, env=env)
    finally:
        env.pop('BENCH_PASSWORD', None)
        env.pop('BENCH_SUPPORT_TOKEN', None)
    if result.returncode:
        print('Demo did not pass. Inspect failure.json, progress.json and cleanup.json. Do not blindly retry.', file=sys.stderr)
    elif args.record:
        print('Browser recording saved under ' + str(args.output / 'video') + '. Review before presenting.')
    return result.returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print('Demo preflight failed (' + type(error).__name__ + '). See docs/presenter-demo.md.', file=sys.stderr)
        raise SystemExit(2)
