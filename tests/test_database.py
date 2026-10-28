import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from benchlib.database import target_fingerprint, transaction_probe


class Cursor:
    def __init__(self, raw, fail_drop=False):
        self.raw, self.fail_drop = raw, fail_drop

    def execute(self, sql):
        if self.fail_drop and sql.startswith("DROP TABLE"):
            raise RuntimeError("cleanup unavailable")
        return self.raw.execute(sql)

    def fetchall(self):
        return self.raw.fetchall()

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Connection:
    def __init__(self, path, fail_drop=False):
        self.raw, self.fail_drop, self.closed = sqlite3.connect(path), fail_drop, False

    def cursor(self):
        return Cursor(self.raw.cursor(), self.fail_drop)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()
        self.closed = True


class DatabaseProbeTests(unittest.TestCase):
    def test_actual_commit_rollback_and_fresh_connection_leave_no_scratch_table(self):
        with tempfile.TemporaryDirectory() as temp:
            path, connections = Path(temp) / "database.sqlite", []
            def connect():
                connection = Connection(path)
                connections.append(connection)
                return connection
            result = transaction_probe(connect)
            self.assertTrue(all(result[key] for key in ("commit", "rollback", "fresh_connection")))
            self.assertEqual(len(connections), 2)
            self.assertTrue(all(connection.closed for connection in connections))
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])

    def test_reconnect_failure_keeps_original_handle_for_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "database.sqlite"
            original = Connection(path)
            failure = ConnectionError("reconnect unavailable")
            calls = 0
            def connect():
                nonlocal calls
                calls += 1
                if calls == 2:
                    self.assertFalse(original.closed)
                    raise failure
                return original
            with self.assertRaises(ConnectionError) as raised:
                transaction_probe(connect)
            self.assertIs(raised.exception, failure)
            self.assertTrue(original.closed)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])

    def test_cleanup_failure_does_not_mask_original_reconnect_error(self):
        with tempfile.TemporaryDirectory() as temp:
            original = Connection(Path(temp) / "database.sqlite", fail_drop=True)
            failure = ConnectionError("reconnect unavailable")
            calls = 0
            def connect():
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise failure
                return original
            with self.assertRaises(ConnectionError) as raised:
                transaction_probe(connect)
            self.assertIs(raised.exception, failure)
            self.assertIn("cleanup also failed", raised.exception.__notes__[0])
            self.assertTrue(original.closed)

    def test_target_fingerprint_changes_with_target_and_never_records_password(self):
        spec = {"driver": "postgresql", "user": "bench", "dsn": "host=db-a dbname=engine sslmode=verify-full sslrootcert=/ca.pem", "password_env": "PASSWORD_A"}
        fingerprint = target_fingerprint(spec)
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
        self.assertEqual(fingerprint, target_fingerprint({**spec, "password_env": "PASSWORD_B"}))
        for changed in ({"user": "other"}, {"driver": "oracle"}, {"dsn": spec["dsn"].replace("db-a", "db-b")}):
            self.assertNotEqual(fingerprint, target_fingerprint({**spec, **changed}))
        for dsn in (spec["dsn"] + " password = 'secret'", "postgresql://name:secret@db/engine"):
            with self.assertRaises(ValueError) as raised:
                target_fingerprint({**spec, "dsn": dsn})
            self.assertNotIn("secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
