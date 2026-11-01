#!/usr/bin/env python3
"""Qualify FNV-06 in an owned controller stage with fresh OCI PostgreSQL.

Allocation, database deletion and source-preservation checks are separate guarded
operator actions. This runner never starts a performance campaign.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time


def read(path): return json.loads(Path(path).read_text())
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    Path(path).chmod(0o600)


def job():
    """Runs only inside the private qualification container."""
    sys.path.insert(0, '/source')
    from benchlib.fluxnova import FluxnovaClient
    from benchlib.fnv06_cloud import require_stage
    from benchlib.fnv06_functional import Recorder, exercise
    source, output = Path('/source'), Path('/evidence')
    meta, inventory = read('/run/identity.json'), read('/run/source-manifest.json')
    require_stage(meta, meta['stage'])
    assert not any(output.iterdir()), 'Retain every attempt; evidence directory must be empty'
    for name, digest in inventory['files'].items():
        original = source / name
        assert original.resolve().is_relative_to(source) and sha(original) == digest
        target = output / 'inputs' / name; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original.read_bytes())
    corpus = read(output / 'inputs/workloads/fnv06/cases.json')
    scope = 'OCI MANAGED POSTGRESQL FUNCTIONAL QUALIFICATION - NOT PERFORMANCE'
    manifest = {'study_id': 'FNV-06', 'scope': scope, 'run_id': meta['run_id'],
                'runtime': read('/run/runtime.json'), 'database': 'OCI managed PostgreSQL',
                'functional_batch_wait_timeout_s': 120, 'batch_poll_interval_s': .5,
                'source_sha256': inventory['files'], 'source_commit': meta['source_commit'],
                'model_calls': 0, 'cloud_resources_accessed': True,
                'performance_comparison_complete': False,
                'design': 'Same fixed functional corpus as local qualification. No warmup, offered-load or performance comparison.',
                'authority_scope': 'Synthetic message, signal and batch routing in an isolated owned engine.'}
    save(output / 'manifest.json', manifest)
    summary = {'study_id': 'FNV-06', 'scope': scope, 'run_id': meta['run_id'], 'status': 'running',
               'cases_collected': [], 'owned_process_ids': [], 'owned_batch_ids': [],
               'model_calls': 0, 'cloud_resources_accessed': True,
               'performance_comparison_complete': False, 'owned_engine_stopped': False, 'cleanup_errors': []}
    url, user, password = 'http://engine:8080/engine-rest', 'fnv06bench', os.environ['BENCH_PASSWORD']
    engine = FluxnovaClient(url, user, password)
    class BoundedRecorder(Recorder):
        def call(self, *args, **kwargs):
            require_stage(meta, meta['stage'])
            return super().call(*args, **kwargs)
    api = BoundedRecorder(engine, output)
    try:
        save(output / 'engine-version.json', engine.readiness())
        exercise(api, corpus, meta['run_id'], user, summary)
        summary['status'] = 'collected_pending_database_snapshot'
    except BaseException as error:
        summary.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        save(output / 'summary.json', summary)


class Cell:
    def __init__(self, stage, *, cleanup_only=False):
        self.stage = stage.resolve(); self.meta = read(self.stage / 'identity.json')
        sys.path.insert(0, str(self.stage / 'source'))
        from benchlib.fnv06_cloud import require_stage
        self.run_id = require_stage(self.meta, self.stage, require_time=not cleanup_only)
        self.prefix = 'fnv06-' + self.run_id[:12]
        self.names = {k: self.prefix + '-' + k for k in ('engine', 'job')}
        self.network, self.label = self.prefix, 'fnv06_id=' + self.run_id
        self.output = self.stage / 'functional'
        self.dbhost = self.meta['connection']['primary_db_endpoint']['fqdn']
        self.dbuser = 'fnv06_' + self.run_id[:12]
        self.ca = self.stage / 'db-ca.pem'
        self.forward = None
        if (self.stage / 'credentials.local.json').exists():
            self.credentials = read(self.stage / 'credentials.local.json')

    def bound(self):
        from benchlib.fnv06_cloud import require_stage
        require_stage(self.meta, self.stage)
        assert not (self.stage / 'cancel').exists()

    def command(self, args, *, env=None, timeout=120):
        result = subprocess.run(args, env=env, capture_output=True, timeout=timeout)
        if result.returncode:
            # Command output is private; never echo passwords from an engine log.
            path = self.stage / ('command-' + str(time.time_ns()) + '.stderr')
            path.write_bytes(result.stderr); path.chmod(0o600)
            raise RuntimeError('owned_command_failed:' + args[0])
        return result.stdout

    def docker(self, *args, **kwargs): return self.command(['docker', *args], **kwargs)

    def inspect(self, role):
        value = json.loads(self.docker('inspect', self.names[role]))[0]
        assert value['Config']['Labels'].get('fnv06_id') == self.run_id
        return value

    def envopts(self, values):
        env = os.environ.copy(); env.update({k: str(v) for k, v in values.items()})
        return env, [part for key in values for part in ('--env', key)]

    def database(self, admin=False):
        import psycopg
        password, user = self.credentials['database'], self.dbuser
        if admin:
            import oci
            c = oci.secrets.SecretsClient({'region': 'us-phoenix-1'},
                signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner(), timeout=(10, 30))
            password = base64.b64decode(c.get_secret_bundle(self.meta['admin_secret_id'],
                version_number=int(self.meta['admin_secret_version'])).data.secret_bundle_content.content).decode()
            user = self.meta['admin_username']
        return psycopg.connect(host=self.dbhost, dbname='postgres', user=user, password=password,
            sslmode='verify-full', sslrootcert=str(self.ca), connect_timeout=10, autocommit=True)

    def setup(self):
        from psycopg import sql
        import secrets
        self.bound()
        inventory = read(self.stage / 'source-manifest.json')
        assert sha(self.stage / 'runtime.py') == inventory['runtime_sha256']
        assert sha(self.stage / 'fixed_db_forward.py') == inventory['forward_sha256']
        for name, digest in inventory['files'].items():
            path = self.stage / 'source' / name
            assert path.resolve().is_relative_to(self.stage / 'source') and sha(path) == digest
        assert not (self.stage / 'credentials.local.json').exists() and not self.output.exists()
        self.credentials = {k: secrets.token_urlsafe(32) for k in ('admin', 'database')}
        save(self.stage / 'credentials.local.json', self.credentials)
        self.ca.write_text(self.meta['connection']['ca_certificate']); self.ca.chmod(0o644)
        for image in self.meta['images'].values(): self.docker('image', 'inspect', image)
        with self.database(True) as db:
            assert db.execute("SELECT count(*) FROM pg_namespace WHERE nspname='fluxnova'").fetchone()[0] == 0
            db.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Identifier(self.dbuser), sql.Literal(self.credentials['database'])))
            db.execute('CREATE SCHEMA fluxnova')
            db.execute(sql.SQL('GRANT USAGE,CREATE ON SCHEMA fluxnova TO {}').format(sql.Identifier(self.dbuser)))
            db.execute(sql.SQL('ALTER ROLE {} SET search_path TO fluxnova,public').format(sql.Identifier(self.dbuser)))
        self.docker('network', 'create', '--internal', '--label', self.label, self.network)
        network = json.loads(self.docker('network', 'inspect', self.network))[0]
        assert network['Internal'] and network['Labels'].get('fnv06_id') == self.run_id
        gateway = network['IPAM']['Config'][0]['Gateway']
        rule = ['-i', 'br-' + network['Id'][:12], '-s', network['IPAM']['Config'][0]['Subnet'], '-d', gateway,
                '-p', 'tcp', '--dport', '5432', '-m', 'comment', '--comment', self.network, '-j', 'ACCEPT']
        assert subprocess.run(['iptables', '-C', 'INPUT', *rule], capture_output=True).returncode == 1
        save(self.stage / 'owned-network.json', {'id': network['Id'], 'rule': rule})
        self.command(['iptables', '-I', 'INPUT', '1', *rule])
        save(self.stage / 'fixed-forward-binding.json', {'run_id': self.run_id, 'target_id': self.meta['target_id'], 'host': self.dbhost, 'gateway': gateway})
        with (self.stage / 'fixed-forward.log').open('xb') as log:
            self.forward = subprocess.Popen([sys.executable, str(self.stage / 'fixed_db_forward.py')],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        save(self.stage / 'forward.json', {'pid': self.forward.pid, 'path': str(self.stage / 'fixed_db_forward.py')})
        end = time.monotonic() + 20
        while True:
            assert self.forward.poll() is None
            try:
                with socket.create_connection((gateway, 5432), timeout=1): break
            except OSError:
                assert time.monotonic() < end; time.sleep(.5)
        common = ['--label', self.label, '--network', self.network, '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges']
        env, options = self.envopts({'SPRING_DATASOURCE_URL': 'jdbc:postgresql://' + self.dbhost + ':5432/postgres?currentSchema=fluxnova&sslmode=verify-full&sslrootcert=/run/db-ca.pem',
            'SPRING_DATASOURCE_USERNAME': self.dbuser, 'SPRING_DATASOURCE_PASSWORD': self.credentials['database'],
            'SPRING_DATASOURCE_DRIVER_CLASS_NAME': 'org.postgresql.Driver', 'SPRING_DATASOURCE_HIKARI_MAXIMUM_POOL_SIZE': '10',
            'FLUXNOVA_BPM_RUN_AUTH_ENABLED': 'true', 'FLUXNOVA_BPM_AUTHORIZATION_ENABLED': 'true',
            'FLUXNOVA_BPM_HISTORY_LEVEL': 'full', 'FLUXNOVA_BPM_ADMIN_USER_ID': 'fnv06bench',
            'FLUXNOVA_BPM_ADMIN_USER_PASSWORD': self.credentials['admin'], 'JAVA_OPTS': '-Xms512m -Xmx2g -Duser.timezone=UTC', 'FLUXNOVA_BPM_RUN_EXAMPLE_ENABLED': 'false'})
        self.docker('run', '-d', '--name', self.names['engine'], *common, '--network-alias', 'engine', '--add-host', self.dbhost + ':' + gateway,
            '--memory', '4g', '--cpus', '2', '--pids-limit', '256', '--mount', 'type=bind,src=' + str(self.ca) + ',dst=/run/db-ca.pem,readonly',
            *options, self.meta['images']['engine'], env=env)
        from benchlib.fluxnova import FluxnovaClient
        address = self.inspect('engine')['NetworkSettings']['Networks'][self.network]['IPAddress']
        engine = FluxnovaClient('http://' + address + ':8080/engine-rest', 'fnv06bench', self.credentials['admin'])
        end = time.monotonic() + 300
        while True:
            self.bound()
            try: version = engine.readiness(); break
            except (OSError, RuntimeError):
                assert time.monotonic() < end; time.sleep(2)
        assert version['version'] == '3.0.1'
        with self.database() as db:
            tls = db.execute('SELECT s.ssl,s.version FROM pg_stat_activity a JOIN pg_stat_ssl s USING(pid) WHERE a.usename=%s', (self.dbuser,)).fetchall()
            assert len(tls) > 1 and all(r[0] for r in tls)
            postgres = db.execute('SELECT version()').fetchone()[0]
        runtime = {'engine': version, 'database_version': postgres, 'database_tls': tls,
                   'jdbc_tls_verify_full': True, 'engine_image': self.meta['images']['engine'],
                   'services_image': self.meta['images']['services'], 'internal_network': True,
                   'jvm_timezone': 'UTC', 'database_role': self.dbuser}
        save(self.stage / 'runtime.json', runtime)
        self.output.mkdir(mode=0o700); os.chown(self.output, 65532, 65532)
        # Exact metadata mounts are readable only inside the isolated job.
        for name in ('identity.json', 'source-manifest.json', 'runtime.json'):
            os.chown(self.stage / name, 65532, 65532)
        mounts = ['--mount', 'type=bind,src=' + str(self.stage / 'source') + ',dst=/source,readonly',
                  '--mount', 'type=bind,src=' + str(self.output) + ',dst=/evidence']
        for name in ('identity.json', 'source-manifest.json', 'runtime.json'):
            mounts.extend(['--mount', 'type=bind,src=' + str(self.stage / name) + ',dst=/run/' + name + ',readonly'])
        env, options = self.envopts({'BENCH_PASSWORD': self.credentials['admin'], 'PYTHONDONTWRITEBYTECODE': '1'})
        self.docker('create', '--name', self.names['job'], *common, '--user', '65532:65532', '--read-only',
            '--memory', '512m', '--cpus', '1', '--pids-limit', '64', *mounts, '--workdir', '/source', *options,
            self.meta['images']['services'], 'python', 'scripts/run_fnv06_cloud.py', 'job', env=env)
        # Resolve outside the restricted network; DNS failure cannot count as isolation.
        targets = [(socket.gethostbyname(h), p) for h, p in self.meta['forbidden_endpoints']]
        probe = 'import socket,json;r=[]\nfor h,p in ' + repr(targets) + ':\n s=socket.socket();s.settimeout(1);v=s.connect_ex((h,p));s.close();assert v!=0;r.append({"port":p,"blocked":True})\nprint(json.dumps(r))'
        blocked = json.loads(self.docker('run', '--rm', *common, '--user', '65532:65532', '--read-only', '--memory', '64m',
            '--pids-limit', '16', self.meta['images']['services'], 'python', '-c', probe))
        save(self.stage / 'isolation.json', {'forbidden_endpoints_blocked': blocked, 'resolved_targets': targets})

    def snapshot_database(self):
        """Independent SQL read after collection and before any workflow cleanup."""
        from benchlib.fnv06_sql import capture
        self.bound()
        with self.database() as db:
            snapshot = capture(db, run_id=self.run_id, target_id=self.meta['target_id'],
                               expected_user=self.dbuser, bound=self.bound)
        save(self.output / 'database-before-cleanup.json', snapshot)

    def cleanup_workflows(self):
        """Expiry-safe cleanup restricted to recorded identities in this owned cell."""
        from benchlib.fluxnova import FluxnovaClient
        from benchlib.fnv06_cloud import require_stage
        from benchlib.fnv06_functional import Recorder
        require_stage(self.meta, self.stage, require_time=False)
        state = self.inspect('engine')
        assert state['State']['Running']
        address = state['NetworkSettings']['Networks'][self.network]['IPAddress']
        engine = FluxnovaClient('http://' + address + ':8080/engine-rest', 'fnv06bench', self.credentials['admin'])
        directory = self.output / 'cleanup'; directory.mkdir()
        api = Recorder(engine, directory); summary = read(self.output / 'summary.json')
        for endpoint, owned in (('/batch', summary['owned_batch_ids']),
                                ('/process-instance', summary['owned_process_ids'])):
            population = api.get(endpoint + '?maxResults=1000')['response']
            assert len(population) == api.get(endpoint + '/count')['response']['count']
            assert {r['id'] for r in population} <= set(owned), 'Unexpected cleanup owner'
            for row in population:
                suffix = '?cascade=false' if endpoint == '/batch' else '?deleteReason=fnv06-owned-cleanup'
                assert api.call('DELETE', endpoint + '/' + row['id'] + suffix)['status'] == 204
        counts = {path: api.get(path + '/count')['response']['count'] for path in
                  ('/process-instance', '/event-subscription', '/incident', '/external-task', '/batch')}
        save(self.output / 'final-engine-counts.json', counts)
        assert not any(counts.values()), 'Owned workflow cleanup incomplete'

    def cleanup(self):
        errors, containers = [], {}
        for role in ('job', 'engine'):
            try:
                present = subprocess.run(['docker', 'inspect', self.names[role]], capture_output=True)
                if present.returncode: continue
                state = self.inspect(role)
                if state['State']['Running']: self.docker('stop', '--time', '30', self.names[role], timeout=45)
                state = self.inspect(role)
                assert not state['State']['Running']
                containers[role] = {'id': state['Id'], 'label': self.run_id, 'stopped': True,
                                    'exit_code': state['State']['ExitCode'], 'oom_killed': state['State']['OOMKilled']}
                logs = subprocess.run(['docker', 'logs', self.names[role]], capture_output=True, timeout=30)
                assert logs.returncode == 0
                raw = logs.stdout + logs.stderr
                for value in getattr(self, 'credentials', {}).values(): raw = raw.replace(value.encode(), b'REDACTED')
                (self.stage / (role + '.log')).write_bytes(raw)
                self.docker('rm', self.names[role])
            except Exception as error: errors.append({'component': role, 'error_type': type(error).__name__})
        if self.forward and self.forward.poll() is None:
            try: self.forward.terminate(); self.forward.wait(timeout=15)
            except Exception as error: errors.append({'component': 'forward', 'error_type': type(error).__name__})
        elif (self.stage / 'forward.json').exists():
            try:
                forward = read(self.stage / 'forward.json'); proc = Path('/proc') / str(int(forward['pid']))
                if proc.exists():
                    argv = (proc / 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
                    assert len(argv) == 2 and argv[1].decode() == str(self.stage / 'fixed_db_forward.py') == forward['path']
                    os.kill(forward['pid'], signal.SIGTERM)
                    deadline = time.monotonic() + 15
                    while proc.exists():
                        assert time.monotonic() < deadline; time.sleep(.1)
            except Exception as error: errors.append({'component': 'forward', 'error_type': type(error).__name__})
        network_file = self.stage / 'owned-network.json'
        if network_file.exists():
            try:
                owned = read(network_file)
                found = subprocess.run(['docker', 'network', 'inspect', self.network], capture_output=True)
                network = json.loads(found.stdout)[0] if found.returncode == 0 else None
                if network:
                    assert network['Id'] == owned['id'] and network['Labels'].get('fnv06_id') == self.run_id
                code = subprocess.run(['iptables', '-C', 'INPUT', *owned['rule']], capture_output=True).returncode
                assert code in (0, 1)
                if code == 0: self.command(['iptables', '-D', 'INPUT', *owned['rule']])
                if network: self.docker('network', 'rm', self.network)
            except Exception as error: errors.append({'component': 'network', 'error_type': type(error).__name__})
        return {'at': datetime.now(timezone.utc).isoformat(), 'run_id': self.run_id, 'containers': containers, 'cleanup_errors': errors,
                'owned_engine_stopped': bool(containers.get('engine', {}).get('stopped')), 'database_deletion_separate': True}

    def run(self):
        self.bound(); failure = None
        try:
            self.setup(); self.bound(); self.docker('start', self.names['job'])
            while self.inspect('job')['State']['Running']:
                self.bound(); time.sleep(2)
            state = self.inspect('job')['State']
            assert state['ExitCode'] == 0 and not state['OOMKilled'], 'qualification_job_failed'
            self.snapshot_database()
            self.cleanup_workflows()
            summary = read(self.output / 'summary.json')
            assert summary['status'] == 'collected_pending_database_snapshot'
            summary['status'] = 'collected_pending_independent_audit'
            save(self.output / 'summary.json', summary)
        except BaseException as error:
            failure = error
        finally:
            closeout = self.cleanup(); save(self.stage / 'closeout.json', closeout)
            if self.output.exists():
                save(self.output / 'engine-closeout.json', closeout)
                for name in ('runtime.json', 'isolation.json', 'identity.json', 'source-manifest.json'):
                    if (self.stage / name).exists(): save(self.output / name, read(self.stage / name))
                if (self.output / 'summary.json').exists():
                    summary = read(self.output / 'summary.json')
                    summary.update(owned_engine_stopped=closeout['owned_engine_stopped'], cleanup_errors=closeout['cleanup_errors'])
                    if failure or closeout['cleanup_errors']: summary['status'] = 'failed'
                    save(self.output / 'summary.json', summary)
                save(self.output / 'checksums.json', {'algorithm': 'sha256', 'files': {p.relative_to(self.output).as_posix(): sha(p)
                    for p in sorted(self.output.rglob('*')) if p.is_file() and p.name != 'checksums.json'}})
            if failure: save(self.stage / 'failure.json', {'error_type': type(failure).__name__, 'reason': str(failure)[:200]})
        if failure: raise failure
        assert not closeout['cleanup_errors'] and closeout['owned_engine_stopped']
        print(json.dumps({'status': 'collected_pending_independent_audit', 'workflow_instances': 42,
                          'case_families': 12, 'performance_comparison_complete': False}), flush=True)


if __name__ == '__main__':
    def terminated(*_): raise RuntimeError('owned_qualification_terminated')
    signal.signal(signal.SIGTERM, terminated)
    if sys.argv[1:] == ['job']: job()
    elif sys.argv[1:] == ['run']: Cell(Path(__file__).resolve().parent).run()
    elif sys.argv[1:] == ['retire']:
        cell = Cell(Path(__file__).resolve().parent, cleanup_only=True)
        result = cell.cleanup(); save(cell.stage / 'retirement.json', result)
        print(json.dumps(result)); assert not result['cleanup_errors']
    else: raise SystemExit('Use run from an owned stage, or job inside the prepared isolated container.')
