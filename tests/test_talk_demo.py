import base64
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import run_talk_demo
from scripts.run_browser_review_checks import run as browser_run


class TalkDemoTests(unittest.TestCase):
    def test_refuses_existing_output_before_access(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(run_talk_demo.socket, 'create_connection') as connection:
            with self.assertRaises(SystemExit):
                run_talk_demo.main(['--output', folder])
            connection.assert_not_called()

    def test_refuses_missing_prerequisites_before_access(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(run_talk_demo.socket, 'create_connection') as connection:
            with self.assertRaises(SystemExit):
                run_talk_demo.main(['--output', str(Path(folder) / 'new'), '--secret-references', str(Path(folder) / 'missing')])
            connection.assert_not_called()

    def test_launcher_limits_case_and_keeps_secrets_out_of_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            refs = root / 'references.json'
            refs.write_text(json.dumps({'admin_password_secret_id': 'admin-ref', 'support_token_secret_id': 'support-ref'}))
            config = root / 'verifier.json'
            config.write_text('{}')
            packages = root / 'packages'
            packages.mkdir()
            secrets = {'admin-ref': 'not-a-command-line-password', 'support-ref': 'not-a-command-line-token'}
            vault = Mock()
            vault.get_secret_bundle.side_effect = lambda key, **kwargs: SimpleNamespace(data=SimpleNamespace(secret_bundle_content=SimpleNamespace(content=base64.b64encode(secrets[key].encode()).decode())))
            sdk = SimpleNamespace(secrets=SimpleNamespace(SecretsClient=Mock(return_value=vault)), retry=SimpleNamespace(NoneRetryStrategy=Mock(return_value=None)))
            captured = {}

            def child(command, **kwargs):
                captured.update(command=list(command), env=dict(kwargs['env']))
                return subprocess.CompletedProcess(command, 0)

            with patch.dict('sys.modules', {'oci': sdk}), patch('services.live_recoverable_agent.identity', return_value=({}, object(), None)), patch.object(run_talk_demo.socket, 'create_connection') as connection, patch.object(run_talk_demo.subprocess, 'run', side_effect=child):
                connection.return_value.__enter__.return_value = None
                code = run_talk_demo.main(['--output', str(root / 'new'), '--secret-references', str(refs), '--verifier-config', str(config), '--playwright-path', str(packages), '--record'])
            self.assertEqual(code, 0)
            self.assertEqual(connection.call_count, 3)
            command = captured['command']
            self.assertEqual(command[command.index('--cases') + 1], 'approve')
            self.assertIn('--headed', command)
            self.assertIn('--record-video', command)
            self.assertEqual(captured['env']['BENCH_PASSWORD'], secrets['admin-ref'])
            self.assertEqual(captured['env']['BENCH_SUPPORT_TOKEN'], secrets['support-ref'])
            for value in secrets.values():
                self.assertNotIn(value, ' '.join(command))
            self.assertEqual(vault.get_secret_bundle.call_count, 2)

    def test_pacing_guard_runs_before_browser_or_cloud_access(self):
        for delay, hold in [(-1, 0), (1001, 0), (0, -1), (0, 11)]:
            with self.subTest(delay=delay, hold=hold), self.assertRaisesRegex(ValueError, 'pacing_out_of_range'):
                browser_run(SimpleNamespace(slow_mo_ms=delay, demo_hold_s=hold))


if __name__ == '__main__':
    unittest.main()
