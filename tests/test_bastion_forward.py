import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location("bastion_forward", Path(__file__).resolve().parents[1] / "scripts" / "bastion_forward.py")
forward = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forward)


class BastionForwardTests(unittest.TestCase):
    def contract(self):
        return {"region": "us-phoenix-1", "bastion": {"id": "ocid1.bastion.oc1.phx.example"},
                "roles": {"controller": {"private_ip": "10.78.0.10", "user": "ubuntu"}}}

    def session(self):
        sid = "ocid1.bastionsession.oc1.phx.example"
        return SimpleNamespace(id=sid, lifecycle_state="ACTIVE", ssh_metadata={
            "command": f"ssh -i <privateKey> -N -L <localPort>:10.78.0.10:22 -p 22 {sid}@host.bastion.us-phoenix-1.oci.oraclecloud.com"})

    def test_accepts_direct_and_terraform_wrapped_exports(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "contract.json"
            for data in (self.contract(), {"environment_contract": {"value": self.contract()}}):
                path.write_text(json.dumps(data), encoding="utf-8-sig")
                self.assertEqual(forward.load_contract(path, Path(temp)), self.contract())

    def test_rejects_unapplied_bastion_and_non_private_targets(self):
        contract = self.contract()
        contract["bastion"] = None
        with self.assertRaisesRegex(ValueError, "No deployed Bastion"):
            forward.connection_details(contract)
        for address in ("127.0.0.1", "169.254.169.254", "8.8.8.8", "::1", "10.78.0.10;whoami"):
            contract = self.contract()
            contract["roles"]["controller"]["private_ip"] = address
            with self.assertRaises(ValueError):
                forward.connection_details(contract)

    def test_tunnel_is_loopback_only_and_preserves_host_key_checks(self):
        key = Path("folder with spaces") / "key"
        command = forward.tunnel_command("ssh", key, 2222, "10.78.0.10", self.session())
        self.assertIn("127.0.0.1:2222:10.78.0.10:22", command)
        self.assertIn(str(key), command)
        self.assertIn("ExitOnForwardFailure=yes", command)
        self.assertNotIn("StrictHostKeyChecking=no", command)
        self.assertNotIn("-A", command)

    def test_does_not_execute_metadata_command_and_rejects_wrong_session(self):
        session = self.session()
        session.ssh_metadata["command"] += " ; touch /tmp/should-not-run"
        command = forward.tunnel_command("ssh", Path("key"), 2222, "10.78.0.10", session)
        self.assertNotIn("touch", " ".join(command))
        session.id = "ocid1.bastionsession.oc1.phx.other"
        with self.assertRaises(ValueError):
            forward.bastion_destination(session)

    def run_with_client(self, client):
        return forward.run_session(client, SimpleNamespace(session_ttl_in_seconds=1800),
                                   "ssh", Path("key"), 2222, "10.78.0.10", "ubuntu")

    def test_cleans_up_failed_session_before_starting_ssh(self):
        client = Mock()
        client.create_session.return_value.data = self.session()
        client.get_session.return_value.data.lifecycle_state = "FAILED"
        with patch.object(forward.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "FAILED"):
                self.run_with_client(client)
        popen.assert_not_called()
        client.delete_session.assert_called_once_with(self.session().id)

    def test_cleans_up_session_when_ssh_cannot_launch(self):
        client = Mock()
        client.create_session.return_value.data = self.session()
        client.get_session.return_value.data = self.session()
        with patch.object(forward.subprocess, "Popen", side_effect=OSError("launch failed")):
            with self.assertRaises(OSError):
                self.run_with_client(client)
        client.delete_session.assert_called_once_with(self.session().id)

    def test_interrupt_stops_child_and_deletes_only_created_session(self):
        client = Mock()
        client.create_session.return_value.data = self.session()
        client.get_session.return_value.data = self.session()
        process = Mock()
        process.wait.side_effect = [KeyboardInterrupt(), 0]
        process.poll.return_value = None
        with patch.object(forward.subprocess, "Popen", return_value=process), \
                patch.object(forward, "wait_for_forward", return_value=True):
            with self.assertRaises(KeyboardInterrupt):
                self.run_with_client(client)
        process.terminate.assert_called_once()
        client.delete_session.assert_called_once_with(self.session().id)

    def test_retries_startup_failure_using_same_session_before_announcing_ready(self):
        client = Mock()
        client.create_session.return_value.data = self.session()
        client.get_session.return_value.data = self.session()
        failed, connected = Mock(), Mock()
        failed.poll.return_value = failed.wait.return_value = 255
        connected.poll.return_value = connected.wait.return_value = 0
        output = io.StringIO()

        def readiness(process, port, deadline):
            self.assertNotIn("Ready:", output.getvalue())
            return process is connected

        with patch.object(forward.subprocess, "Popen", side_effect=[failed, connected]) as popen, \
                patch.object(forward, "wait_for_forward", side_effect=readiness), \
                patch.object(forward.time, "sleep") as sleep, \
                patch.object(forward.sys, "stdout", output):
            self.assertEqual(self.run_with_client(client), 0)
        self.assertEqual(popen.call_args_list[0], popen.call_args_list[1])
        sleep.assert_called_once_with(forward.STARTUP_RETRY_DELAY)
        client.create_session.assert_called_once()
        client.delete_session.assert_called_once_with(self.session().id)
        self.assertIn("Ready:", output.getvalue())

    def test_persistent_startup_failure_is_bounded_and_cleans_up(self):
        client = Mock()
        client.create_session.return_value.data = self.session()
        client.get_session.return_value.data = self.session()
        process = Mock()
        process.poll.return_value = process.wait.return_value = 255
        output = io.StringIO()
        with patch.object(forward.subprocess, "Popen", return_value=process) as popen, \
                patch.object(forward, "wait_for_forward", return_value=False), \
                patch.object(forward.time, "sleep"), \
                patch.object(forward.sys, "stdout", output):
            with self.assertRaisesRegex(RuntimeError, "before the tunnel was ready"):
                self.run_with_client(client)
        self.assertEqual(popen.call_count, forward.STARTUP_ATTEMPTS)
        self.assertNotIn("Ready:", output.getvalue())
        client.delete_session.assert_called_once_with(self.session().id)

    def test_established_tunnel_failure_is_not_retried(self):
        client = Mock()
        client.create_session.return_value.data = self.session()
        client.get_session.return_value.data = self.session()
        process = Mock()
        process.poll.return_value = process.wait.return_value = 255
        with patch.object(forward.subprocess, "Popen", return_value=process) as popen, \
                patch.object(forward, "wait_for_forward", return_value=True):
            self.assertEqual(self.run_with_client(client), 255)
        popen.assert_called_once()
        client.delete_session.assert_called_once_with(self.session().id)

    def test_startup_timeout_stops_child_and_cleans_up(self):
        client = Mock()
        client.create_session.return_value.data = self.session()
        client.get_session.return_value.data = self.session()
        process = Mock()
        process.poll.return_value = None
        with patch.object(forward.subprocess, "Popen", return_value=process), \
                patch.object(forward, "wait_for_forward", side_effect=TimeoutError("not ready")):
            with self.assertRaises(TimeoutError):
                self.run_with_client(client)
        process.terminate.assert_called_once()
        client.delete_session.assert_called_once_with(self.session().id)

    def test_readiness_requires_target_ssh_banner(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(forward.socket, "create_connection") as connect, \
                patch.object(forward.time, "monotonic", return_value=0), \
                patch.object(forward.time, "sleep"):
            connect.return_value.__enter__.return_value.recv.side_effect = [b"", b"SSH-2.0-OpenSSH\r\n"]
            self.assertTrue(forward.wait_for_forward(process, 2222, 10))
        self.assertEqual(connect.call_count, 2)

    def test_readiness_deadline_does_not_accept_silent_listener(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(forward.socket, "create_connection") as connect, \
                patch.object(forward.time, "monotonic", side_effect=[0, 121]), \
                patch.object(forward.time, "sleep"):
            connect.return_value.__enter__.return_value.recv.side_effect = TimeoutError()
            with self.assertRaises(TimeoutError):
                forward.wait_for_forward(process, 2222, 120)

    def test_session_creation_failure_does_not_delete_another_session(self):
        client = Mock()
        client.create_session.side_effect = RuntimeError("quota")
        with self.assertRaises(RuntimeError):
            self.run_with_client(client)
        client.delete_session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
