#!/usr/bin/env python3
"""Service bounded reset requests using the controller's existing private SSH.

The load generator supplies only a run ID and pinned checkpoint identities.
All restore paths, credentials and commands come from controller arguments.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import re
import subprocess
import sys
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.state_checkpoint import Cell,under
from benchlib.remote import WORKSPACE,PYTHON
from benchlib.util import ROOT,sha256,utc_now,write_json,safe_run_id
from benchlib.reset_mailbox import validate_receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('config','environment','deployment','snapshot','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--postgres-image',required=True)
    parser.add_argument('--schema',default='fluxnova')
    parser.add_argument('--duration-s',type=int,default=1800)
    parser.add_argument('--max-resets',type=int,default=2)
    args=parser.parse_args()
    if os.name!='posix':raise ValueError('Run the reset broker on the Linux controller')
    if not 60<=args.duration_s<=21600 or not 1<=args.max_resets<=20:raise ValueError('Broker bounds exceeded')
    os.umask(0o077)
    def interrupted(signum,frame):raise KeyboardInterrupt('Broker termination requested')
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGHUP,interrupted)
    inventory=json.loads(args.deployment.read_text())
    cell=Cell(json.loads(args.config.read_text()),json.loads(args.environment.read_text()),inventory,args.postgres_image,args.schema)
    snapshot=under(args.snapshot,ROOT/'dist/checkpoints');manifest=cell.validate(snapshot)
    binding={'dataset_sha256':sha256(snapshot/'manifest.json'),'state_fingerprint':manifest['state_fingerprint']}
    remote_root=Path(inventory['remote_root'])
    if not remote_root.is_absolute() or remote_root.parent!=Path(WORKSPACE) or inventory['hosts']['loadgen']['directory']!=str(remote_root):
        raise ValueError('Unexpected deployed load-generator release directory')
    output=under(args.output,ROOT/'dist')
    if output.exists():raise ValueError('Broker output already exists')
    identity=uuid.uuid4().hex
    mailbox=remote_root/'dist/reset-mailboxes'/identity
    operations=ROOT/'dist/checkpoints/brokers'/identity;operations.mkdir(mode=0o700,parents=True)
    def remote(command,payload=None):
        argv=[PYTHON,str(remote_root/'scripts/reset_mailbox.py'),command,'--mailbox',str(mailbox)]
        return json.loads(cell.ssh('loadgen',argv,data=json.dumps(payload).encode() if payload is not None else None,timeout=45))
    meta=remote('create',{**binding,'duration_s':args.duration_s,'max_resets':args.max_resets})
    result={'status':'ready','started_at':utc_now(),'mailbox':str(mailbox),'session':meta,'operations':[]}
    output.parent.mkdir(parents=True,exist_ok=True);write_json(output,result)
    print(json.dumps({'status':'ready','mailbox':str(mailbox),**binding}),flush=True)
    try:
        while len(result['operations'])<args.max_resets and time.time()<meta['expires_at']:
            requests=remote('pending')
            if not requests:time.sleep(2);continue
            request=requests[0]
            # Recheck bindings on the controller; no requester-provided argv/path.
            safe_run_id(request['run_id'])
            if not re.fullmatch('[a-f0-9]{32}',request['request_id']) or request['session_id']!=meta['session_id']:
                raise ValueError('Unexpected request/session identity')
            if not request['created_at']<=time.time()<request['deadline']<=meta['expires_at']:
                raise ValueError('Request outside its permitted time window')
            for key in binding:
                if request[key]!=binding[key]:raise ValueError('Request checkpoint identity mismatch')
            remote('claim',request)
            receipt_path=operations/(request['request_id']+'.json')
            log=operations/(request['request_id']+'.log')
            argv=[sys.executable,str(ROOT/'scripts/state_checkpoint.py'),'restore',
                  '--config',str(args.config.resolve()),'--environment',str(args.environment.resolve()),
                  '--deployment',str(args.deployment.resolve()),'--snapshot',str(snapshot),
                  '--postgres-image',args.postgres_image,'--schema',args.schema,
                  '--run-id',request['run_id'],'--output',str(receipt_path)]
            with log.open('x') as stream:
                child=subprocess.Popen(argv,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT)
                try:returncode=child.wait()
                except BaseException:
                    # Let state_checkpoint perform rollback/restart cleanup.
                    child.terminate();child.wait();raise
            raw=json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
            receipt={key:raw[key] for key in ('run_id','success','dataset_sha256','state_fingerprint','method',
                       'writers_resumed','started_at','completed_at','error_type','rollback_verified') if key in raw}
            if returncode:
                receipt.update(success=False,run_id=request['run_id'],error_type=raw.get('error_type','RestoreProcessFailure'))
            else:
                validate_receipt(receipt,request['run_id'],binding)
                if receipt.get('writers_resumed') is not True:raise ValueError('Restore did not resume writers')
            remote('deliver',{'request':request,'receipt':receipt})
            result['operations'].append({'request_id':request['request_id'],'receipt':receipt,'returncode':returncode})
            write_json(output,result)
            if returncode:raise RuntimeError('Reset failed; broker stopped before accepting more work')
        result['status']='completed' if len(result['operations'])==args.max_resets else 'expired'
    except BaseException as exc:
        result.update(status='error',error_type=type(exc).__name__);raise
    finally:
        try:remote('close')
        finally:
            result['completed_at']=utc_now();write_json(output,result)
    print(json.dumps({'status':result['status'],'resets':len(result['operations'])}),flush=True)


if __name__=='__main__':main()
