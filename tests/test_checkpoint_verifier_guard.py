import json
import unittest
from unittest.mock import Mock
from scripts.state_checkpoint import Cell


class CheckpointVerifierGuardTests(unittest.TestCase):
    def record(self, **changes):
        return {**{'properties': {'ActiveState': 'inactive', 'MainPID': '0',
                                  'LoadState': 'masked', 'UnitFileState': 'masked'},
                   'verifier_process_ids': [], 'unit_is_persistent_mask': True}, **changes}

    def test_active_or_merely_disabled_verifier_blocks_pair_checkpoint(self):
        cell = object.__new__(Cell)
        for record in [self.record(verifier_process_ids=[123]),
                       self.record(properties={'ActiveState': 'inactive', 'MainPID': '0', 'LoadState': 'loaded', 'UnitFileState': 'disabled'}, unit_is_persistent_mask=False)]:
            cell.ssh = Mock(return_value=json.dumps(record).encode())
            with self.assertRaises(ValueError):cell.require_verifier_fenced()

    def test_persistent_mask_or_no_installed_service_can_be_quiesced(self):
        cell = object.__new__(Cell)
        for record in [self.record(), self.record(properties={'LoadState': 'not-found', 'ActiveState': 'inactive', 'MainPID': '0'}, unit_is_persistent_mask=False)]:
            cell.ssh = Mock(return_value=json.dumps(record).encode())
            cell.require_verifier_fenced()
