import copy
import unittest
from benchlib.managed_recovery import (target_spec, require_owned_target, pitr_target_spec,
                                      require_owned_pitr_target, replica_for_failover,
                                      engine_failover_spec, require_owned_engine_failover_target)


class ManagedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.run_id = 'a' * 32
        self.source = {'id': 'source', 'lifecycle_state': 'ACTIVE', 'compartment_id': 'compartment',
                       'instance_count': 1, 'system_type': 'OCI_OPTIMIZED_STORAGE', 'db_version': '17.6',
                       'config_id': 'config', 'shape': 'VM.Standard.E5.Flex', 'instance_ocpu_count': 2,
                       'instance_memory_size_in_gbs': 32, 'storage_details': {'iops': 75000},
                       'network_details': {'subnet_id': 'subnet', 'nsg_ids': ['nsg'],
                                           'primary_db_endpoint_private_ip': '10.0.1.10'}}
        self.backup = {'id': 'backup', 'lifecycle_state': 'ACTIVE', 'db_system_id': 'source',
                       'compartment_id': 'compartment'}

    def test_separate_endpoint_and_bounded_topology(self):
        original = copy.deepcopy(self.source)
        spec = target_spec(self.source, self.backup, self.run_id)
        self.assertNotIn('primary_db_endpoint_private_ip', spec['network_details'])
        self.assertNotIn('id', spec)
        self.assertNotIn('credentials', spec)
        self.assertEqual(spec['instance_count'], 1)
        self.assertEqual(spec['shape'], 'PostgreSQL.VM.Standard.E5.Flex')
        self.assertEqual(spec['management_policy']['backup_policy']['kind'], 'NONE')
        spec['network_details']['nsg_ids'].append('other')
        self.assertEqual(self.source, original)

    def test_unrelated_or_unready_backup_rejected(self):
        for field, value in [('db_system_id', 'other'), ('compartment_id', 'other'), ('lifecycle_state', 'CREATING')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                target_spec(self.source, {**self.backup, field: value}, self.run_id)

    def test_unsupported_topology_rejected(self):
        for field, value in [('instance_count', 2), ('system_type', 'OTHER'), ('lifecycle_state', 'UPDATING')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                target_spec({**self.source, field: value}, self.backup, self.run_id)

    def test_cleanup_requires_exact_id_tags_and_backup(self):
        target = {**target_spec(self.source, self.backup, self.run_id), 'id': 'target'}
        kwargs = dict(source_id='source', backup_id='backup', target_id='target', run_id=self.run_id)
        require_owned_target(target, **kwargs)
        for changed in [{'id': 'source'}, {'id': 'another'}, {'freeform_tags': {}},
                        {'source': {'source_type': 'BACKUP', 'backup_id': 'other'}}]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                require_owned_target({**target, **changed}, **kwargs)
        with self.assertRaises(ValueError):
            require_owned_target({**target, 'id': 'source'}, **{**kwargs, 'target_id': 'source'})

    def test_run_identity_required(self):
        for run in ['', 'recovery', 'a' * 31, '../' + 'a' * 29]:
            with self.subTest(run=run), self.assertRaises(ValueError):
                target_spec(self.source, self.backup, run)

    def test_engine_failover_allocates_fresh_system_without_source_endpoint(self):
        original = copy.deepcopy(self.source)
        spec = engine_failover_spec(self.source, self.run_id)
        self.assertEqual(spec['source'], {'source_type': 'NONE'})
        self.assertEqual(spec['instance_count'], 2)
        self.assertNotIn('id', spec)
        self.assertNotIn('primary_db_endpoint_private_ip', spec['network_details'])
        self.assertNotIn('credentials', spec)
        self.assertEqual(spec['management_policy']['pitr_policy']['kind'], 'NONE')
        self.assertEqual(spec['management_policy']['backup_policy']['kind'], 'NONE')
        self.assertEqual(self.source, original)
        with self.assertRaises(ValueError):
            engine_failover_spec({**self.source, 'instance_count': 2}, self.run_id)

    def test_engine_failover_guard_refuses_source_and_cross_lane_targets(self):
        target = {**engine_failover_spec(self.source, self.run_id), 'id': 'target'}
        kwargs = dict(source_id='source', target_id='target', run_id=self.run_id)
        require_owned_engine_failover_target(target, **kwargs)
        invalid = [{'id': 'source'}, {'id': 'another'}, {'instance_count': 1},
                   {'source': {'source_type': 'BACKUP', 'backup_id': 'backup'}},
                   {'freeform_tags': {**target['freeform_tags'], 'recovery_lane': 'pitr'}},
                   {'freeform_tags': {**target['freeform_tags'], 'recovery_run_id': 'b' * 32}},
                   {'display_name': 'unrelated'}]
        for changed in invalid:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                require_owned_engine_failover_target({**target, **changed}, **kwargs)
        with self.assertRaises(ValueError):
            require_owned_engine_failover_target({**target, 'id': 'source'},
                                                **{**kwargs, 'target_id': 'source'})

    def pitr_source(self):
        return {**self.source, 'instance_count': 2,
                'management_policy': {'pitr_policy': {'kind': 'STANDARD'}}}

    def windows(self):
        return [{'time_recovery_window_start': '2026-10-16T17:00:00Z',
                 'time_recovery_window_end': '2026-10-16T17:10:00Z'}]

    def test_pitr_requires_actual_window_and_discrete_utc_timestamp(self):
        spec = pitr_target_spec(self.pitr_source(), self.run_id, '2026-10-16T10:05:00-07:00', self.windows())
        self.assertEqual(spec['source']['time_to_restore'], '2026-10-16T17:05:00+00:00')
        self.assertEqual(spec['instance_count'], 1)
        self.assertNotIn('primary_db_endpoint_private_ip', spec['network_details'])
        for stamp in ['2026-10-16T17:11:00Z', '2026-10-16T16:59:59Z',
                      '2026-10-16T17:05:00.001Z', '2026-10-16T17:05:00']:
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                pitr_target_spec(self.pitr_source(), self.run_id, stamp, self.windows())
        with self.assertRaises(ValueError):
            pitr_target_spec(self.pitr_source(), self.run_id, '2026-10-16T17:05:00Z', [])

    def test_pitr_rejects_disabled_policy_and_unplanned_nodes(self):
        for changed in [{'instance_count': 3}, {'management_policy': {'pitr_policy': {'kind': 'NONE'}}}]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                pitr_target_spec({**self.pitr_source(), **changed}, self.run_id,
                                 '2026-10-16T17:05:00Z', self.windows())

    def test_pitr_cleanup_checks_original_source_and_exact_timestamp(self):
        stamp = '2026-10-16T17:05:00Z'
        target = {**pitr_target_spec(self.pitr_source(), self.run_id, stamp, self.windows()), 'id': 'target'}
        kwargs = dict(source_id='source', target_id='target', run_id=self.run_id, restore_at=stamp)
        require_owned_pitr_target(target, **kwargs)
        for field, value in [('source_type', 'BACKUP'), ('db_system_id', 'other'),
                             ('time_to_restore', '2026-10-16T17:06:00Z')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                require_owned_pitr_target({**target, 'source': {**target['source'], field: value}}, **kwargs)
        with self.assertRaises(ValueError):
            require_owned_pitr_target({**target, 'id': 'source'}, **{**kwargs, 'target_id': 'source'})

    def test_replica_selection_uses_authoritative_primary(self):
        db = {'lifecycle_state': 'ACTIVE', 'instance_count': 2,
              'instances': [{'id': 'a', 'lifecycle_state': 'ACTIVE'}, {'id': 'b', 'lifecycle_state': 'ACTIVE'}]}
        self.assertEqual(replica_for_failover(db, 'a'), 'b')
        self.assertEqual(replica_for_failover(db, 'b'), 'a')
        with self.assertRaises(ValueError):
            replica_for_failover(db, 'unrelated')

    def test_replica_selection_rejects_unsafe_topology(self):
        db = {'lifecycle_state': 'ACTIVE', 'instance_count': 2,
              'instances': [{'id': 'a', 'lifecycle_state': 'ACTIVE'}, {'id': 'b', 'lifecycle_state': 'ACTIVE'}]}
        for changed in [{'lifecycle_state': 'UPDATING'}, {'instance_count': 1}, {'instances': db['instances'][:1]},
                        {'instances': [db['instances'][0], db['instances'][0]]},
                        {'instances': [db['instances'][0], {'id': 'b', 'lifecycle_state': 'CREATING'}]}]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                replica_for_failover({**db, **changed}, 'a')

    def test_omitted_pitr_time_requires_both_original_receipts(self):
        stamp = '2026-10-16T17:05:00Z'
        spec = pitr_target_spec(self.pitr_source(), self.run_id, stamp, self.windows())
        target = {**spec, 'id': 'target', 'source': {**spec['source'], 'time_to_restore': None}}
        kwargs = dict(source_id='source', target_id='target', run_id=self.run_id, restore_at=stamp)
        for proofs in [{}, {'creation_spec': spec}, {'accepted_target': target}]:
            with self.subTest(proofs=list(proofs)), self.assertRaises(ValueError):
                require_owned_pitr_target(target, **kwargs, **proofs)
        require_owned_pitr_target(target, **kwargs, creation_spec=spec, accepted_target=target)

    def test_pitr_fallback_rejects_conflicting_identity_or_time(self):
        stamp = '2026-10-16T17:05:00Z'
        spec = pitr_target_spec(self.pitr_source(), self.run_id, stamp, self.windows())
        target = {**spec, 'id': 'target', 'source': {**spec['source'], 'time_to_restore': None}}
        kwargs = dict(source_id='source', target_id='target', run_id=self.run_id, restore_at=stamp)
        invalid = [({'source': {**spec['source'], 'time_to_restore': '2026-10-16T17:06:00Z'}}, {}),
                   ({'freeform_tags': {}}, {}), ({}, {'id': 'other'}),
                   ({}, {'source': {**target['source'], 'db_system_id': 'other'}})]
        for changed_spec, changed_acceptance in invalid:
            with self.subTest(spec=changed_spec, accepted=changed_acceptance), self.assertRaises(ValueError):
                require_owned_pitr_target(target, **kwargs, creation_spec={**spec, **changed_spec},
                                          accepted_target={**target, **changed_acceptance})
        with self.assertRaises(ValueError):
            require_owned_pitr_target({**target, 'source': {**spec['source'], 'time_to_restore': '2026-10-16T17:06:00Z'}},
                                      **kwargs, creation_spec=spec, accepted_target=target)


if __name__ == '__main__':
    unittest.main()
