#!/usr/bin/env python3
"""Dry-run/apply stopped-owner revocation or unused-key retirement.

Read pinned Vault versions on the verifier host. Never writes Vault, changes
service configuration, starts a worker, or destroys a historical key version.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.rotate_verifier_keys import validate_configs
from services.key_lifecycle import revoke_keys, retire_keys
from services.verifier_service import load_vault


def run(current, target, operation, apply=False, loader=load_vault):
    validate_configs(current, target)
    if operation not in ('revoke', 'retire'):raise ValueError('invalid_key_policy_operation')
    old_values, old_key = loader(current);new_values, new_key = loader(target)
    if any(old_values[k] != new_values[k] for k in
           ['management_token','engine_username','engine_password','support_token']):
        raise ValueError('key_policy_must_preserve_service_credentials')
    action = revoke_keys if operation == 'revoke' else retire_keys
    result = action(current['state_directory'], old_key, new_key, apply=apply)
    result.update(previous_vault_version=current['secret_version'], next_vault_version=target['secret_version'])
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current-config',type=Path,required=True)
    parser.add_argument('--next-config',type=Path,required=True)
    parser.add_argument('--operation',choices=['revoke','retire'],required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:
        print(json.dumps(run(json.loads(args.current_config.read_text()),
                             json.loads(args.next_config.read_text()), args.operation, args.apply)))
    except Exception as error:
        print(json.dumps({'key_policy_change_failed':True,'error_type':type(error).__name__}))
        raise SystemExit(1)


if __name__=='__main__':main()
