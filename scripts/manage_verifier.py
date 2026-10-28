#!/usr/bin/env python3
"""Local operator CLI for the loopback-only persistent intent authority.

Fetches the management token from the pinned Vault version into memory. Does
not print the Vault bundle, signing key, engine password or simulator token.
"""
import argparse
import base64
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from services.live_recoverable_agent import identity
from services.recoverable_agent import atomic_json
from services.worker import Client


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--profile',default='BENCHMARK')
    parser.add_argument('--url',default='http://127.0.0.1:19082')
    commands=parser.add_subparsers(dest='action',required=True)
    commands.add_parser('health')
    issue=commands.add_parser('issue');issue.add_argument('--request-id',required=True);issue.add_argument('--payment',type=Path,required=True)
    issue.add_argument('--reviewer',required=True);issue.add_argument('--ttl-s',type=int,default=3600);issue.add_argument('--output',type=Path,required=True)
    bind=commands.add_parser('bind');bind.add_argument('--key',required=True);bind.add_argument('--process-id',required=True)
    bind.add_argument('--screening-id',required=True);bind.add_argument('--screening-key',required=True)
    status=commands.add_parser('status');status.add_argument('--key',required=True)
    args=parser.parse_args()
    parsed=urlsplit(args.url)
    if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost') or parsed.username or parsed.query or parsed.fragment:
        parser.error('url must be a local HTTP SSH forward without credentials/query/fragment')
    config=json.loads(args.config.read_text())
    if args.action=='health':print(json.dumps(Client(args.url).request('GET','/health')));return
    import oci
    cfg,signer,_=identity(args.profile)
    client=oci.secrets.SecretsClient(cfg,signer=signer,timeout=(10,30),retry_strategy=oci.retry.NoneRetryStrategy())
    bundle=client.get_secret_bundle(config['secret_id'],version_number=config['secret_version'],retry_strategy=oci.retry.NoneRetryStrategy()).data
    values=json.loads(base64.b64decode(bundle.secret_bundle_content.content))
    authority=Client(args.url,'Bearer '+values['management_token'])
    if args.action=='issue':
        if args.output.exists():parser.error('output already exists; inspect it instead of replacing its identity')
        intent=authority.request('POST','/intents',{'request_id':args.request_id,'payment':json.loads(args.payment.read_text()),'reviewer':args.reviewer,'ttl_s':args.ttl_s})
        args.output.parent.mkdir(parents=True,exist_ok=True);atomic_json(args.output,intent)
        print(json.dumps({'key':intent['key'],'output':str(args.output)}))
    elif args.action=='bind':
        print(json.dumps(authority.request('POST','/intents/'+args.key+'/bind',{'process_id':args.process_id,'screening_id':args.screening_id,'screening_key':args.screening_key})))
    else:print(json.dumps(authority.request('GET','/intents/'+args.key)))


if __name__=='__main__':main()
