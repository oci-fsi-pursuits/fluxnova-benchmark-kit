import copy
import unittest

from benchlib.timing_comparison import database_spec, measurement_plan, require_matched_controls, require_owned_database


class TimingComparisonTests(unittest.TestCase):
    def target(self):
        nonce = 'a' * 32
        return dict(id='owned', display_name='fluxnova-timing-' + nonce[:12],
                    freeform_tags=dict(managed_by='fluxnova-benchmarks', comparison_run_id=nonce,
                                       temporary_comparison='true', comparison_lane='worker-timing'),
                    source=dict(source_type='NONE'), instance_count=2)

    def guard(self, target, **kwargs):
        require_owned_database(target, **dict(source_id='original', target_id='owned', run_id='a' * 32, **kwargs))

    def test_original_database_is_always_excluded(self):
        with self.assertRaises(ValueError):
            require_owned_database(self.target(), source_id='owned', target_id='owned', run_id='a' * 32)

    def test_spec_copies_allocation_without_original_data_or_endpoint(self):
        source = {'instance_count': 1, 'lifecycle_state': 'ACTIVE', 'system_type': 'OCI_OPTIMIZED_STORAGE',
                  'compartment_id': 'compartment', 'db_version': '17.6', 'config_id': 'configuration',
                  'shape': 'VM.Standard.E5.Flex', 'instance_ocpu_count': 2, 'instance_memory_size_in_gbs': 32,
                  'storage_details': {'iops': 75000}, 'network_details': {'subnet_id': 'private',
                  'nsg_ids': ['group'], 'primary_db_endpoint_private_ip': 'original-address'}}
        before = copy.deepcopy(source)
        spec = database_spec(source, 'a' * 32)
        self.assertEqual(source, before)
        self.assertEqual(spec['source'], {'source_type': 'NONE'})
        self.assertEqual(spec['instance_count'], 2)
        self.assertEqual(spec['management_policy']['backup_policy'], {'kind': 'NONE'})
        self.assertNotIn('primary_db_endpoint_private_ip', spec['network_details'])
        self.assertNotIn('recovery_run_id', spec['freeform_tags'])
        self.guard({**spec, 'id': 'owned'})

    def test_exact_create_identity_required(self):
        self.guard(self.target())
        for field, value in [('id', 'other'), ('display_name', 'fluxnova-timing-other'),
                             ('source', {'source_type': 'BACKUP'}), ('source', None), ('instance_count', 1),
                             ('freeform_tags', {})]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.guard({**self.target(), field: value})
        for field in self.target()['freeform_tags']:
            target = self.target(); target['freeform_tags'][field] = 'wrong'
            with self.subTest(tag=field), self.assertRaises(ValueError): self.guard(target)

    def test_plan_is_fixed_and_stops_on_invalid_evidence(self):
        p = measurement_plan()
        self.assertEqual(p['pairs'], [['on', 'off'], ['off', 'on'], ['on', 'off'], ['off', 'on'], ['on', 'off']])
        self.assertTrue(p['stop_on_first_nonpassing_or_invalid_run'])
        self.assertEqual(p['rate_per_s'] * p['measurement_s'], p['planned_correct_per_run'])

    def test_only_timing_flag_can_differ(self):
        before = {'runtime': {'worker_timing_enabled': True, 'heap_gib': 2}, 'image': 'pinned', 'checkpoint': 'same'}
        after = copy.deepcopy(before); after['runtime']['worker_timing_enabled'] = False
        require_matched_controls(before, after)
        for field, value in [('image', 'other'), ('checkpoint', 'other')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                require_matched_controls(before, {**after, field: value})
        after['runtime']['heap_gib'] = 3
        with self.assertRaises(ValueError): require_matched_controls(before, after)

    def test_missing_non_boolean_or_duplicate_setting_is_rejected(self):
        for setting in (None, 1, 'on', True):
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                require_matched_controls({'runtime': {'worker_timing_enabled': True}},
                                         {'runtime': {'worker_timing_enabled': setting}})
