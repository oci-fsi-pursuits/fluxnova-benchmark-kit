#!/usr/bin/env python3
"""Owned OCI PAY-05 offered-load qualification; no performance campaign.

Stage as runtime.py beside identity.json, source-manifest.json and source/.
The operator separately owns allocation, final source checks and DB deletion.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback

HERE = Path(__file__).resolve().parent
if (HERE / 'identity.json').is_file(): sys.path.insert(0, str(HERE / 'source'))
from scripts.run_pay05_cloud import Cell, read, save, sha
from benchlib.checkpoint import postgres_fingerprint, sqlite_fingerprint, sqlite_copy, restore_listing
from benchlib.evidence import calibrate_generator, linux_host_sample
from benchlib.fluxnova import FluxnovaClient
from benchlib.pay05_load import qualification_plan, assess_cloud
from benchlib.runner import extract_event, normalize_event
from benchlib.w02_cloud import require_matched_controls
from services.worker import Client


def utc(): return datetime.now(timezone.utc).isoformat()
def digest(value): return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def require_tools(meta):
    images = meta.get('tool_images', {})
    if set(images) != {'k6', 'postgres'} or any(not re.fullmatch(r'[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}', v) for v in images.values()):
        raise ValueError('PAY-05 requires pinned k6 and PostgreSQL tools')
    return images


class LoadCell(Cell):
    def __init__(self, stage, *, cleanup_only=False):
        super().__init__(stage, cleanup_only=cleanup_only)
        require_tools(self.meta)
        self.names.update({k: self.prefix + '-' + k for k in ('backend', 'worker', 'generator')})
        self.checkpoint = self.stage / 'checkpoint'
        self.backend = self.stage / 'backend/study.sqlite'
        self.configure()

    def configure(self):
        self.output = self.stage / 'qualification'
        self.corpus_path = self.stage / 'source/workloads/pay05/cases.json'
        self.corpus = read(self.corpus_path)
        self.plan = qualification_plan(self.corpus)

    def source_guard(self):
        manifest = read(self.stage / 'source-manifest.json')
        for name, expected in manifest['files'].items():
            path = self.stage / 'source' / name
            assert path.resolve().is_relative_to(self.stage / 'source') and sha(path) == expected
        for name, key in [('runtime.py', 'runtime_sha256'), ('fixed_db_forward.py', 'forward_sha256'), ('k6', 'k6_wrapper_sha256')]:
            assert sha(self.stage / name) == manifest[key]
        return manifest

    def clients(self):
        addresses = {k: self.inspect(k)['NetworkSettings']['Networks'][self.network]['IPAddress'] for k in ('engine', 'backend')}
        return (FluxnovaClient('http://' + addresses['engine'] + ':8080/engine-rest', 'pay05bench', self.credentials['admin']),
                Client('http://' + addresses['backend'] + ':28181', 'Bearer ' + self.credentials['registrar']))

    def ready(self):
        end = time.monotonic() + 300
        while True:
            self.bound()
            try:
                engine, support = self.clients()
                assert engine.readiness()['version'] == '3.0.1'
                support.request('GET', '/health'); return
            except (OSError, RuntimeError):
                assert time.monotonic() < end; time.sleep(2)

    def queues(self):
        engine = self.clients()[0]
        return {k: engine.request('GET', v)['count'] for k, v in {
            'active_instances': '/process-instance/count', 'external_tasks': '/external-task/count', 'incidents': '/incident/count'}.items()}

    def setup(self):
        self.source_guard()
        for image in self.meta['tool_images'].values(): self.docker('image', 'inspect', image)
        super().setup()
        assert not self.inspect('job')['State']['Running']
        self.docker('rm', self.names['job'])
        os.chown(self.output, 0, 0)
        self.credentials.update({k: secrets.token_urlsafe(32) for k in ('worker', 'registrar')})
        save(self.stage / 'credentials.local.json', self.credentials)
        self.backend.parent.mkdir(mode=0o700); os.chown(self.backend.parent, 65532, 65532)
        common = ['--label', self.label, '--network', self.network, '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                  '--user', '65532:65532', '--read-only', '--memory', '256m', '--pids-limit', '64',
                  '--mount', 'type=bind,src=' + str(self.stage / 'source') + ',dst=/source,readonly', '--workdir', '/source']
        env, options = self.envopts({'PAY05_WORKER_TOKEN': self.credentials['worker'], 'PAY05_REGISTRATION_TOKEN': self.credentials['registrar'], 'PYTHONDONTWRITEBYTECODE': '1'})
        self.docker('run', '-d', '--name', self.names['backend'], *common, '--network-alias', 'backend', '--cpus', '.25',
            '--mount', 'type=bind,src=' + str(self.backend.parent) + ',dst=/data', *options, self.meta['images']['services'],
            'python', '-m', 'services.fx_quotes', '--host', '0.0.0.0', '--database', '/data/study.sqlite', env=env)
        env, options = self.envopts({'BENCH_USERNAME': 'pay05bench', 'BENCH_PASSWORD': self.credentials['admin'],
            'PAY05_WORKER_TOKEN': self.credentials['worker'], 'PYTHONDONTWRITEBYTECODE': '1'})
        self.docker('create', '--name', self.names['worker'], *common, '--cpus', '.5', *options, self.meta['images']['services'],
            'python', '-m', 'services.fx_quotes_worker', '--engine-url', 'http://engine:8080/engine-rest', '--support-url', 'http://backend:28181', env=env)
        self.ready()
        deployment = self.clients()[0].deploy([self.stage / 'source/workloads/pay05' / (arm + '.bpmn') for arm in ('sequential', 'parallel')])
        save(self.output / 'deployment.json', deployment)
        self.definitions = {v['key']: v['id'] for v in deployment['deployedProcessDefinitions'].values()}
        assert set(self.definitions) == {'bench_pay05_sequential', 'bench_pay05_parallel'}
        assert not any(self.queues().values())
        for name in ('runtime.json', 'isolation.json'): save(self.output / name, read(self.stage / name))
        source = self.source_guard()
        for name in source['files']:
            target = self.output / 'inputs' / name; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((self.stage / 'source' / name).read_bytes())
        save(self.output / 'source-manifest.json', source)
        save(self.output / 'plan.json', {**self.plan, 'frozen_at': utc(), 'source_commit': self.meta['source_commit']})
        self.capture()

    def stop_workers(self):
        if self.inspect('worker')['State']['Running']: self.docker('stop', '--time', '35', self.names['worker'], timeout=45)
        assert not self.inspect('worker')['State']['Running']

    def pause(self):
        assert not any(self.queues().values())
        self.stop_workers()
        for role in ('engine', 'backend'):
            self.inspect(role); self.docker('stop', '--time', '30', self.names[role], timeout=45)
            assert not self.inspect(role)['State']['Running']

    def resume(self):
        for path in self.backend.parent.glob('study.sqlite*'):
            assert path.is_file() and not path.is_symlink(); os.chown(path, 65532, 65532)
        for role in ('backend', 'engine'): self.inspect(role); self.docker('start', self.names[role])
        self.ready()

    def pgtool(self, args, folder):
        env, options = self.envopts({'PGHOST': self.dbhost, 'PGPORT': '5432', 'PGUSER': self.dbuser,
            'PGPASSWORD': self.credentials['database'], 'PGDATABASE': 'postgres', 'PGSSLMODE': 'verify-full', 'PGSSLROOTCERT': '/run/db-ca.pem'})
        return self.docker('run', '--rm', '--label', self.label, '--network', 'host',
            '--mount', 'type=bind,src=' + str(self.ca) + ',dst=/run/db-ca.pem,readonly',
            '--mount', 'type=bind,src=' + str(folder) + ',dst=/checkpoint', *options, self.meta['tool_images']['postgres'], *args, env=env, timeout=600)

    def logical(self):
        with self.database() as db: engine = postgres_fingerprint(db, 'fluxnova')
        return {'engine': engine, 'backend': sqlite_fingerprint(self.backend)}

    def snapshot(self, folder):
        folder.mkdir(mode=0o700)
        before = self.logical()
        self.pgtool(['pg_dump', '--schema=fluxnova', '--strict-names', '--format=custom', '--no-owner', '--no-acl', '--file=/checkpoint/engine.dump'], folder)
        sqlite_copy(self.backend, folder / 'study.sqlite')
        assert self.logical() == before
        save(folder / 'logical.json', before)
        save(folder / 'files.json', {p.name: sha(p) for p in folder.iterdir() if p.is_file()})
        return before

    def capture(self):
        self.pause(); value = self.snapshot(self.checkpoint)
        save(self.checkpoint / 'manifest.json', {'logical': value, 'files': {n: sha(self.checkpoint / n) for n in ('engine.dump', 'study.sqlite')}})
        save(self.output / 'checkpoint.json', read(self.checkpoint / 'manifest.json'))
        self.resume()

    def reset(self, folder):
        self.bound(); self.pause(); frozen = read(self.checkpoint / 'manifest.json')
        assert all(sha(self.checkpoint / n) == v for n, v in frozen['files'].items())
        self.snapshot(folder / 'previous-state')
        listing = self.pgtool(['pg_restore', '--list', '/checkpoint/engine.dump'], self.checkpoint).decode()
        (self.checkpoint / 'restore.list').write_text(restore_listing(listing, 'fluxnova'))
        self.pgtool(['pg_restore', '--dbname=postgres', '--clean', '--if-exists', '--single-transaction', '--no-owner', '--no-acl',
                     '--exit-on-error', '--use-list=/checkpoint/restore.list', '/checkpoint/engine.dump'], self.checkpoint)
        sqlite_copy(self.checkpoint / 'study.sqlite', self.backend, replace=True)
        actual = self.logical(); assert actual == frozen['logical']
        save(folder / 'restored-logical.json', actual)
        receipt = {'verified': True, 'expected_fingerprint': digest(frozen['logical']), 'restored_fingerprint': digest(actual),
                   'schema': 'fluxnova', 'verified_before_resuming_writers': True, 'at': utc()}
        save(folder / 'reset.json', receipt); self.resume(); assert not any(self.queues().values()); return receipt

    def controls(self):
        containers = {}
        for role in ('engine', 'backend', 'worker'):
            x = self.inspect(role)
            containers[role] = {'image_id': x['Image'], 'arguments': x['Config']['Cmd'],
                'environment_sha256': digest(sorted(x['Config']['Env'])), 'memory_bytes': x['HostConfig']['Memory'],
                'nano_cpus': x['HostConfig']['NanoCpus'], 'pids_limit': x['HostConfig']['PidsLimit'],
                'network_mode': x['HostConfig']['NetworkMode'], 'read_only_root': x['HostConfig']['ReadonlyRootfs'],
                'mounts': [{k: m[k] for k in ('Type', 'Source', 'Destination', 'RW')} for m in x['Mounts']]}
        return {'source_manifest_sha256': sha(self.stage / 'source-manifest.json'), 'containers': containers,
                'worker_slots': 3, 'worker_idle_s': .1, 'poll_ms': 100, 'telemetry_interval_s': 5,
                'db_nodes': 1, 'db_ocpu': 2, 'db_memory_gib': 32, 'tool_images': self.meta['tool_images'],
                'generator': '16 fixed VUs, 1 GiB memory; shares controller', 'corpus_sha256': sha(self.corpus_path),
                'checkpoint_sha256': sha(self.checkpoint / 'manifest.json'),
                'instrumentation': 'SQL/Docker/Linux; no Java agent'}

    def sample(self):
        row = {'monotonic_s': time.monotonic(), 'at': utc(), 'host': linux_host_sample()}
        roles = ('engine', 'backend', 'worker')
        stats = [json.loads(s) for s in self.docker('stats', '--no-stream', '--format', '{{json .}}',
                  *[self.names[k] for k in roles], timeout=8).decode().splitlines()]
        stats = {s['Name']: s for s in stats}; row['containers'] = {}
        for role in roles:
            x, s = self.inspect(role), stats[self.names[role]]
            row['containers'][role] = {'running': x['State']['Running'], 'oom_killed': x['State']['OOMKilled'],
                'restart_count': x['RestartCount'], 'cpu_percent': float(s['CPUPerc'].rstrip('%')),
                'memory_usage': s['MemUsage'], 'pids': s['PIDs']}
        with self.database() as db:
            db.execute('SET statement_timeout=5000')
            values = db.execute('SELECT xact_commit,deadlocks FROM pg_stat_database WHERE datname=current_database()').fetchone()
            sessions = db.execute("SELECT count(*),count(*) FILTER (WHERE state='active'),count(*) FILTER (WHERE wait_event_type='Lock') FROM pg_stat_activity WHERE datname=current_database() AND usename=%s", (self.dbuser,)).fetchone()
            row['database'] = dict(zip(('commits', 'deadlocks', 'connections', 'active', 'lock_waiters'), [*values, *sessions]))
            row['database'].update(tls=db.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()[0], server_version=db.execute('SHOW server_version').fetchone()[0])
            row['engine'] = {k: db.execute('SELECT count(*) FROM fluxnova.' + table).fetchone()[0] for k, table in
                [('external_tasks', 'act_ru_ext_task'), ('incidents', 'act_ru_incident'), ('jobs', 'act_ru_job')]}
            row['engine']['active_instances'] = db.execute('SELECT count(*) FROM fluxnova.act_ru_execution WHERE id_=proc_inst_id_').fetchone()[0]
        return row

    def calibrate(self):
        folder = self.output / 'calibration'; folder.mkdir(mode=0o700)
        config = {'workload': {'rate': 1}, 'execution': {'k6_binary': str(self.stage / 'k6')},
                  'artifact': {'k6_image': self.meta['tool_images']['k6']},
                  'evidence': {'estimated_http_calls_per_workflow': 50, 'calibration_duration_s': 15}}
        value = calibrate_generator(config, self.prefix, folder); assert value['valid']; return value

    def final_state(self, folder):
        final = self.queues()
        final['completed_instances'] = self.clients()[0].request('GET', '/history/process-instance/count?finished=true')['count']
        with sqlite3.connect('file:' + str(self.backend) + '?mode=ro', uri=True) as db:
            for key, table in [('effects', 'effects'), ('registrations', 'cases'), ('receipts', 'receipts')]:
                final[key] = db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
        with self.database() as db:
            count = db.execute("SELECT count(*) FROM fluxnova.act_hi_procinst WHERE state_='COMPLETED'").fetchone()[0]
            assert count == final['completed_instances']
            history = db.execute("SELECT proc_inst_id_,business_key_,proc_def_id_,state_ FROM fluxnova.act_hi_procinst ORDER BY business_key_").fetchall()
        save(folder / 'sql-history.json', [{'id': r[0], 'business_key': r[1], 'definition_id': r[2], 'state': r[3]} for r in history])
        save(folder / 'final-state.json', final); return final

    def assess_lane(self, folder, events, summary, execution, calibration, final, rows, reset):
        return assess_cloud(events, summary, execution, calibration, final, rows, reset, self.corpus)

    def lane(self, index, settings, calibration, *, name=None, plan=None):
        self.bound(); self.source_guard()
        name = name or ('q' + str(index) + '-' + settings['arm'])
        assert re.fullmatch('[a-z0-9-]+', name)
        plan = plan or self.plan
        folder = self.output / name; folder.mkdir(mode=0o700)
        reset = self.reset(folder)
        baseline = self.controls(); save(folder / 'controls.json', baseline)
        require_matched_controls(read(self.output / 'controls.json'), baseline)
        assert not self.inspect('worker')['State']['Running']; self.docker('start', self.names['worker'])
        rows, stop = [], threading.Event()
        def collect():
            try: value = self.sample()
            except Exception as error: value = {'monotonic_s': time.monotonic(), 'error': type(error).__name__}
            rows.append(value)
            with (folder / 'telemetry.ndjson').open('a') as stream: stream.write(json.dumps(value) + '\n')
        def sampling():
            while not stop.wait(5): collect()
        collect(); thread = threading.Thread(target=sampling, daemon=True); thread.start()
        rate, arm = settings['rate'], settings['arm']
        warmup, duration = plan['warmup_arrivals'] / rate, plan['measurement_arrivals'] / rate
        run_id = self.prefix + '-' + name
        values = {'PAY05_ENGINE_URL': 'http://engine:8080/engine-rest', 'PAY05_SUPPORT_URL': 'http://backend:28181',
            'PAY05_USERNAME': 'pay05bench', 'PAY05_PASSWORD': self.credentials['admin'], 'PAY05_REGISTRATION_TOKEN': self.credentials['registrar'],
            'PAY05_RUN_ID': run_id, 'PAY05_ARM': arm, 'PAY05_RATE': rate, 'PAY05_DURATION_S': duration, 'PAY05_WARMUP_S': warmup,
            'PAY05_DRAIN_S': 30, 'PAY05_VUS': 16, 'PAY05_OUTPUT_DIR': '/evidence',
            'PAY05_DEFINITION_ID': self.definitions['bench_pay05_' + arm],
            'PAY05_CORPUS_FILE': '/run/pay05-corpus.json', 'PAY05_CORPUS_SHA256': sha(self.corpus_path)}
        env, options = self.envopts(values)
        argv = ['docker', 'run', '--rm', '--name', self.names['generator'], '--label', self.label, '--network', self.network,
            '--user', '0:0', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--read-only', '--memory', '1g', '--pids-limit', '256',
            '--mount', 'type=bind,src=' + str(self.stage / 'source') + ',dst=/source,readonly',
            '--mount', 'type=bind,src=' + str(self.corpus_path) + ',dst=/run/pay05-corpus.json,readonly',
            '--mount', 'type=bind,src=' + str(folder) + ',dst=/evidence', *options, self.meta['tool_images']['k6'],
            'run', '--log-format', 'json', '--no-color', '/source/load/pay05_quotes.js']
        execution = {'started_at': utc(), 'started_monotonic_s': time.monotonic()}
        failure = None
        try:
            with (folder / 'k6.log').open('xb') as log:
                proc = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT)
                try: code = proc.wait(timeout=warmup + duration + 90)
                except BaseException:
                    found = subprocess.run(['docker', 'inspect', self.names['generator']], capture_output=True)
                    if found.returncode == 0:
                        self.inspect('generator'); self.docker('stop', '--time', '10', self.names['generator'], timeout=20)
                    proc.wait(timeout=15); raise
            execution['returncode'] = code
        except BaseException as error:
            failure = error; execution.update(returncode=-1, error_type=type(error).__name__)
        finally:
            execution.update(finished_at=utc(), finished_monotonic_s=time.monotonic())
            save(folder / 'execution.json', execution)
            stop.set(); thread.join(timeout=30); assert not thread.is_alive(); collect(); self.stop_workers()
        if failure: raise failure
        events = []
        with (folder / 'events.ndjson').open('x') as stream:
            for line in (folder / 'k6.log').read_text(errors='replace').splitlines():
                item = extract_event(line)
                if item:
                    item = normalize_event(item); events.append(item); stream.write(json.dumps(item, allow_nan=False) + '\n')
        summary = read(folder / 'k6-summary.json')
        expected = {'run_id': run_id, 'arm': arm, 'rate': rate, 'duration_s': duration, 'warmup_s': warmup,
                    'drain_s': 30, 'fixed_vus': 16, 'deadline_s': 2, 'poll_interval_ms': 100,
                    'definition_id': values['PAY05_DEFINITION_ID'], 'corpus_sha256': values['PAY05_CORPUS_SHA256']}
        assert all(summary['pay05'][k] == v for k, v in expected.items())
        final = self.final_state(folder)
        result = self.assess_lane(folder, events, summary, execution, calibration, final, rows, reset)
        result.update(run_id=run_id, folder=folder.name); save(folder / 'assessment.json', result)
        self.pause(); self.snapshot(folder / 'stores'); self.resume()
        self.source_guard(); require_matched_controls(baseline, self.controls())
        return result

    def cleanup(self):
        errors, stopped = [], {}
        for role in ('generator', 'worker', 'backend'):
            try:
                present = subprocess.run(['docker', 'inspect', self.names[role]], capture_output=True)
                if present.returncode: continue
                state = self.inspect(role)
                if state['State']['Running']: self.docker('stop', '--time', '35', self.names[role], timeout=45)
                present = subprocess.run(['docker', 'inspect', self.names[role]], capture_output=True)
                if present.returncode and role == 'generator': continue
                state = self.inspect(role); assert not state['State']['Running']
                stopped[role] = {'stopped': True, 'oom_killed': state['State']['OOMKilled'], 'exit_code': state['State']['ExitCode']}
                logs = subprocess.run(['docker', 'logs', self.names[role]], capture_output=True, timeout=30)
                assert logs.returncode == 0
                raw = logs.stdout + logs.stderr
                for value in getattr(self, 'credentials', {}).values(): raw = raw.replace(value.encode(), b'REDACTED')
                (self.stage / (role + '.log')).write_bytes(raw); self.docker('rm', self.names[role])
            except Exception as error: errors.append({'component': role, 'error_type': type(error).__name__})
        result = super().cleanup(); result['cleanup_errors'].extend(errors); result['containers'].update(stopped)
        return result

    def run(self):
        self.bound(); failure = None
        status = {'status': 'running', 'study_id': 'PAY-05', 'run_id': self.run_id, 'lanes': [],
                  'cloud_resources_accessed': True, 'performance_comparison_complete': False}
        try:
            self.setup(); save(self.output / 'controls.json', self.controls())
            calibration = self.calibrate()
            for index, settings in enumerate(self.plan['lanes'], 1):
                status['current_lane'] = index; save(self.output / 'summary.json', status)
                result = self.lane(index, settings, calibration)
                status['lanes'].append(result); save(self.output / 'summary.json', status)
                assert result['status'] == 'passed', 'load_qualification_gate_failed'
            status['status'] = 'passed'
        except BaseException as error:
            failure = error; status.update(status='failed', error_type=type(error).__name__)
            save(self.stage / 'failure.json', {'error_type': type(error).__name__, 'traceback': traceback.format_exc()})
        finally:
            closeout = self.cleanup(); save(self.stage / 'closeout.json', closeout)
            if self.output.exists():
                if self.backend.exists() and not (self.output / 'final-ledger.sqlite').exists():
                    sqlite_copy(self.backend, self.output / 'final-ledger.sqlite')
                status.update(owned_engine_stopped=closeout['owned_engine_stopped'], cleanup_errors=closeout['cleanup_errors'], finished_at=utc())
                if closeout['cleanup_errors']: status['status'] = 'failed'
                save(self.output / 'summary.json', status); save(self.output / 'engine-closeout.json', closeout)
                save(self.output / 'checksums.json', {'algorithm': 'sha256', 'files': {p.relative_to(self.output).as_posix(): sha(p)
                    for p in sorted(self.output.rglob('*')) if p.is_file() and p.name != 'checksums.json'}})
        if failure: raise failure
        assert status['status'] == 'passed' and closeout['owned_engine_stopped']
        print(json.dumps({'status': 'passed', 'qualification_lanes': 4, 'performance_comparison_complete': False}), flush=True)


if __name__ == '__main__':
    def terminated(*_): raise RuntimeError('owned_qualification_terminated')
    signal.signal(signal.SIGTERM, terminated)
    if sys.argv[1:] == ['run']: LoadCell(HERE).run()
    elif sys.argv[1:] == ['retire']:
        cell = LoadCell(HERE, cleanup_only=True); result = cell.cleanup(); save(HERE / 'retirement.json', result)
        print(json.dumps(result)); assert not result['cleanup_errors']
    else: raise SystemExit('Use run or retire from an owned stage.')
