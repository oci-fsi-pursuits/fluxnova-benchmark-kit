import copy
from datetime import datetime, timedelta, timezone
import unittest

from benchlib.pay05_cloud import database_spec, require_owned_database, require_stage


RUN = 'a' * 32
NOW = datetime(2026, 10, 31, 18, tzinfo=timezone.utc)


def target():
    return {'id': 'target', 'display_name': 'fluxnova-pay05-' + RUN[:12], 'instance_count': 1,
            'source': {'source_type': 'NONE'}, 'freeform_tags': {'managed_by': 'fluxnova-benchmarks',
            'pay05_run_id': RUN, 'comparison_lane': 'pay05', 'temporary_comparison': 'true'}}


def stage():
    return {'study_id': 'PAY-05', 'run_id': RUN, 'stage': '/opt/fluxnova-bench/workspace/pay05-' + RUN,
            'source_id': 'source', 'target_id': 'target', 'source_db_host': 'original.example',
            'connection': {'primary_db_endpoint': {'fqdn': 'target.postgresql.us-phoenix-1.oci.oraclecloud.com'},
                           'ca_certificate': 'test-fixture-certificate'}, 'budget_limit_usd': 400,
            'images': {'engine': 'registry.example/engine@sha256:' + '1' * 64,
                       'services': 'registry.example/services@sha256:' + '2' * 64},
            'prepared_at': NOW.isoformat(), 'stop_before_utc': (NOW + timedelta(hours=2)).isoformat()}


class OwnedCloudQualificationTests(unittest.TestCase):
    def test_never_accept_original_or_another_trials_database(self):
        require_owned_database(target(), source_id='source', target_id='target', run_id=RUN)
        for change in ({'id': 'source'}, {'display_name': 'other'}, {'instance_count': 2},
                       {'source': {'source_type': 'BACKUP'}}, {'freeform_tags': {}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                require_owned_database({**target(), **change}, source_id='source', target_id='target', run_id=RUN)
        with self.assertRaises(ValueError):
            require_owned_database(target(), source_id='target', target_id='target', run_id=RUN)

    def test_every_ownership_tag_is_required(self):
        for key in target()['freeform_tags']:
            changed = target(); changed['freeform_tags'][key] = 'different'
            with self.subTest(key=key), self.assertRaises(ValueError):
                require_owned_database(changed, source_id='source', target_id='target', run_id=RUN)

    def test_stage_rejects_reference_endpoint_and_unpinned_images(self):
        good = stage(); self.assertEqual(require_stage(good, good['stage'], now=NOW), RUN)
        mutations = [('target_id', 'source'), ('source_db_host', good['connection']['primary_db_endpoint']['fqdn']),
                     ('study_id', 'w02'), ('budget_limit_usd', 401), ('stage', '/tmp/pay05-' + RUN)]
        for key, value in mutations:
            changed = copy.deepcopy(good); changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): require_stage(changed, good['stage'], now=NOW)
        changed = copy.deepcopy(good); changed['images']['engine'] = 'registry.example/engine:latest'
        with self.assertRaises(ValueError): require_stage(changed, changed['stage'], now=NOW)
        changed = copy.deepcopy(good); changed['connection']['primary_db_endpoint']['fqdn'] = 'attacker.example'
        with self.assertRaises(ValueError): require_stage(changed, changed['stage'], now=NOW)

    def test_window_is_explicit_bounded_and_expiry_is_inclusive(self):
        good = stage()
        for instant in (NOW - timedelta(seconds=1), NOW + timedelta(hours=2)):
            with self.assertRaises(ValueError): require_stage(good, good['stage'], now=instant)
        for stop in ((NOW + timedelta(hours=7)).isoformat(), NOW.replace(tzinfo=None).isoformat()):
            changed = {**good, 'stop_before_utc': stop}
            with self.assertRaises(ValueError): require_stage(changed, changed['stage'], now=NOW)

    def test_fresh_database_uses_same_size_without_cloning_workflow_data(self):
        source = {'instance_count': 1, 'lifecycle_state': 'ACTIVE', 'system_type': 'OCI_OPTIMIZED_STORAGE',
                  'compartment_id': 'compartment', 'db_version': '16.3', 'config_id': 'config',
                  'shape': 'VM.Standard.E5.Flex', 'instance_ocpu_count': 2, 'instance_memory_size_in_gbs': 32,
                  'storage_details': {'is_regionally_durable': True}, 'network_details': {'subnet_id': 'subnet', 'nsg_ids': ['nsg']}}
        before = copy.deepcopy(source); spec = database_spec(source, RUN)
        self.assertEqual(source, before)
        self.assertEqual((spec['instance_count'], spec['instance_ocpu_count'], spec['instance_memory_size_in_gbs']), (1, 2, 32))
        self.assertEqual(spec['source'], {'source_type': 'NONE'})
        self.assertEqual(spec['freeform_tags']['campaign_budget_usd'], '400')
        require_owned_database({**spec, 'id': 'target'}, source_id='source', target_id='target', run_id=RUN)


if __name__ == '__main__': unittest.main()
