"""Install a reviewed source bundle on explicit private OCI role hosts.

SSH keys, verified known_hosts entries and Docker registry authentication are
operator prerequisites. This module never discovers arbitrary SSH targets.
"""
from __future__ import annotations
import copy
import ipaddress
import json
from pathlib import Path
import re
import shlex
import shutil
import ssl
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

from .util import ROOT, sha256, utc_now, write_json

WORKSPACE = '/opt/fluxnova-bench/workspace'
PYTHON = '/opt/fluxnova-bench/venv/bin/python'
SSH_OPTIONS = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15']
SOURCE_FOLDERS = ('benchlib', 'schemas', 'load', 'workloads', 'services', 'deploy', 'scripts', 'infra', 'docs', 'telemetry')
ROOT_FILES = ('README.md', 'pyproject.toml', 'bench.sh', 'bench.ps1', 'requirements-cloud.txt', 'requirements-database.txt', '.dockerignore')
RFC1918 = tuple(ipaddress.ip_network(value) for value in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def host_target(role):
    try:
        address = ipaddress.IPv4Address(role['private_ip'])
    except (KeyError, ValueError, ipaddress.AddressValueError) as error:
        raise ValueError('Each role must contain a concrete private IPv4 address') from error
    if not any(address in network for network in RFC1918):
        raise ValueError('SSH targets must use RFC1918 private IPv4 addresses')
    user = role.get('user', 'ubuntu')
    if not isinstance(user, str) or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', user):
        raise ValueError('Invalid SSH username')
    return f'{user}@{address}'


def shell_command(argv, cwd=None):
    """Only the remote POSIX shell needs quoting; local SSH receives an argv."""
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(x, str) and '\x00' not in x for x in argv):
        raise ValueError('Remote command must be an argv of strings without NUL')
    command = shlex.join(argv)
    return ('cd ' + shlex.quote(cwd) + ' && ' + command) if cwd else command


def reject_embedded_secrets(value, path='config'):
    """Fail closed for credential values; Vault IDs and environment names are valid."""
    forbidden = {'password', 'auth_password', 'token', 'private_key', 'ssh_private_key', 'access_token', 'api_key', 'client_secret', 'secret_access_key'}
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            is_reference = normalized.endswith(('_env', '_secret_id', '_secret_version')) or normalized == 'secret_version'
            sensitive = normalized in forbidden or normalized == 'authorization' or normalized.endswith(('_password', '_token', '_secret', '_api_key', '_private_key'))
            if sensitive and not is_reference:
                raise ValueError(f'{path}.{key} cannot be packaged; use a Vault ID or environment-variable reference')
            reject_embedded_secrets(child, path + '.' + str(key))
    elif isinstance(value, list):
        for child in value:
            reject_embedded_secrets(child, path)
    elif isinstance(value, str):
        if 'PRIVATE KEY-----' in value or re.search(r'(?i)\bpassword\s*=', value):
            raise ValueError(f'{path} contains an embedded credential')
        if '://' in value:
            parsed = urllib.parse.urlsplit(value)
            if parsed.username or parsed.password:
                raise ValueError(f'{path} contains credential-bearing URL')


