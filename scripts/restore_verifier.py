#!/usr/bin/env python3
"""Restore a private registry onto a stopped verifier host over existing SSH.

Requires a persistently masked source, an inactive target, staged service code,
reference-only target configuration and the existing service user. The target
reads its own pinned Vault secret to verify every MAC. Does not start a service.
Alternatively, --oci-source-instance-id checks a stopped VM through the OCI API
instead of SSH. That mode requires a backup receipt made with --oci-identity.
This is an operator-coordinated restore, not automatic failover or global fencing.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
from datetime import datetime, timezone


FENCE_PROBE = '''import json,subprocess
from pathlib import Path
r=subprocess.run(['systemctl','show','fluxnova-bound-verifier','--property=ActiveState,MainPID,UnitFileState,LoadState'],capture_output=True,text=True)
properties=dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
pids=[]
for p in Path('/proc').glob('[0-9]*/cmdline'):
 try:
  if b'services.verifier_service' in p.read_bytes():pids.append(int(p.parent.name))
 except (FileNotFoundError,PermissionError,ProcessLookupError):pass
print(json.dumps({'properties':properties,'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'machine_id':Path('/etc/machine-id').read_text().strip(),'verifier_process_ids':pids,'unit_is_persistent_mask':Path('/etc/systemd/system/fluxnova-bound-verifier.service').is_symlink() and Path('/etc/systemd/system/fluxnova-bound-verifier.service').resolve()==Path('/dev/null')}))
'''


def require_stopped(record, source=False):
    properties = record['properties']
    if (properties.get('ActiveState') in ('active', 'activating', 'deactivating', 'reloading')
            or properties.get('MainPID', '0') != '0' or record['verifier_process_ids']):
        raise ValueError('verifier_host_is_not_stopped')
    if source and (properties.get('UnitFileState') != 'masked' or not record['unit_is_persistent_mask']):
        raise ValueError('source_requires_persistent_verifier_mask')


def remote(ssh_config, host, code):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', host):
        raise ValueError('invalid_ssh_host_alias')
    result = subprocess.run(['ssh', '-F', str(ssh_config), host,
                             'sudo /opt/fluxnova-bench/venv/bin/python -'],
                            input=code.replace('\r\n', '\n').encode(), capture_output=True, timeout=75)
    if result.returncode:
        # stderr may contain application data. Keep it out of caller logs.
        raise RuntimeError('remote_verifier_restore_step_failed')
    return json.loads(result.stdout)


def cloud_source_fence(args, reference):
    """Read fresh lifecycle state; a saved STOPPED receipt is never sufficient."""
    import os
    instance_id = args.oci_source_instance_id
    identity = reference.get('oci_source_identity', {})
    if (not re.fullmatch(r'ocid1\.instance\.[A-Za-z0-9._-]+', instance_id)
            or identity.get('instance_id') != instance_id
            or not identity.get('machine_id') or not identity.get('boot_id')
            or not identity.get('region')):
        raise ValueError('cloud_fence_requires_matching_backup_instance_identity')
    import oci
    config = oci.config.from_file(profile_name=args.oci_profile)
    if config['region'] != identity['region']:
        raise ValueError('cloud_fence_region_mismatch')
    token = Path(config['security_token_file']).read_text().strip()
    signer = oci.auth.signers.SecurityTokenSigner(
        token, oci.signer.load_private_key_from_file(os.path.expanduser(config['key_file'])))
    compute = oci.core.ComputeClient(config, signer=signer, timeout=(10, 30),
                                    retry_strategy=oci.retry.NoneRetryStrategy())
    response = compute.get_instance(instance_id, retry_strategy=oci.retry.NoneRetryStrategy())
    instance = response.data
    if instance.id != instance_id or instance.lifecycle_state != 'STOPPED':
        raise ValueError('source_oci_instance_is_not_stopped')
    return {**identity, 'fence_type': 'oci_instance_stopped',
            'lifecycle_state': instance.lifecycle_state,
            'checked_at': datetime.now(timezone.utc).isoformat(),
            'opc_request_id': response.headers.get('opc-request-id')}


def source_fence(args, reference):
    if getattr(args, 'oci_source_instance_id', None):
        return cloud_source_fence(args, reference)
    source = remote(args.ssh_config, args.source_host, FENCE_PROBE)
    require_stopped(source, source=True)
    return source


def restore(args):
    if args.source_host == args.target_host:
        raise ValueError('restore_requires_distinct_host_aliases')
    reference = json.loads(args.backup_receipt.read_text())
    if args.backup.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('registry_backup_exceeds_restore_limit')
    raw = args.backup.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != reference['sha256'] or len(raw) != reference['bytes']:
        raise ValueError('registry_backup_checksum_mismatch')
    source = source_fence(args, reference)
    target = remote(args.ssh_config, args.target_host, FENCE_PROBE)
    require_stopped(target)
    if source['boot_id'] == target['boot_id'] or source['machine_id'] == target['machine_id']:
        raise ValueError('restore_requires_distinct_virtual_machines')
    payload = {'backup_b64': base64.b64encode(raw).decode(), 'sha256': sha, 'bytes': len(raw)}
    code = '''import base64,hashlib,hmac,json,os,pwd,sqlite3,sys,tempfile,uuid
from contextlib import closing
from pathlib import Path
release=Path('/opt/fluxnova-bound-verifier/current').resolve()
assert release.is_relative_to(Path('/opt/fluxnova-bound-verifier/releases')) and release.is_dir()
sys.path.insert(0,str(release))
from services.verifier_service import load_vault
from services.recoverable_agent import owner
try:
 from services.signing_keys import authority_fingerprint,authenticate_registered
except ModuleNotFoundError as missing:
 if missing.name!='services.signing_keys':raise
 # An existing single-key deployment may still run the retained older release.
 # Keep its original verification, with no fallback for keyring material.
 def authority_fingerprint(key):
  if not isinstance(key,bytes) or len(key)!=32:raise ValueError('legacy_release_requires_single_key')
  return hashlib.sha256(key).hexdigest()
 def authenticate_registered(body,token,key):
  authority_fingerprint(key)
  expected=base64.urlsafe_b64encode(body.encode()).decode()+'.'+hmac.new(key,body.encode(),hashlib.sha256).hexdigest()
  if not hmac.compare_digest(token,expected):raise ValueError('registry_mac_mismatch')
config=json.loads(Path('/etc/fluxnova-bound-verifier/config.json').read_text())
state=Path(config['state_directory']).resolve();assert state==Path('/var/lib/fluxnova-bound-verifier')
identity=pwd.getpwnam('fluxnova-verifier')
payload=PAYLOAD
raw=base64.b64decode(payload['backup_b64'],validate=True)
assert len(raw)==payload['bytes'] and hashlib.sha256(raw).hexdigest()==payload['sha256']
values,key=load_vault(config)
assert state.is_dir() and state.stat().st_uid==identity.pw_uid and state.stat().st_mode&0o777==0o700
with owner(state):
 for suffix in ('-wal','-shm','-journal'):assert not (state/('intents.sqlite'+suffix)).exists()
 fd,name=tempfile.mkstemp(prefix='restore-',suffix='.sqlite',dir=state)
 incoming=Path(name)
 try:
  with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
  os.chmod(incoming,0o600);os.chown(incoming,identity.pw_uid,identity.pw_gid)
  with closing(sqlite3.connect(incoming.as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
   assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
   assert db.execute("SELECT value FROM registry_meta WHERE name='signing_key_sha256'").fetchone()[0]==authority_fingerprint(key)
   rows=db.execute('SELECT body,token FROM intents').fetchall()
   issuance=db.execute('SELECT count(*) FROM issuance_requests').fetchone()[0]
   for body,token in rows:
    authenticate_registered(body,token,key)
  target=state/'intents.sqlite';previous=None
  if target.exists():
   previous=state/('intents.before-restore-'+uuid.uuid4().hex+'.sqlite')
   with target.open('rb') as source,previous.open('xb') as destination:
    destination.write(source.read());destination.flush();os.fsync(destination.fileno())
   os.chmod(previous,0o600);os.chown(previous,identity.pw_uid,identity.pw_gid)
  incoming.replace(target)
  fd=os.open(state,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)
  assert hashlib.sha256(target.read_bytes()).hexdigest()==payload['sha256']
  # owner() may be held by root during this stopped-host operation. Keep the
  # lock usable by the service identity after the restore process exits.
  os.chown(state/'owner.lock',identity.pw_uid,identity.pw_gid);os.chmod(state/'owner.lock',0o600)
  print(json.dumps({'sha256':payload['sha256'],'bytes':len(raw),'intents':len(rows),'issuance_requests':issuance,'all_macs_verified':True,'vault_version':config['secret_version'],'state_mode':'0700','file_mode':'0600','service_uid':identity.pw_uid,'previous_registry_retained':previous.name if previous else None,'service_started':False,'signing_key_or_password_values_written':False}))
 finally:
  if incoming.exists():incoming.unlink()
'''.replace('PAYLOAD', repr(payload))
    result = remote(args.ssh_config, args.target_host, code)
    after = source_fence(args, reference)
    target_after = remote(args.ssh_config, args.target_host, FENCE_PROBE)
    require_stopped(target_after)
    return {'source_host': args.source_host, 'target_host': args.target_host,
            'source_fence_before': source, 'source_fence_after': after,
            'target_stopped_before': target, 'target_stopped_after': target_after, 'restore': result}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--backup-receipt', type=Path, required=True)
    parser.add_argument('--ssh-config', type=Path, required=True)
    parser.add_argument('--source-host', required=True)
    parser.add_argument('--target-host', required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--oci-source-instance-id', help='Require fresh OCI STOPPED state instead of contacting source SSH')
    parser.add_argument('--oci-profile', default='BENCHMARK', help='Local OCI session-token profile; used only for the cloud fence')
    args = parser.parse_args()
    if args.receipt.exists():
        parser.error('receipt already exists; retain it and choose a new path')
    result = restore(args)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    with args.receipt.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'restored': True, 'source_host': args.source_host, 'target_host': args.target_host,
                      'intents': result['restore']['intents'], 'macs_verified': True, 'service_started': False}))
