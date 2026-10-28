"""Stopped-owner, additive signing-key rotation with a retained registry backup."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
from contextlib import closing

from services.bound_payment import IntentRegistry
from services.recoverable_agent import atomic_json, owner
from services.signing_keys import (SigningKeys, authenticate_registered,
                                   authority_fingerprint, canonical)


def logical_rows(db):
    return {table:db.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall()
            for table in ('intents', 'issuance_requests')}


def rotate_keys(directory, current, target, apply=False):
    """No service start or Vault write. A concurrent service owner blocks rotation.

    Verification material is retained. Referenced key retirement and compromise
    revocation need a separate lifecycle and are deliberately unavailable here.
    """
    directory = Path(directory).resolve(strict=True)
    path = directory/'intents.sqlite'
    if path.is_symlink() or not path.is_file():raise ValueError('rotation_requires_existing_registry')
    if type(apply) is not bool or not isinstance(target, SigningKeys):raise ValueError('invalid_rotation_target')
    if isinstance(current, SigningKeys):
        if target.legacy != current.legacy or any(target._keys.get(k) != v for k,v in current._keys.items()):
            raise ValueError('rotation_requires_retained_verification_keys')
        if target.revoked != current.revoked or dict(target.retired) != dict(current.retired):
            raise ValueError('rotation_must_preserve_lifecycle_policy')
        old_key = current.issuing_key()
    else:
        authority_fingerprint(current)
        if target.legacy is None or target._keys[target.legacy] != current:
            raise ValueError('rotation_requires_matching_legacy_key')
        old_key = current
        if target.revoked or target.retired:raise ValueError('rotation_must_preserve_lifecycle_policy')
    if target.issuing_key() == old_key or target.issuing_key() in (
            current._keys.values() if isinstance(current, SigningKeys) else (current,)):
        raise ValueError('rotation_requires_new_issuing_material')
    return _migrate_policy(directory, current, target, operation='rotation', apply=apply)


def _migrate_policy(directory, current, target, *, operation, apply=False, validate_rows=lambda rows: None):
    """Shared stopped-owner backup/commit path; callers validate transitions."""
    directory = Path(directory).resolve(strict=True)
    path = directory/'intents.sqlite'
    if path.is_symlink() or not path.is_file():raise ValueError('rotation_requires_existing_registry')
    if type(apply) is not bool or not isinstance(target, SigningKeys):raise ValueError('invalid_rotation_target')
    scope = {'rotation':'Scheduled issuance-key rotation with retained verification keys; no compromise revocation',
             'revocation':'Revoked keys cannot authorize new sends; exact registered receipts may reconcile; verification material retained',
             'retirement':'Remove only revoked keys absent from all current registry records; retain historical Vault versions/backups; no secure-erasure claim'}[operation]
    with owner(directory):
        registry = IntentRegistry(path, current)
        with registry.connect() as db:
            db.execute('BEGIN IMMEDIATE');registry.require_key_policy(db)
            before = logical_rows(db)
            validate_rows(before)
            for body,token in db.execute('SELECT body,token FROM intents'):
                authenticate_registered(body,token,current);authenticate_registered(body,token,target)
            if db.execute('SELECT count(*) FROM issuance_requests r LEFT JOIN intents i ON i.key=r.key WHERE i.key IS NULL').fetchone()[0]:
                raise ValueError('rotation_registry_orphan_issuance')
        nonce = uuid.uuid4().hex
        receipt = {'format':'fluxnova-key-'+operation+'-v1',operation+'_id':nonce,'applied':False,
                   'previous_policy':registry.key_fingerprint,'next_policy':authority_fingerprint(target),
                   'previous_generation':int(registry.key_generation),'next_generation':int(registry.key_generation)+1,
                   'active_signing_key_id':target.active,'verification_key_ids':sorted(target._keys),
                   'revoked_signing_key_ids':sorted(target.revoked),'retired_signing_key_ids':sorted(target.retired),
                   'intents_preserved':len(before['intents']),'issuance_requests_preserved':len(before['issuance_requests']),
                   'logical_rows_sha256':hashlib.sha256(canonical(before)).hexdigest(),
                   'scope':scope}
        if not apply:return receipt
        backup = directory/('intents.before-key-'+operation+'-'+nonce+'.sqlite')
        with backup.open('xb') as stream:os.chmod(stream.name,0o600)
        try:
            with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(backup)) as destination:
                source.backup(destination)
            with backup.open('r+b') as stream:os.fsync(stream.fileno())
            with registry.connect() as db:
                db.execute('BEGIN IMMEDIATE');registry.require_key_policy(db)
                if logical_rows(db) != before:raise ValueError('rotation_registry_changed_during_backup')
                validate_rows(before)
                for body,token in db.execute('SELECT body,token FROM intents'):
                    authenticate_registered(body,token,current);authenticate_registered(body,token,target)
                db.execute("UPDATE registry_meta SET value=? WHERE name='signing_key_sha256'",(receipt['next_policy'],))
                db.execute("INSERT OR REPLACE INTO registry_meta(name,value) VALUES('key_generation',?)",(str(receipt['next_generation']),))
                if logical_rows(db) != before:raise ValueError('rotation_changed_intent_rows')
            with path.open('r+b') as stream:os.fsync(stream.fileno())
            if os.name != 'nt':
                fd=os.open(directory,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
            receipt.update(applied=True,backup_file=backup.name,backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest())
            atomic_json(directory/('key-'+operation+'-'+nonce+'.json'),receipt)
            return receipt
        except BaseException:
            # Never roll back an ambiguous commit or delete the recovery copy.
            raise
