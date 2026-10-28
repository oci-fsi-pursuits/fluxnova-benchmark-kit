from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from benchlib.checkpoint import digest, sqlite_fingerprint
from benchlib.recovery import seal, verify
from benchlib.util import sha256
from services.bound_payment import IntentRegistry
from services.key_rotation import rotate_keys
from services.signing_keys import SigningKeys


class RecoveryBundleTests(unittest.TestCase):
    key = b'r' * 32
    reference = {'secret_id': 'ocid1.vaultsecret.oc1.test', 'secret_version': 1}

    def fixture(self, directory):
        root = Path(directory)
        (root / 'engine.dump').write_bytes(b'private engine fixture')
        with closing(sqlite3.connect(root / 'ledger.sqlite')) as db:
            db.execute('CREATE TABLE receipts(key TEXT PRIMARY KEY, result TEXT)')
        registry = IntentRegistry(root / 'registry.sqlite', self.key)
        registry.issue({'amount_minor': 2000, 'limit_minor': 1000, 'authorized': True,
                        'sanctions_match': False}, 'reviewer', 60, request_id='request')
        logical = {'engine': {'fixture': True}, 'ledger': sqlite_fingerprint(root / 'ledger.sqlite')}
        (root / 'manifest.json').write_text(json.dumps({'format': 'fluxnova-pair-v1',
            'files': {name: sha256(root / name) for name in ['engine.dump', 'ledger.sqlite']},
            'logical': logical, 'state_fingerprint': digest(logical)}))
        return root

    def test_complete_bundle_verifies_and_cannot_overwrite_its_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            record = seal(root, self.key, self.reference)
            self.assertEqual(verify(root, sha256(root / 'recovery-manifest.json'), self.key, self.reference), record)
            self.assertEqual(record['registry']['intents'], 1)
            self.assertEqual(record['registry']['issuance_requests'], 1)
            self.assertNotIn(self.key.decode(), (root / 'recovery-manifest.json').read_text())
            with self.assertRaises(FileExistsError):
                seal(root, self.key, self.reference)

    def test_rotated_registry_bundle_retains_mixed_token_versions_and_pinned_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory)
            # Rotation requires the standard state filename. Keep the sealed
            # bundle registry as a consistent backup after the transition.
            (root/'registry.sqlite').rename(root/'intents.sqlite')
            ring=SigningKeys({'original':self.key,'next':b'n'*32},'next','original')
            rotate_keys(root,self.key,ring,apply=True)
            registry=IntentRegistry(root/'intents.sqlite',ring)
            registry.issue({'amount_minor':2000,'limit_minor':1000,'authorized':True,
                            'sanctions_match':False},'reviewer',60,request_id='after-rotation')
            (root/'intents.sqlite').rename(root/'registry.sqlite')
            reference={**self.reference,'secret_version':2}
            record=seal(root,ring,reference)
            self.assertEqual(record['registry']['intents'],2)
            self.assertEqual(verify(root,sha256(root/'recovery-manifest.json'),ring,reference),record)
            with self.assertRaisesRegex(ValueError,'key_mismatch'):
                seal(root,self.key,reference)

    def test_each_store_swap_or_corruption_is_rejected(self):
        for name in ['engine.dump', 'ledger.sqlite', 'registry.sqlite', 'manifest.json']:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = self.fixture(directory);seal(root, self.key, self.reference)
                expected = sha256(root / 'recovery-manifest.json')
                (root / name).write_bytes(b'a different snapshot')
                with self.assertRaises((ValueError, sqlite3.DatabaseError)):
                    verify(root, expected, self.key, self.reference)

    def test_manifest_relabel_and_wrong_vault_version_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory);seal(root, self.key, self.reference)
            expected = sha256(root / 'recovery-manifest.json')
            with self.assertRaisesRegex(ValueError, 'vault_reference'):
                verify(root, expected, self.key, {**self.reference, 'secret_version': 2})
            path = root / 'recovery-manifest.json';record = json.loads(path.read_text())
            record['state_fingerprint'] = '0' * 64;path.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, 'manifest_checksum'):
                verify(root, expected, self.key, self.reference)

    def test_wrong_key_bad_mac_and_orphan_issuance_reject_seal(self):
        for change in ['wrong_key', 'bad_mac', 'orphan']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = self.fixture(directory)
                with closing(sqlite3.connect(root / 'registry.sqlite')) as db:
                    if change == 'bad_mac':db.execute("UPDATE intents SET token='changed'")
                    if change == 'orphan':db.execute("UPDATE issuance_requests SET key='missing'")
                    db.commit()
                with self.assertRaisesRegex(ValueError, 'key_mismatch|mac_mismatch|orphan'):
                    seal(root, b'x' * 32 if change == 'wrong_key' else self.key, self.reference)
                self.assertFalse((root / 'recovery-manifest.json').exists())

    def test_ledger_logical_identity_must_match_even_if_file_hash_is_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            with closing(sqlite3.connect(root / 'ledger.sqlite')) as db:
                db.execute("INSERT INTO receipts VALUES('new','later')");db.commit()
            path = root / 'manifest.json';pair = json.loads(path.read_text())
            pair['files']['ledger.sqlite'] = sha256(root / 'ledger.sqlite');path.write_text(json.dumps(pair))
            with self.assertRaisesRegex(ValueError, 'ledger_logical'):
                seal(root, self.key, self.reference)
