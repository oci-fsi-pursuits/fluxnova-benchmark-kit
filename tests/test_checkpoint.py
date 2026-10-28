import sqlite3
from contextlib import closing
import tempfile
import unittest
import json
from pathlib import Path
from benchlib.checkpoint import digest, identifier, sqlite_copy, sqlite_fingerprint, restore_pair, restore_listing, RestoreFailure
from benchlib.util import sha256
from scripts.state_checkpoint import under, load_checkpoint


class CheckpointTests(unittest.TestCase):
    def test_restore_preserves_schema_controls_but_restores_tables_and_their_grants(self):
        listing='\n'.join(['6; 2615 16403 SCHEMA - fluxnova admin',
          '9; 0 0 ACL - SCHEMA fluxnova admin',
          '10; 0 0 DEFAULT ACL fluxnova DEFAULT PRIVILEGES FOR TABLES admin',
          '11; 1259 100 TABLE fluxnova accounts app',
          '12; 0 0 TABLE DATA fluxnova accounts app',
          '13; 0 0 ACL fluxnova TABLE accounts app',
          '14; 1259 101 TABLE fluxnova schema app'])
        active=[line for line in restore_listing(listing,'fluxnova').splitlines() if not line.startswith(';')]
        self.assertEqual(active,listing.splitlines()[3:])

    def test_backup_includes_committed_wal_and_restores_exact_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            live, saved = Path(temp)/'live.sqlite', Path(temp)/'saved.sqlite'
            db=sqlite3.connect(live)
            db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA wal_autocheckpoint=0')
            db.execute('CREATE TABLE ledger (id TEXT PRIMARY KEY, value BLOB)')
            db.execute('INSERT INTO ledger VALUES (?,?)', ('one', b'\x00\xff'));db.commit()
            expected=sqlite_copy(live,saved)
            db.execute('INSERT INTO ledger VALUES (?,?)', ('two', b'later'));db.commit();db.close()
            self.assertNotEqual(sqlite_fingerprint(live), expected)
            self.assertEqual(sqlite_copy(saved,live,replace=True), expected)

    def test_existing_checkpoint_and_self_copy_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            live,saved=Path(temp)/'live.sqlite',Path(temp)/'saved.sqlite'
            with closing(sqlite3.connect(live)) as db:db.execute('CREATE TABLE data (id INTEGER)')
            sqlite_copy(live,saved)
            with self.assertRaises(FileExistsError):sqlite_copy(live,saved)
            with self.assertRaises(ValueError):sqlite_copy(live,live,replace=True)

    def test_logical_fingerprint_ignores_insertion_order_but_detects_schema_change(self):
        with tempfile.TemporaryDirectory() as temp:
            first,second=Path(temp)/'a.sqlite',Path(temp)/'b.sqlite'
            for path,rows in ((first,[(1,'a'),(2,'b')]),(second,[(2,'b'),(1,'a')])):
                with closing(sqlite3.connect(path)) as db:
                    db.execute('CREATE TABLE data (id INTEGER, value TEXT)')
                    db.executemany('INSERT INTO data VALUES (?,?)', rows);db.commit()
            self.assertEqual(sqlite_fingerprint(first),sqlite_fingerprint(second))
            with closing(sqlite3.connect(second)) as db:db.execute('CREATE INDEX data_id ON data(id)')
            self.assertNotEqual(sqlite_fingerprint(first),sqlite_fingerprint(second))

    def test_shared_and_unsafe_schema_names_are_rejected(self):
        for value in ('public','pg_catalog','information_schema','fluxnova; DROP SCHEMA public','a.b',''):
            with self.assertRaises(ValueError):identifier(value)
        self.assertEqual(identifier('fluxnova'),'fluxnova')

    def test_second_store_failure_restores_and_verifies_both_previous_states(self):
        calls=[]
        def engine(x):calls.append(('engine',x))
        def ledger(x):
            calls.append(('ledger',x))
            if x=='candidate':raise RuntimeError('injected failure')
        def verify(x):
            calls.append(('verify',x))
            if calls.count(('verify',x))==1:raise RuntimeError('engine changed before ledger failed')
        with self.assertRaises(RestoreFailure) as raised:restore_pair(engine,ledger,verify,'candidate','previous')
        self.assertTrue(raised.exception.recovered)
        self.assertEqual(calls,[('engine','candidate'),('ledger','candidate'),('verify','previous'),('engine','previous'),('ledger','previous'),('verify','previous')])

    def test_unverified_rollback_must_not_resume_writers(self):
        def failure(_):raise RuntimeError('unavailable')
        with self.assertRaises(RestoreFailure) as raised:restore_pair(failure,lambda _:None,failure,'a','b')
        self.assertFalse(raised.exception.recovered)

    def test_transactional_failure_resumes_only_after_unchanged_pair_is_verified(self):
        calls=[]
        def engine(value):
            calls.append(('engine',value));raise RuntimeError('transaction rolled back')
        def verify(value):calls.append(('verify',value))
        with self.assertRaises(RestoreFailure) as raised:
            restore_pair(engine,lambda _:self.fail('Ledger must remain untouched'),verify,'candidate','previous')
        self.assertTrue(raised.exception.recovered)
        self.assertEqual(calls,[('engine','candidate'),('verify','previous')])

    def test_checkpoint_and_receipt_paths_cannot_escape_designated_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.assertEqual(under(root/'child',root),root.resolve()/'child')
            for value in (root,root/'..'/'outside'):
                with self.assertRaises(ValueError):under(value,root)

    def test_corrupt_archive_and_logical_manifest_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ('engine.dump','ledger.sqlite'):(root/name).write_bytes(b'original')
            manifest={'format':'fluxnova-pair-v1','files':{name:sha256(root/name) for name in ('engine.dump','ledger.sqlite')},
                      'logical':{'engine':'engine','ledger':'ledger'},'state_fingerprint':digest({'engine':'engine','ledger':'ledger'})}
            (root/'manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(load_checkpoint(root),manifest)
            (root/'engine.dump').write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError,'checksum'):load_checkpoint(root)
            (root/'engine.dump').write_bytes(b'original')
            manifest['logical']['ledger']='changed'
            (root/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'logical identity'):load_checkpoint(root)


if __name__=='__main__':unittest.main()
