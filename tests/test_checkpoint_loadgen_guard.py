import unittest
from unittest.mock import Mock

from scripts.state_checkpoint import Cell


class CheckpointLoadgenGuardTests(unittest.TestCase):
    def test_running_container_blocks_before_stopping_any_writer(self):
        cell = object.__new__(Cell)
        cell.engine = Mock()
        cell.engine.request.return_value = {'count': 0}
        cell.ssh = Mock(return_value=b'9c2dee7f8ed7\n')
        cell.stopped = []

        with self.assertRaisesRegex(ValueError, 'running containers'):
            cell.quiesce()

        self.assertEqual(cell.stopped, [])
        cell.ssh.assert_called_once_with('loadgen', ['docker', 'ps', '--quiet'])

    def test_idle_host_reaches_writer_readiness_check(self):
        cell = object.__new__(Cell)
        cell.engine = Mock()
        cell.engine.request.return_value = {'count': 0}
        cell.ssh = Mock(side_effect=[b'', b'[{"State":{"Running":false}}]'])
        cell.stopped = []

        with self.assertRaisesRegex(ValueError, 'writers must initially be running'):
            cell.quiesce()

        self.assertEqual(cell.stopped, [])
        self.assertEqual(cell.ssh.call_count, 2)


if __name__ == '__main__':
    unittest.main()
