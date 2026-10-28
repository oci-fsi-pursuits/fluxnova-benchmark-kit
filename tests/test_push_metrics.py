import json
import unittest
from unittest.mock import patch

from scripts.push_metrics import publish, validate_release


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


if __name__ == '__main__':
    unittest.main()