def prepare_config(config, environment, remote_root):
    ready = copy.deepcopy(config)
    for key in ('region', 'compartment_id'):
        if environment.get(key) != config.get('campaign', {}).get(key):
            raise ValueError('Environment ' + key + ' does not match the reviewed configuration')
    roles = environment.get('roles', {})
    if any(name not in roles for name in ('app', 'support', 'loadgen')):
        raise ValueError('Environment must contain app, support and loadgen role inventories')
    targets = {name: host_target(roles[name]) for name in ('app', 'support', 'loadgen')}
    if len(set(target.split('@')[1] for target in targets.values())) != 3:
        raise ValueError('Application, support and load-generator hosts must be separate')
    for name, role_name in (('base_url', 'app'), ('support_url', 'support')):
        endpoint = environment.get(name, '')
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme not in ('http', 'https') or parsed.hostname != roles[role_name]['private_ip'] or parsed.username or parsed.password:
            raise ValueError('Environment ' + name + ' must identify its explicit private role host without credentials')
        if parsed.scheme == 'http' and not ready.get('runtime', {}).get('allow_insecure_http', False):
            raise ValueError('Private HTTP requires runtime.allow_insecure_http=true for the isolated lab; configure HTTPS otherwise')
        ready['runtime'][name] = endpoint
    # The built-in support container listens on HTTP 8090. Its co-located worker
    # uses Docker DNS; routing through the VM's published port can hit INPUT
    # firewall rejection on OCI images. Preserve TLS hostnames for HTTPS setups.
    if urllib.parse.urlsplit(ready['runtime']['support_url']).scheme == 'http':
        ready['runtime']['worker_support_url'] = 'http://benchmark-support:8090'
    image_keys = ('image', 'services_image', 'k6_image') + (('otel_image',) if ready['runtime'].get('telemetry_enabled', True) else ())
    if 'worker_image' in ready.get('artifact', {}):
        image_keys += ('worker_image',)
    for key in image_keys:
        if not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', ready.get('artifact', {}).get(key, '')):
            raise ValueError('artifact.' + key + ' must be a published immutable image reference')
    if not ready['runtime'].get('jdbc_url'):
        raise ValueError('Populate runtime.jdbc_url with the verified database endpoint before remote installation')
    # The inventory explicitly selects the principal provisioned for these VMs;
    # never copy a controller's OCI profile/key to a role host.
    if environment.get('access_method', 'instance_principal') != 'instance_principal':
        raise ValueError('Remote OCI roles require the reviewed instance-principal access method')
    ready['campaign']['access_method'] = 'instance_principal'
    if ready['runtime'].get('telemetry_enabled', True):
        if ready['artifact'].get('javaagent_bundled') is not True:
            raise ValueError('Remote telemetry requires artifact.javaagent_bundled=true from the pinned image build')
        if not ready['runtime'].get('otlp_endpoint'):
            ready['runtime']['otlp_endpoint'] = 'http://' + roles['support']['private_ip'] + ':4318'
        otlp = urllib.parse.urlsplit(ready['runtime']['otlp_endpoint'])
        if otlp.scheme not in ('http', 'https') or otlp.hostname != roles['support']['private_ip'] or otlp.username or otlp.password:
            raise ValueError('runtime.otlp_endpoint must identify the explicit support host without credentials')
    ready['runtime']['db_ca_file'] = remote_root + '/deploy/db-ca.pem'
    for field, name in (('ca_file', 'engine-ca.pem'), ('support_ca_file', 'support-ca.pem')):
        if ready['runtime'].get(field):
            ready['runtime'][field] = remote_root + '/deploy/' + name
    ready.setdefault('execution', {})['k6_binary'] = remote_root + '/scripts/k6-container.sh'
    ready.setdefault('execution', {})['k6_image_env'] = 'BENCH_K6_IMAGE'
    reject_embedded_secrets(ready)
    return ready, targets


