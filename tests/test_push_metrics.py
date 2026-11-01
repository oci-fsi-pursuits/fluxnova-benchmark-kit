import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.push_metrics import database_snapshot, publish, validate_release


class PushMetricsTests(unittest.TestCase):
    def test_release_must_stay_within_workspace(self):
        valid = '/opt/fluxnova-bench/workspace/release-test'
        self.assertEqual(validate_release(valid), valid)
        for path in ['/tmp/release-test', '/opt/fluxnova-bench/workspace/release-test/../outside', '/opt/fluxnova-bench/workspace']:
            with self.assertRaises(ValueError):
                validate_release(path)

    def test_snapshot_is_stdin_data_not_shell_interpolation(self):
        snapshot = {'application': {'_meta': {'observed_at': '$(touch bad)'}, 'cpu': 1}}
        with patch('scripts.push_metrics.subprocess.run') as run:
            publish('ubuntu@10.0.0.1', '/opt/fluxnova-bench/workspace/release-test', snapshot)
        self.assertEqual(json.loads(run.call_args.kwargs['input']), snapshot)
        self.assertNotIn('$(touch bad)', run.call_args.args[0][-1])
        self.assertIn('StrictHostKeyChecking=yes', run.call_args.args[0])

    def test_connection_timeout_retry_records_failure_and_fresh_result(self):
        class ConnectionTimeout(Exception):
            pass
        stamps = ['2026-01-01T00:00:00+00:00', '2026-01-01T00:00:02+00:00',
                  '2026-01-01T00:00:02.1+00:00', '2026-01-01T00:00:02.2+00:00']
        with patch.dict(sys.modules, {'psycopg.errors': SimpleNamespace(ConnectionTimeout=ConnectionTimeout)}), \
                patch('scripts.push_metrics.postgres_metrics', side_effect=[ConnectionTimeout('private connection details'), {'db_connections': 7}]) as collect, \
                patch('scripts.push_metrics.utc_now', side_effect=stamps):
            snapshot = database_snapshot({'host': 'example.invalid'}, 2)
        self.assertEqual(collect.call_count, 2)
        self.assertEqual(snapshot['db_connections'], 7)
        meta = snapshot['_meta']
        self.assertEqual(meta['errors'], {})
        self.assertEqual(meta['observed_at'], stamps[2])
        self.assertEqual([row['error_type'] for row in meta['collection_attempts']], ['ConnectionTimeout', None])
        self.assertNotIn('private connection details', json.dumps(snapshot))

    def test_retry_limit_and_nonconnection_errors_remain_failed_snapshots(self):
        class ConnectionTimeout(Exception):
            pass
        for attempts, error, expected_calls in [(1, ConnectionTimeout('private'), 1),
                                                (2, ConnectionTimeout('private'), 2),
                                                (2, ValueError('private'), 1)]:
            with self.subTest(attempts=attempts, error=type(error).__name__), \
                    patch.dict(sys.modules, {'psycopg.errors': SimpleNamespace(ConnectionTimeout=ConnectionTimeout)}), \
                    patch('scripts.push_metrics.postgres_metrics', side_effect=error) as collect:
                snapshot = database_snapshot({}, attempts)
            self.assertEqual(collect.call_count, expected_calls)
            self.assertEqual(set(snapshot), {'_meta'})
            self.assertEqual(snapshot['_meta']['errors'], {'database': type(error).__name__})
            self.assertEqual(len(snapshot['_meta']['collection_attempts']), expected_calls)
            self.assertNotIn('private', json.dumps(snapshot))

    def test_invalid_connection_attempt_limit_does_not_collect(self):
        for limit in [0, 3, True, 1.5, '2']:
            with self.subTest(limit=limit), patch('scripts.push_metrics.postgres_metrics') as collect:
                with self.assertRaises(ValueError):
                    database_snapshot({}, limit)
                collect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
