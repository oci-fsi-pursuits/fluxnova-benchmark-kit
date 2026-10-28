import json
import subprocess
import unittest
from unittest.mock import patch

from benchlib.deployment import SERVICES_NETWORK, deploy_role, services_network


class DeploymentNetworkTests(unittest.TestCase):
    def test_does_not_adopt_an_unrelated_network(self):
        result = subprocess.CompletedProcess([], 0, json.dumps([{'Driver': 'bridge', 'Labels': {}}]))
        with patch('benchlib.deployment.subprocess.run', return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError, 'not managed'):
                services_network()
            self.assertEqual(run.call_count, 1)

    def test_worker_uses_internal_endpoint_on_support_network(self):
        config = {'runtime': {'base_url': 'http://10.0.0.1:8080/engine-rest',
                              'support_url': 'http://10.0.0.2:8090',
                              'worker_support_url': 'http://benchmark-support:8090'},
                  'artifact': {'services_image': 'registry/test@sha256:' + 'a' * 64}}
        with patch('benchlib.deployment.services_network', return_value=SERVICES_NETWORK), \
                patch('benchlib.deployment.secret', return_value='test-secret'), \
                patch('benchlib.deployment.run_container', return_value='container') as run:
            deploy_role(config, 'worker')
        _, _, env, options, _ = run.call_args.args
        self.assertEqual(env['BENCH_SUPPORT_URL'], 'http://benchmark-support:8090')
        self.assertEqual(options[options.index('--network') + 1], SERVICES_NETWORK)
        self.assertEqual(env['BENCH_BASE_URL'], config['runtime']['base_url'])
        self.assertNotIn('BENCH_WORKER_TIMING', env)

    def test_worker_timing_is_opt_in_and_requires_boolean(self):
        config = {'runtime': {'base_url': 'http://engine', 'support_url': 'http://support', 'worker_timing_enabled': True},
                  'artifact': {'services_image': 'registry/test@sha256:' + 'a' * 64}}
        with patch('benchlib.deployment.services_network', return_value=SERVICES_NETWORK), \
                patch('benchlib.deployment.secret', return_value='test-secret'), \
                patch('benchlib.deployment.run_container', return_value='container') as run:
            deploy_role(config, 'worker')
            self.assertEqual(run.call_args.args[2]['BENCH_WORKER_TIMING'], '1')
            config['runtime']['worker_timing_enabled'] = 'false'
            with self.assertRaisesRegex(ValueError, 'must be a boolean'):
                deploy_role(config, 'worker')

    def test_worker_image_override_preserves_support_image(self):
        original = 'registry/test@sha256:' + 'a' * 64
        worker = 'registry/test@sha256:' + 'b' * 64
        config = {'runtime': {'base_url':'http://engine', 'support_url':'http://support'},
                  'artifact': {'services_image':original, 'worker_image':worker}}
        with patch('benchlib.deployment.services_network', return_value=SERVICES_NETWORK), \
                patch('benchlib.deployment.secret', return_value='test-secret'), \
                patch('benchlib.deployment.run_container', return_value='container') as run:
            deploy_role(config, 'worker')
            self.assertEqual(run.call_args.args[1], worker)
            deploy_role(config, 'support')
            self.assertEqual(run.call_args.args[1], original)


if __name__ == '__main__':
    unittest.main()
