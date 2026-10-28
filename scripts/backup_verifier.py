#!/usr/bin/env python3
"""Copy a consistent verifier-registry backup over existing private SSH access.

The registry contains synthetic payment metadata/MACs, not the Vault signing key.
Writes a new private output file and checksum receipt; never prints backup data.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-config',type=Path,required=True);parser.add_argument('--ssh-host',default='fluxnova-support')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--oci-identity',action='store_true',help='Include live OCI instance metadata for a later STOPPED-host restore')
    args=parser.parse_args()
    if args.output.exists():parser.error('backup output already exists')
    code='''import base64,hashlib,json,os,sqlite3,tempfile,urllib.request
from contextlib import closing
from pathlib import Path
state=Path('/var/lib/fluxnova-bound-verifier').resolve()
fd,name=tempfile.mkstemp(prefix='backup-',suffix='.sqlite',dir=state);os.close(fd)
target=Path(name)
assert target.resolve().parent==state
try:
 with closing(sqlite3.connect(state/'intents.sqlite')) as source,closing(sqlite3.connect(target)) as destination:
  source.backup(destination)
  rows=destination.execute('SELECT count(*) FROM intents').fetchone()[0]
 raw=target.read_bytes()
 identity={}
 if INCLUDE_OCI_IDENTITY:
  request=urllib.request.Request('http://169.254.169.254/opc/v2/instance/',headers={'Authorization':'Bearer Oracle'})
  with urllib.request.urlopen(request,timeout=5) as response:metadata=json.load(response)
  identity={'instance_id':metadata['id'],'region':metadata['canonicalRegionName'],'machine_id':Path('/etc/machine-id').read_text().strip(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
 print(json.dumps({'backup_b64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest(),'intent_rows':rows,'oci_source_identity':identity}))
finally:target.unlink()
'''.replace('INCLUDE_OCI_IDENTITY',repr(args.oci_identity))
    result=subprocess.run(['ssh','-F',str(args.ssh_config),args.ssh_host,'sudo python3 -'],input=code.encode(),capture_output=True,timeout=45)
    if result.returncode:raise SystemExit('Remote registry backup failed; no output file written.')
    record=json.loads(result.stdout);raw=base64.b64decode(record.pop('backup_b64'),validate=True)
    if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise SystemExit('Backup checksum mismatch; no output file written.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('xb') as stream:stream.write(raw)
    args.output.chmod(0o600)
    receipt={**record,'bytes':len(raw),'file':args.output.name,'signing_key_included':False}
    args.output.with_suffix(args.output.suffix+'.receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))


if __name__=='__main__':main()