def public_certificate(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError('Certificate path must identify a public CA certificate on the controller')
    certificate = path.read_bytes()
    if b'-----BEGIN CERTIFICATE-----' not in certificate or b'PRIVATE KEY-----' in certificate:
        raise ValueError('CA input must contain public certificates, never a private key')
    return certificate


def create_bundle(source_root, archive_path, ready_config, ca_file, extra_cas=None):
    source_root, archive_path, ca_file = Path(source_root), Path(archive_path), Path(ca_file)
    if not ca_file.is_file():
        raise ValueError('runtime.db_ca_file must identify a public PostgreSQL CA certificate on the controller')
    certificate = public_certificate(ca_file)
    reject_embedded_secrets(ready_config)
    files = {}
    for folder in SOURCE_FOLDERS:
        for path in sorted((source_root / folder).rglob('*')):
            relative = path.relative_to(source_root)
            if path.is_symlink() or not path.is_file():
                continue
            if any(part in ('__pycache__', '.terraform', '.git', '.tools', 'runs') or (part.startswith('.') and part != '.terraform.lock.hcl') for part in relative.parts):
                continue
            if path.suffix.lower() in ('.pyc', '.pem', '.key', '.log', '.zip', '.tfstate', '.tfvars') or path.name.endswith(('.local.json', '.tfstate.backup', '.tfvars.json')):
                continue
            files[relative.as_posix()] = path.read_bytes()
    for name in ROOT_FILES:
        path = source_root / name
        if path.is_file() and not path.is_symlink():
            files[name] = path.read_bytes()
    for required in ('benchlib/cli.py', 'scripts/k6-container.sh', 'requirements-cloud.txt', 'requirements-database.txt'):
        if required not in files:
            raise ValueError('Missing required source bundle file: ' + required)
    files['config.ready.json'] = (json.dumps(ready_config, indent=2, sort_keys=True) + '\n').encode()
    files['deploy/db-ca.pem'] = certificate
    for field, filename in (('ca_file', 'engine-ca.pem'), ('support_ca_file', 'support-ca.pem')):
        if extra_cas and extra_cas.get(field):
            files['deploy/' + filename] = public_certificate(extra_cas[field])
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return {'sha256': sha256(archive_path), 'file_count': len(files)}


def wait_ready(config, role, timeout_s=180):
    """Called on the role host; prove HTTP readiness, not business correctness."""
    if role == 'app':
        from .deployment import secret
        from .fluxnova import FluxnovaClient
        password = secret(config, config['runtime']['admin_password_secret_id'])
        client = FluxnovaClient(config['runtime']['base_url'], config['runtime'].get('admin_username', 'benchmark'), password, config['runtime'].get('ca_file'))
        probe = client.readiness
    elif role == 'support':
        def probe():
            context = ssl.create_default_context(cafile=config['runtime'].get('support_ca_file'))
            with urllib.request.urlopen(config['runtime']['support_url'].rstrip('/') + '/health', timeout=10, context=context) as response:
                result = json.loads(response.read())
            if result.get('status') != 'ok':
                raise RuntimeError('Support health did not return ok')
            return result
    elif role == 'telemetry':
        def probe():
            with urllib.request.urlopen('http://127.0.0.1:13133/health', timeout=10) as response:
                if response.status != 200:
                    raise RuntimeError('Telemetry health did not return HTTP 200')
            return {'status': 'ok', 'scope': 'collector-health-only'}
    else:
        raise ValueError('Readiness is defined for app, support and telemetry')
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            return probe()
        except (OSError, RuntimeError, ValueError):
            time.sleep(2)
    raise RuntimeError(role + ' readiness timed out; inspect retained container logs before retrying')


def deploy_hosts(config, environment):
    """Copy/install on only the three targets in the reviewed Terraform output."""
    identity = uuid.uuid4().hex
    remote_root = WORKSPACE + '/release-' + identity
    ready, targets = prepare_config(config, environment, remote_root)
    for tool in ('ssh', 'scp'):
        if shutil.which(tool) is None:
            raise RuntimeError(tool + ' is required on the controller')
    evidence = ROOT / 'dist' / ('remote-' + identity)
    evidence.mkdir(parents=True, exist_ok=False)
    inventory = {'created_at': utc_now(), 'status': 'preparing', 'remote_root': remote_root, 'hosts': {}, 'commands_executed': False,
                 'limitations': ['operator-established verified SSH known_hosts and per-host Docker login are prerequisites', 'runtime readiness is not workflow correctness; run database-check and golden before load']}
    inventory_path = evidence / 'inventory.json'
    write_json(inventory_path, inventory)
    steps = 0

    def execute(role, argv):
        nonlocal steps
        steps += 1
        log = evidence / f'{steps:02d}-{role}.log'
        inventory['commands_executed'] = True
        write_json(inventory_path, inventory)
        with log.open('w', encoding='utf-8') as stream:
            result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, text=True, timeout=1800)
        if result.returncode:
            raise RuntimeError(f'Remote {role} step failed; inspect {log}. Resources and prior evidence are preserved.')

    def ssh(role, argv, cwd=None):
        execute(role, ['ssh', *SSH_OPTIONS, targets[role], shell_command(argv, cwd)])

    try:
        archive = evidence / ('bundle-' + identity + '.zip')
        inventory['bundle'] = create_bundle(ROOT, archive, ready, config['runtime'].get('db_ca_file', ''), config['runtime'])
        remote_zip = WORKSPACE + '/' + archive.name
        for role in ('support', 'app', 'loadgen'):
            inventory['hosts'][role] = {'target': targets[role], 'status': 'preparing', 'directory': remote_root}
            write_json(inventory_path, inventory)
            ssh(role, ['cloud-init', 'status', '--wait'])
            ssh(role, ['test', '-f', '/opt/fluxnova-bench/bootstrap-complete'])
            ssh(role, ['test', '-x', PYTHON])
            ssh(role, ['test', '-d', WORKSPACE])
            execute(role, ['scp', *SSH_OPTIONS, str(archive.resolve()), targets[role] + ':' + remote_zip])
            verify = "import hashlib,pathlib,sys; assert hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()==sys.argv[2], 'Bundle checksum mismatch'"
            ssh(role, ['python3', '-c', verify, remote_zip, inventory['bundle']['sha256']])
            # The UUID directory must be absent; never overwrite another checkout.
            command = 'test ! -e ' + shlex.quote(remote_root) + ' && ' + shell_command(['python3', '-m', 'zipfile', '-e', remote_zip, remote_root])
            execute(role, ['ssh', *SSH_OPTIONS, targets[role], command])
            ssh(role, [PYTHON, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', 'requirements-cloud.txt', '-r', 'requirements-database.txt'], remote_root)
            ssh(role, ['chmod', '0755', 'scripts/k6-container.sh', 'bench.sh'], remote_root)
            if role in ('app', 'support'):
                ssh(role, [PYTHON, '-m', 'benchlib.cli', 'deploy', '--config', 'config.ready.json', '--runtime', '--role', role], remote_root)
                code = "import json; from benchlib.remote import wait_ready; print(json.dumps(wait_ready(json.load(open('config.ready.json')), '" + role + "')))"
                ssh(role, [PYTHON, '-c', code], remote_root)
                if role == 'support':
                    if ready['runtime'].get('telemetry_enabled', True):
                        ssh(role, [PYTHON, '-m', 'benchlib.cli', 'deploy', '--config', 'config.ready.json', '--runtime', '--role', 'telemetry'], remote_root)
                        code = "import json; from benchlib.remote import wait_ready; print(json.dumps(wait_ready(json.load(open('config.ready.json')), 'telemetry')))"
                        ssh(role, [PYTHON, '-c', code], remote_root)
                        inventory['hosts'][role]['collector_http_ready'] = True
                else:
                    ssh('support', [PYTHON, '-m', 'benchlib.cli', 'deploy', '--config', 'config.ready.json', '--runtime', '--role', 'worker'], remote_root)
                    inventory['hosts']['support']['worker_started'] = True
            else:
                ssh(role, ['docker', 'pull', ready['artifact']['k6_image']], remote_root)
            inventory['hosts'][role].update(status='prepared', bootstrap_complete=True, bundle_checksum_verified=True,
                                          runtime_http_ready=role in ('app', 'support'), workflow_correctness_verified=False)
            write_json(inventory_path, inventory)
        inventory.update(status='completed', completed_at=utc_now(), next_steps={
            'host': targets['loadgen'], 'directory': remote_root,
            'export_k6_image': 'export BENCH_K6_IMAGE=' + shlex.quote(ready['artifact']['k6_image']),
            'instructions': 'Set BENCH_USERNAME/BENCH_PASSWORD/BENCH_SUPPORT_TOKEN using approved secret retrieval. Run database-check on the controller, deploy BPMN, then golden and campaign from the load generator; archive all evidence.'})
    except Exception as error:
        inventory.update(status='failed', error_type=type(error).__name__, finished_at=utc_now())
        raise
    finally:
        write_json(inventory_path, inventory)
    return {'inventory': str(inventory_path), **inventory}
