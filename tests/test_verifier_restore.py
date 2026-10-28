import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

from scripts.restore_verifier import cloud_source_fence, require_stopped, restore


class VerifierRestoreTests(unittest.TestCase):
    def test_cloud_fence_rejects_missing_or_mismatched_backup_identity(self):
        args = SimpleNamespace(oci_source_instance_id='ocid1.instance.oc1.phx.source')
        for identity in ({}, {'instance_id': 'ocid1.instance.oc1.phx.other'}):
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, 'matching_backup_instance_identity'):
                cloud_source_fence(args, {'oci_source_identity': identity})

    def test_cloud_fence_checks_live_state_and_fails_closed(self):
        instance_id = 'ocid1.instance.oc1.phx.source'
        args = SimpleNamespace(oci_source_instance_id=instance_id, oci_profile='BENCHMARK')
        reference = {'oci_source_identity': {'instance_id': instance_id, 'region': 'us-phoenix-1',
                                            'machine_id': 'source-vm', 'boot_id': 'source-boot'}}
        sdk = MagicMock()
        sdk.config.from_file.return_value = {'region': 'us-phoenix-1', 'security_token_file': 'token', 'key_file': 'key'}
        compute = sdk.core.ComputeClient.return_value
        response = compute.get_instance.return_value
        response.headers = {'opc-request-id': 'fresh-request'}
        response.data.id = instance_id
        with patch.dict('sys.modules', {'oci': sdk}), patch.object(Path, 'read_text', return_value='private-token'):
            for state in ('RUNNING', 'STOPPING', 'STARTING', 'TERMINATED', 'UNKNOWN'):
                response.data.lifecycle_state = state
                with self.subTest(state=state), self.assertRaisesRegex(ValueError, 'not_stopped'):
                    cloud_source_fence(args, reference)
            response.data.lifecycle_state = 'STOPPED'
            result = cloud_source_fence(args, reference)
            self.assertEqual(result['lifecycle_state'], 'STOPPED')
            self.assertEqual(result['opc_request_id'], 'fresh-request')
            self.assertEqual(compute.get_instance.call_count, 6)
            sdk.config.from_file.return_value['region'] = 'other-region'
            with self.assertRaisesRegex(ValueError, 'region_mismatch'):
                cloud_source_fence(args, reference)
            self.assertEqual(compute.get_instance.call_count, 6)

    def test_cloud_restore_never_contacts_unreachable_source_and_rechecks_fence(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            backup = folder / 'registry.sqlite'
            backup.write_bytes(b'test')
            receipt = folder / 'receipt.json'
            receipt.write_text(json.dumps({'sha256': hashlib.sha256(b'test').hexdigest(), 'bytes': 4}))
            args = SimpleNamespace(source_host='offline', target_host='replacement', backup=backup,
                                   backup_receipt=receipt, ssh_config=folder / 'ssh.conf',
                                   oci_source_instance_id='ocid1.instance.oc1.phx.source')
            source = {'boot_id': 'source-boot', 'machine_id': 'source-vm', 'lifecycle_state': 'STOPPED'}
            target = {'properties': {'ActiveState': 'inactive', 'MainPID': '0'},
                      'verifier_process_ids': [], 'boot_id': 'target-boot', 'machine_id': 'target-vm'}
            with patch('scripts.restore_verifier.cloud_source_fence', return_value=source) as cloud, patch(
                    'scripts.restore_verifier.remote', side_effect=[target, {'all_macs_verified': True}, target]) as remote:
                result = restore(args)
                self.assertEqual(cloud.call_count, 2)
                self.assertTrue(result['restore']['all_macs_verified'])
                self.assertTrue(all(call.args[1] == 'replacement' for call in remote.call_args_list))
            with patch('scripts.restore_verifier.cloud_source_fence', side_effect=[source, ValueError('source_oci_instance_is_not_stopped')]), patch(
                    'scripts.restore_verifier.remote', side_effect=[target, {'all_macs_verified': True}]) as remote:
                with self.assertRaisesRegex(ValueError, 'not_stopped'):
                    restore(args)
                self.assertEqual(remote.call_count, 2)

    def test_different_aliases_for_the_same_vm_are_rejected(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            backup = folder / 'registry.sqlite'
            backup.write_bytes(b'test')
            receipt = folder / 'receipt.json'
            receipt.write_text(json.dumps({'sha256': hashlib.sha256(b'test').hexdigest(), 'bytes': 4}))
            args = SimpleNamespace(source_host='alias1', target_host='alias2', backup=backup,
                                   backup_receipt=receipt, ssh_config=folder / 'ssh.conf')
            stopped = {'properties': {'ActiveState': 'inactive', 'MainPID': '0', 'UnitFileState': 'masked'},
                       'verifier_process_ids': [], 'unit_is_persistent_mask': True,
                       'boot_id': 'same-boot', 'machine_id': 'same-vm'}
            with patch('scripts.restore_verifier.remote', return_value=stopped) as remote:
                with self.assertRaisesRegex(ValueError, 'distinct_virtual_machines'):
                    restore(args)
                self.assertEqual(remote.call_count, 2)

    def test_source_requires_persistent_mask_and_no_worker_process(self):
        valid = {'properties': {'ActiveState': 'inactive', 'MainPID': '0', 'UnitFileState': 'masked'},
                 'verifier_process_ids': [], 'unit_is_persistent_mask': True}
        require_stopped(valid, source=True)
        for change in ({'properties': {**valid['properties'], 'MainPID': '123'}},
                       {'properties': {**valid['properties'], 'UnitFileState': 'disabled'}},
                       {'verifier_process_ids': [123]}, {'unit_is_persistent_mask': False}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                require_stopped({**valid, **change}, source=True)

    def test_corrupted_backup_is_rejected_before_any_host_access(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            backup = folder / 'registry.sqlite'
            backup.write_bytes(b'corrupt')
            receipt = folder / 'receipt.json'
            receipt.write_text(json.dumps({'sha256': '0' * 64, 'bytes': 7}))
            args = SimpleNamespace(source_host='original', target_host='replacement', backup=backup,
                                   backup_receipt=receipt, ssh_config=folder / 'ssh.conf')
            with patch('scripts.restore_verifier.remote') as remote:
                with self.assertRaisesRegex(ValueError, 'checksum_mismatch'):
                    restore(args)
                remote.assert_not_called()

    def test_active_source_blocks_target_access_and_restore(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            backup = folder / 'registry.sqlite'
            backup.write_bytes(b'test')
            receipt = folder / 'receipt.json'
            receipt.write_text(json.dumps({'sha256': hashlib.sha256(b'test').hexdigest(), 'bytes': 4}))
            args = SimpleNamespace(source_host='original', target_host='replacement', backup=backup,
                                   backup_receipt=receipt, ssh_config=folder / 'ssh.conf')
            active = {'properties': {'ActiveState': 'active', 'MainPID': '123', 'UnitFileState': 'enabled'},
                      'verifier_process_ids': [123], 'unit_is_persistent_mask': False}
            with patch('scripts.restore_verifier.remote', return_value=active) as remote:
                with self.assertRaisesRegex(ValueError, 'not_stopped'):
                    restore(args)
                self.assertEqual(remote.call_count, 1)
                self.assertEqual(remote.call_args.args[1], 'original')
