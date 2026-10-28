import copy
import unittest
from scripts.cleanup_failed_golden import owned_instances


class CleanupOwnershipTests(unittest.TestCase):
    def fixture(self):
        config = {'runtime': {'base_url': 'http://example/engine-rest'}, 'artifact': {'image': 'pinned-image'}}
        report = {'passed': False, 'endpoint': config['runtime']['base_url'], 'artifact': config['artifact'],
                  'run_id': 'golden-test', 'cases': [{'status': 'failed', 'logical_request_id': 'golden-test:case', 'process_instance_ids': ['owned']},
                                                   {'status': 'passed', 'process_instance_ids': ['keep']}]}
        return config, report

    def test_selects_only_recorded_failed_instances(self):
        config, report = self.fixture()
        self.assertEqual(owned_instances(report, config), {'owned': 'golden-test:case'})

    def test_rejects_wrong_endpoint_image_and_ownership(self):
        config, report = self.fixture()
        for field, value in [('endpoint', 'http://other/engine-rest'), ('passed', True), ('artifact', {'image': 'different'})]:
            changed = copy.deepcopy(report); changed[field] = value
            with self.assertRaises(ValueError): owned_instances(changed, config)
        report['cases'][0]['logical_request_id'] = 'unrelated'
        with self.assertRaises(ValueError): owned_instances(report, config)
