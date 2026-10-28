import copy
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from benchlib.remote import create_bundle, deploy_hosts, host_target, prepare_config, reject_embedded_secrets, shell_command


class RemoteTests(unittest.TestCase):
    def config_and_environment(self):
        image = 'registry/example@sha256:' + 'a' * 64
        config = {'campaign': {'region': 'us-phoenix-1', 'compartment_id': 'compartment'}, 'artifact': {'image': image, 'services_image': image, 'k6_image': image, 'otel_image': image, 'javaagent_bundled': True}, 'runtime': {'jdbc_url': 'jdbc:postgresql://10.1.2.4/db', 'allow_insecure_http': True}}
        environment = {'region': 'us-phoenix-1', 'compartment_id': 'compartment', 'roles': {name: {'private_ip': ip, 'user': 'ubuntu'} for name, ip in [('app', '10.1.2.1'), ('support', '10.1.2.2'), ('loadgen', '10.1.2.3')]}, 'base_url': 'http://10.1.2.1:8080/engine-rest', 'support_url': 'http://10.1.2.2:8090'}
        return config, environment

    def test_targets_reject_public_loopback_and_shell_injection(self):
        for address in ('8.8.8.8', '127.0.0.1', '169.254.169.254', '10.0.0.1;echo bad', '::1'):
            with self.assertRaises(ValueError):
                host_target({'private_ip': address, 'user': 'ubuntu'})
        with self.assertRaises(ValueError):
            host_target({'private_ip': '10.1.2.3', 'user': 'ubuntu;id'})
        self.assertEqual(host_target({'private_ip': '10.1.2.3', 'user': 'ubuntu'}), 'ubuntu@10.1.2.3')

    def test_remote_argument_quoting_preserves_literal_metacharacters(self):
        args = ['python3', '-c', 'print("$(whoami); not shell")', 'path with spaces']
        self.assertEqual(shlex.split(shell_command(args)), args)

    def test_secrets_rejected_but_references_allowed(self):
        reject_embedded_secrets({'password_env': 'BENCH_PASSWORD', 'password_secret_id': 'ocid1.vaultsecret.oc1.example'})
        for data in ({'runtime': {'password': 'secret'}}, {'runtime': {'support_token': 'secret'}}, {'headers': {'Authorization': 'Bearer secret'}}, {'dsn': 'host=example password=secret'}, {'url': 'https://u:p@example.com/'}, {'data': '-----BEGIN PRIVATE KEY-----'}):
            with self.assertRaises(ValueError):
                reject_embedded_secrets(data)

    def test_config_checks_environment_identity_and_isolation(self):
        config, environment = self.config_and_environment()
        ready, targets = prepare_config(config, environment, '/opt/release')
        self.assertEqual(ready['runtime']['db_ca_file'], '/opt/release/deploy/db-ca.pem')
        self.assertEqual(len(targets), 3)
        self.assertEqual(ready['runtime']['otlp_endpoint'], 'http://10.1.2.2:4318')
        self.assertEqual(ready['campaign']['access_method'], 'instance_principal')
        self.assertEqual(ready['runtime']['worker_support_url'], 'http://benchmark-support:8090')
        self.assertEqual(ready['runtime']['support_url'], environment['support_url'])
        wrong = copy.deepcopy(environment)
        wrong['roles']['loadgen']['private_ip'] = wrong['roles']['app']['private_ip']
        with self.assertRaises(ValueError):
            prepare_config(config, wrong, '/opt/release')

    def test_bundle_excludes_local_credentials_and_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('benchlib/cli.py', 'scripts/k6-container.sh', 'requirements-cloud.txt', 'requirements-database.txt'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('# source\n')
            for name in ('.tools/secret', 'runs/results.json', 'config.local.json', 'services/private.pem', 'services/__pycache__/cached.pyc', 'infra/.terraform/state.json', 'infra/terraform.tfvars', 'infra/terraform.tfstate', 'infra/terraform.tfstate.backup'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('DO NOT PACKAGE')
            ca = root / 'public-ca.pem'
            ca.write_text('-----BEGIN CERTIFICATE-----\npublic\n-----END CERTIFICATE-----\n')
            archive = root / 'bundle.zip'
            result = create_bundle(root, archive, {'runtime': {'password_secret_id': 'ocid-reference'}}, ca)
            with zipfile.ZipFile(archive) as package:
                self.assertEqual(set(package.namelist()), {'benchlib/cli.py', 'scripts/k6-container.sh', 'requirements-cloud.txt', 'requirements-database.txt', 'config.ready.json', 'deploy/db-ca.pem'})
                self.assertNotIn('DO NOT PACKAGE', ''.join(package.read(n).decode() for n in package.namelist()))
            self.assertEqual(result['file_count'], 6)
            self.assertEqual(len(result['sha256']), 64)

    def test_optional_worker_image_must_be_immutable(self):
        config, environment = self.config_and_environment()
        config['artifact']['worker_image'] = 'registry/worker:latest'
        with self.assertRaisesRegex(ValueError, 'artifact.worker_image'):
            prepare_config(config, environment, '/opt/release')
        config['artifact']['worker_image'] = 'registry/worker@sha256:' + 'b' * 64
        ready, _ = prepare_config(config, environment, '/opt/release')
        self.assertEqual(ready['artifact']['worker_image'], config['artifact']['worker_image'])

    def test_private_ca_paths_are_rewritten_and_packaged(self):
        config, environment = self.config_and_environment()
        config['runtime']['ca_file'] = 'controller-engine-ca.pem'
        config['runtime']['support_ca_file'] = 'controller-support-ca.pem'
        ready, _ = prepare_config(config, environment, '/opt/release')
        self.assertEqual(ready['runtime']['ca_file'], '/opt/release/deploy/engine-ca.pem')
        self.assertEqual(ready['runtime']['support_ca_file'], '/opt/release/deploy/support-ca.pem')
        self.assertEqual(config['runtime']['ca_file'], 'controller-engine-ca.pem')

    def test_https_support_keeps_its_certificate_hostname(self):
        config, environment = self.config_and_environment()
        environment['support_url'] = 'https://10.1.2.2:8090'
        ready, _ = prepare_config(config, environment, '/opt/release')
        self.assertNotIn('worker_support_url', ready['runtime'])
        self.assertEqual(ready['runtime']['support_url'], environment['support_url'])

    def test_remote_deploy_order_and_fail_stop_with_no_network(self):
        config, environment = self.config_and_environment()
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if '--role app' in argv[-1]:
                return type('Result', (), {'returncode': 1})()
            return type('Result', (), {'returncode': 0})()
        with tempfile.TemporaryDirectory() as temporary, patch('benchlib.remote.ROOT', Path(temporary)), patch('benchlib.remote.shutil.which', return_value='/usr/bin/tool'), patch('benchlib.remote.create_bundle', return_value={'sha256': 'a' * 64, 'file_count': 5}), patch('benchlib.remote.subprocess.run', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'Remote app step failed'):
                deploy_hosts(config, environment)
            inventories = list(Path(temporary).glob('dist/remote-*/inventory.json'))
            self.assertEqual(len(inventories), 1)
            self.assertEqual(json.loads(inventories[0].read_text())['status'], 'failed')
        commands = [argv[-1] for argv in calls if argv[0] == 'ssh']
        self.assertTrue(any('--role telemetry' in value for value in commands))
        self.assertFalse(any('--role worker' in value for value in commands))
        self.assertFalse(any('10.1.2.3' in arg for argv in calls for arg in argv))
        self.assertLess(next(i for i, c in enumerate(commands) if '--role support' in c), next(i for i, c in enumerate(commands) if '--role app' in c))
        self.assertTrue(any('Bundle checksum mismatch' in value for value in commands))
        for argv in calls:
            self.assertIn('StrictHostKeyChecking=yes', argv)
            self.assertIn('BatchMode=yes', argv)


if __name__ == '__main__':
    unittest.main()
