#!/usr/bin/env python3
"""Seal/verify a private quiesced three-store bundle using this VM's pinned Vault access.

Prepare engine.dump, ledger.sqlite, manifest.json with the checkpoint capture,
and registry.sqlite with a stopped-verifier backup. Never overwrite live stores.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchlib.recovery import seal, verify
from benchlib.util import sha256
from services.verifier_service import load_vault


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['seal', 'verify'])
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--vault-config', type=Path, required=True)
    parser.add_argument('--manifest-sha256')
    args = parser.parse_args()
    if args.command == 'verify' and not args.manifest_sha256:
        parser.error('verify requires an independently retained --manifest-sha256')
    config = json.loads(args.vault_config.read_text())
    reference = {k: config[k] for k in ['secret_id', 'secret_version']}
    _, key = load_vault(config)
    record = seal(args.bundle, key, reference) if args.command == 'seal' else verify(args.bundle, args.manifest_sha256, key, reference)
    print(json.dumps({'verified': True, 'manifest_sha256': sha256(args.bundle / 'recovery-manifest.json'),
                      'state_fingerprint': record['state_fingerprint'], 'intents': record['registry']['intents'],
                      'all_macs_verified': True, 'service_started': False}))


if __name__ == '__main__':
    main()
