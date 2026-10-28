#!/usr/bin/env python3
"""Local reset hook and controller-only queue operations; no listening service."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchlib.reset_mailbox import Mailbox
from benchlib.util import ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('create','pending','claim','deliver','close','request'))
    parser.add_argument('--mailbox',type=Path,required=True)
    parser.add_argument('--run-id')
    parser.add_argument('--timeout-s',type=int,default=600)
    parser.add_argument('--dataset-sha256')
    parser.add_argument('--state-fingerprint')
    args=parser.parse_args();os.umask(0o077)
    parent=(ROOT/'dist/reset-mailboxes').resolve();path=args.mailbox.resolve()
    if not path.is_relative_to(parent) or path==parent:raise ValueError('Mailbox must be under this release dist/reset-mailboxes')
    if args.command=='create':
        spec=json.load(sys.stdin)
        value=Mailbox.create(path,spec,spec['duration_s'],spec['max_resets']).meta
    else:
        box=Mailbox(path)
        if args.command=='pending':value=box.pending()
        elif args.command=='claim':box.claim(json.load(sys.stdin));value={'claimed':True}
        elif args.command=='deliver':
            value=json.load(sys.stdin);box.deliver(value['request'],value['receipt']);value={'delivered':True}
        elif args.command=='close':box.close('controller stopped');value={'closed':True}
        else:
            value=box.wait(args.run_id,args.timeout_s,{'dataset_sha256':args.dataset_sha256,'state_fingerprint':args.state_fingerprint})
    print(json.dumps(value))


if __name__=='__main__':main()
