#!/usr/bin/env python3
"""Small real-k6 event qualification in an owned OCI PostgreSQL cell.

Stage as runtime.py beside identity.json and source/. Allocation and deletion
remain separate guarded operator actions. No larger campaign is started here.
"""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import urllib.request

HERE = Path(__file__).resolve().parent
if (HERE / 'identity.json').is_file(): sys.path.insert(0, str(HERE / 'source'))
else: sys.path.insert(0, str(HERE.parent))
from scripts.run_fnv06_cloud import Cell, read, save, sha
from scripts.ci_tools import ASSETS


class LoadCell(Cell):
    job_script = 'scripts/run_fnv06_load_cloud.py'

    def setup(self):
        self.bound()
        # Download outside the private container; only verified executable bytes
        # are mounted into it. Never extract arbitrary archive paths or links.
        tools = self.stage / 'generator'; tools.mkdir(mode=0o700)
        name, digest, member = ASSETS['Linux']
        archive = tools / name
        request = urllib.request.Request('https://github.com/grafana/k6/releases/download/v2.3.0/' + name,
                                         headers={'User-Agent': 'fluxnova-fnv06-qualification'})
        with urllib.request.urlopen(request, timeout=90) as response:
            archive.write_bytes(response.read(80 * 1024 * 1024 + 1))
        assert archive.stat().st_size <= 80 * 1024 * 1024 and sha(archive) == digest
        with tarfile.open(archive) as bundle:
            item = bundle.getmember(member); assert item.isfile() and item.size <= 150 * 1024 * 1024
            binary = tools / 'k6'; binary.write_bytes(bundle.extractfile(item).read())
        binary.chmod(0o755)
        version = self.command([str(binary), 'version'], timeout=10).decode().strip()
        assert 'v2.3.0' in version and 'linux/amd64' in version
        self.generator = {'version': version, 'archive': name, 'archive_sha256': digest,
                          'executable_sha256': sha(binary)}
        save(self.stage / 'generator.json', self.generator)
        self.bound(); super().setup()

    def runtime_extension(self): return {'generator': self.generator}

    def job_mounts(self):
        return ['--mount', 'type=bind,src=' + str(self.stage / 'generator/k6') + ',dst=/run/k6,readonly']


def job():
    from benchlib.fluxnova import FluxnovaClient
    from benchlib.fnv06_cloud import require_stage
    from benchlib.fnv06_functional import Recorder
    from benchlib.fnv06_load_qualification import exercise
    source, output = Path('/source'), Path('/evidence')
    meta, inventory = read('/run/identity.json'), read('/run/source-manifest.json')
    require_stage(meta, meta['stage']); assert not any(output.iterdir())
    for name, digest in inventory['files'].items():
        original = source / name
        assert original.resolve().is_relative_to(source) and sha(original) == digest
        target = output / 'inputs' / name; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original.read_bytes())
    runtime = read('/run/runtime.json'); generator = runtime['generator']
    assert generator['archive_sha256'] == ASSETS['Linux'][1]
    assert sha(Path('/run/k6')) == generator['executable_sha256']
    assert subprocess.check_output(['/run/k6', 'version'], text=True, timeout=10).strip() == generator['version']
    save(output / 'generator-check.json', generator)
    protocol = read(output / 'inputs/workloads/fnv06/load-qualification.json')
    url, user = 'http://engine:8080/engine-rest', 'fnv06bench'
    scope = 'OCI PostgreSQL driver and observer qualification; no performance or capacity claim.'
    manifest = {'study_id': 'FNV-06-LQ', 'scope': scope, 'run_id': meta['run_id'], 'runtime': runtime,
        'database': 'OCI managed PostgreSQL', 'generator': generator, 'engine_url': url,
        'functional_batch_wait_timeout_s': 120, 'batch_poll_interval_s': .5,
        'source_sha256': inventory['files'], 'source_commit': meta['source_commit'],
        'model_calls': 0, 'cloud_resources_accessed': True, 'performance_comparison_complete': False}
    save(output / 'manifest.json', manifest)
    summary = {'study_id': 'FNV-06-LQ', 'scope': scope, 'run_id': meta['run_id'], 'status': 'running',
        'cases_collected': [], 'owned_process_ids': [], 'owned_batch_ids': [], 'model_calls': 0,
        'cloud_resources_accessed': True, 'performance_comparison_complete': False,
        'owned_engine_stopped': False, 'cleanup_errors': []}
    class BoundedRecorder(Recorder):
        def call(self, *args, **kwargs):
            require_stage(meta, meta['stage'])
            return super().call(*args, **kwargs)
    engine = FluxnovaClient(url, user, os.environ['BENCH_PASSWORD'])
    api = BoundedRecorder(engine, output)
    try:
        save(output / 'engine-version.json', engine.readiness())
        exercise(api, protocol, meta['run_id'], user, summary, Path('/run/k6'),
                 bound=lambda: require_stage(meta, meta['stage']))
        summary['status'] = 'collected_pending_database_snapshot'
    except BaseException as error:
        summary.update(status='failed', error_type=type(error).__name__)
        raise
    finally: save(output / 'summary.json', summary)


if __name__ == '__main__':
    def terminated(*_): raise RuntimeError('owned_qualification_terminated')
    signal.signal(signal.SIGTERM, terminated)
    if sys.argv[1:] == ['job']: job()
    elif sys.argv[1:] == ['run']: LoadCell(HERE).run()
    elif sys.argv[1:] == ['retire']:
        cell = LoadCell(HERE, cleanup_only=True); result = cell.cleanup()
        save(cell.stage / 'retirement.json', result)
        import json
        print(json.dumps(result)); assert not result['cleanup_errors']
    else: raise SystemExit('Use run from an owned stage, or job inside the prepared private container.')
