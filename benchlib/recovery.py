"""Bind a quiesced engine/ledger checkpoint to its verifier registry.

These checks validate retained artifacts, not quiescence or infrastructure HA.
The caller must fence writers throughout capture and verify restored SQL state.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3

from benchlib.checkpoint import digest, sqlite_fingerprint
from benchlib.util import sha256, utc_now
from services.signing_keys import SigningKeys, authority_fingerprint, authenticate_registered

FILES = {'engine.dump', 'ledger.sqlite', 'registry.sqlite', 'manifest.json'}


def registry_check(path, key):
    if not isinstance(key, SigningKeys) and (not isinstance(key, bytes) or len(key) != 32):
        raise ValueError('invalid_recovery_key')
    logical = sqlite_fingerprint(path)
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        stored = db.execute("SELECT value FROM registry_meta WHERE name='signing_key_sha256'").fetchone()
        fingerprint = authority_fingerprint(key)
        if stored != (fingerprint,):
            raise ValueError('recovery_registry_key_mismatch')
        rows = db.execute('SELECT key,body,token FROM intents').fetchall()
        for intent_key, body, token in rows:
            try:
                authenticate_registered(body, token, key)
                if json.loads(body)['key'] != intent_key:raise ValueError('key_mismatch')
            except ValueError:raise ValueError('recovery_registry_mac_mismatch') from None
        if db.execute('SELECT count(*) FROM issuance_requests r LEFT JOIN intents i ON i.key=r.key WHERE i.key IS NULL').fetchone()[0]:
            raise ValueError('recovery_registry_orphan_issuance')
        issuance = db.execute('SELECT count(*) FROM issuance_requests').fetchone()[0]
    return {'logical': logical, 'intents': len(rows), 'issuance_requests': issuance,
            'signing_key_sha256': fingerprint, 'all_macs_verified': True}


def files_checked(directory):
    directory = Path(directory).resolve(strict=True)
    for name in FILES:
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != directory:
            raise ValueError('recovery_bundle_requires_local_regular_files')
    pair = json.loads((directory / 'manifest.json').read_text())
    if pair.get('format') != 'fluxnova-pair-v1' or set(pair['files']) != {'engine.dump', 'ledger.sqlite'}:
        raise ValueError('invalid_recovery_pair_manifest')
    if digest(pair['logical']) != pair['state_fingerprint']:
        raise ValueError('recovery_pair_logical_identity_mismatch')
    checksums = {name: sha256(directory / name) for name in FILES}
    if any(checksums[name] != value for name, value in pair['files'].items()):
        raise ValueError('recovery_pair_checksum_mismatch')
    if sqlite_fingerprint(directory / 'ledger.sqlite') != pair['logical']['ledger']:
        raise ValueError('recovery_ledger_logical_mismatch')
    return directory, pair, checksums


def seal(directory, key, vault_reference):
    directory, pair, checksums = files_checked(directory)
    if (set(vault_reference) != {'secret_id', 'secret_version'}
            or not str(vault_reference['secret_id']).startswith('ocid1.vaultsecret.')
            or type(vault_reference['secret_version']) is not int or vault_reference['secret_version'] < 1):
        raise ValueError('invalid_recovery_vault_reference')
    registry = registry_check(directory / 'registry.sqlite', key)
    logical = {**pair['logical'], 'registry': registry['logical']}
    record = {'format': 'fluxnova-recovery-v1', 'created_at': utc_now(), 'files': checksums,
              'logical': logical, 'state_fingerprint': digest(logical),
              'vault_reference': dict(vault_reference), 'registry': registry,
              'scope': 'Quiesced schema, simulator ledger and verifier registry; not global database/IAM state'}
    with (directory / 'recovery-manifest.json').open('x', encoding='utf-8') as stream:
        os.chmod(stream.name, 0o600)
        stream.write(json.dumps(record, indent=2) + '\n');stream.flush();os.fsync(stream.fileno())
    return record


def verify(directory, expected_manifest_sha256, key, vault_reference):
    manifest = Path(directory) / 'recovery-manifest.json'
    if (not re.fullmatch('[0-9a-f]{64}', expected_manifest_sha256)
            or manifest.is_symlink() or sha256(manifest) != expected_manifest_sha256):
        raise ValueError('recovery_manifest_checksum_mismatch')
    record = json.loads(manifest.read_text())
    directory, pair, checksums = files_checked(directory)
    if record.get('format') != 'fluxnova-recovery-v1' or record['files'] != checksums:
        raise ValueError('recovery_bundle_checksum_mismatch')
    if record['vault_reference'] != vault_reference:
        raise ValueError('recovery_vault_reference_mismatch')
    registry = registry_check(directory / 'registry.sqlite', key)
    logical = {**pair['logical'], 'registry': registry['logical']}
    if registry != record['registry'] or logical != record['logical'] or digest(logical) != record['state_fingerprint']:
        raise ValueError('recovery_bundle_logical_mismatch')
    return record
