#!/usr/bin/env python3
"""Validate or apply an offline issuance-key rotation using pinned Vault versions.

Runs on the verifier host as its service user while the service is stopped.
Never updates Vault, starts a worker, changes configuration or prints secrets.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services.key_rotation import rotate_keys
from services.verifier_service import load_vault


def validate_configs(current, target):
    required={'region','secret_id','secret_version','engine_url','support_url','state_directory','port'}
    if set(current)!=required or set(target)!=required:raise ValueError('invalid_rotation_configuration')
    if (any(current[k]!=target[k] for k in required-{'secret_version'})
            or type(current['secret_version']) is not int or current['secret_version']<1
            or type(target['secret_version']) is not int or target['secret_version']<=current['secret_version']):
        raise ValueError('rotation_requires_newer_version_of_same_authority')


def run(current, target, apply=False, loader=load_vault):
    validate_configs(current,target)
    old_values,old_key=loader(current);new_values,new_key=loader(target)
    if any(old_values[k]!=new_values[k] for k in ['management_token','engine_username','engine_password','support_token']):
        raise ValueError('rotation_must_preserve_service_credentials')
    result=rotate_keys(current['state_directory'],old_key,new_key,apply=apply)
    result['previous_vault_version']=current['secret_version'];result['next_vault_version']=target['secret_version']
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current-config',type=Path,required=True)
    parser.add_argument('--next-config',type=Path,required=True)
    parser.add_argument('--apply',action='store_true',help='Commit only after successful stopped-owner validation; otherwise dry run')
    args=parser.parse_args()
    try:
        result=run(json.loads(args.current_config.read_text()),json.loads(args.next_config.read_text()),args.apply)
        print(json.dumps(result))
    except Exception as error:
        print(json.dumps({'rotation_failed':True,'error_type':type(error).__name__}))
        raise SystemExit(1)


if __name__=='__main__':main()
