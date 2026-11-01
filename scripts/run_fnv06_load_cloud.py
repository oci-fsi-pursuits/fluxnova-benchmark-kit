#!/usr/bin/env python3
"""Small real-k6 event qualification in an owned OCI PostgreSQL cell.

Stage as runtime.py beside identity.json and source/. Allocation and deletion
remain separate guarded operator actions. No larger campaign is started here.
"""
import os
from datetime import datetime, timezone
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

    def __init__(self, stage, *, cleanup_only=False):
        super().__init__(stage, cleanup_only=cleanup_only)
        if self.meta.get('qualification_protocol') == 'repeat':
            from benchlib.fnv06_repeat import cell_protocol
            selected=cell_protocol(read(self.stage/'source/workloads/fnv06/repeat-qualification.json'),self.meta['qualification_cell']['id'])
            assert selected['qualification_cell']==self.meta['qualification_cell']
            self.output=self.stage/'cells'/self.meta['qualification_cell']['id']

    def setup(self):
        self.bound()
        repeated=self.meta.get('qualification_protocol')=='repeat'
        if repeated:
            from benchlib.fnv06_reset import verify_seal
            self.output.parent.mkdir(mode=0o700,exist_ok=True)
            cell_id=self.meta['qualification_cell']['id']
            assert not self.output.exists()
            if cell_id!='C01':
                previous='C'+str(int(cell_id[1:])-1).zfill(2)
                receipt=self.stage/'operator-receipts'/previous/'reset.json'
                reset=read(receipt)
                assert sha(receipt)==self.meta['reset_receipt_sha256']
                assert reset['status']=='passed' and reset['next_cell']==self.meta['qualification_cell']
                assert reset['target_id']==self.meta['target_id'] and reset['run_id']==self.run_id
                assert verify_seal(self.stage/'cells'/previous)==reset['prior_seal_sha256']==self.meta['prior_cell_seal_sha256']
            else: assert 'reset_receipt_sha256' not in self.meta
        # Download outside the private container; only verified executable bytes
        # are mounted into it. Never extract arbitrary archive paths or links.
        tools = self.stage / 'generator'
        reused=tools.exists()
        if reused: assert repeated and self.meta['qualification_cell']['id']!='C01'
        else: tools.mkdir(mode=0o700)
        name, digest, member = ASSETS['Linux']
        archive = tools / name
        if not reused:
            request = urllib.request.Request('https://github.com/grafana/k6/releases/download/v2.3.0/' + name,
                                             headers={'User-Agent': 'fluxnova-fnv06-qualification'})
            with urllib.request.urlopen(request, timeout=90) as response:
                archive.write_bytes(response.read(80 * 1024 * 1024 + 1))
        assert archive.stat().st_size <= 80 * 1024 * 1024 and sha(archive) == digest
        with tarfile.open(archive) as bundle:
            item = bundle.getmember(member); assert item.isfile() and item.size <= 150 * 1024 * 1024
            binary = tools / 'k6'; verified=bundle.extractfile(item).read()
            if reused: assert binary.read_bytes()==verified
            else: binary.write_bytes(verified)
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

    def cleanup_recorder(self, engine, directory):
        if self.meta.get('qualification_protocol', 'small') in ('scale','repeat'):
            from benchlib.fnv06_pagination import PaginatedRecorder
            return PaginatedRecorder(engine, directory)
        return super().cleanup_recorder(engine, directory)

    def reset(self):
        """Advance exactly one qualification cell after a fresh signed-GET operator guard."""
        from benchlib.fnv06_reset import reset_owned_schema,verify_seal
        self.bound();assert self.meta.get('qualification_protocol')=='repeat'
        request=read(self.stage/'reset-request.json');issued=datetime.fromisoformat(request['issued_at'])
        assert issued.tzinfo and 0<=(datetime.now(timezone.utc)-issued).total_seconds()<120
        inventory=read(self.stage/'source-manifest.json')
        assert request['source_commit']==self.meta['source_commit']==inventory['commit']
        assert request['source_manifest_sha256']==sha(self.stage/'source-manifest.json')
        assert sha(self.stage/'runtime.py')==inventory['runtime_sha256']
        assert sha(self.stage/'fixed_db_forward.py')==inventory['forward_sha256']
        for name,digest in inventory['files'].items():
            path=self.stage/'source'/name
            assert path.resolve().is_relative_to(self.stage/'source') and sha(path)==digest
        seal=verify_seal(self.output);assert seal==request['previous_seal_sha256']
        audit=request['audit']
        for key,name in [('auditor_sha256','audit_fnv06_load_qualification.py'),
                         ('shared_state_auditor_sha256','audit_fnv06_functional.py'),('cloud_auditor_sha256','audit_fnv06_cloud.py')]:
            assert audit[key]==sha(self.stage/'source/scripts'/name)
        summary=read(self.output/'summary.json')
        assert not any(read(self.output/'final-engine-counts.json').values())
        assert not (self.stage/'failure.json').exists()
        assert not self.command(['docker','ps','-aq','--filter','label='+self.label]).strip()
        assert subprocess.run(['docker','network','inspect',self.network],capture_output=True).returncode!=0
        archive=self.stage/'operator-receipts'/self.meta['qualification_cell']['id']
        assert not archive.exists();archive.mkdir(parents=True,mode=0o700)
        save(archive/'intent.json',{'run_id':self.run_id,'previous_cell':self.meta['qualification_cell'],
            'previous_seal_sha256':seal,'signed_get_guard_at':request['issued_at']})
        with self.database(True) as db:
            result=reset_owned_schema(db,meta=self.meta,target=request['target'],summary=summary,audit=audit,
                                      seal_sha256=seal,bound=self.bound)
        save(archive/'reset.json',result)
        for name in ('identity.json','runtime.json','closeout.json','isolation.json','owned-network.json',
                     'forward.json','engine.log','job.log','reset-request.json'):
            source=self.stage/name;target=archive/name
            assert source.resolve().parent==self.stage and target.resolve().parent==archive
            target.write_bytes(source.read_bytes());target.chmod(0o600)
        for name in ('credentials.local.json','fixed-forward.log'):
            source=self.stage/name;target=archive/name
            assert source.resolve().parent==self.stage and target.resolve().parent==archive and not target.exists()
            source.rename(target)
        updated=dict(self.meta,qualification_cell=result['next_cell'],prior_cell_seal_sha256=seal,
                     reset_receipt_sha256=sha(archive/'reset.json'))
        save(self.stage/'identity.json',updated)
        assert verify_seal(self.output)==seal
        return {'status':'reset_and_advanced','previous_cell':self.meta['qualification_cell']['id'],
                'next_cell':result['next_cell']['id'],'outside_schema_catalogs_unchanged':True}


def job():
    from benchlib.fluxnova import FluxnovaClient
    from benchlib.fnv06_cloud import require_stage
    from benchlib.fnv06_functional import Recorder
    from benchlib.fnv06_pagination import PaginatedRecorder
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
    mode=meta.get('qualification_protocol', 'small');assert mode in ('small','scale','repeat')
    protocol = read(output / 'inputs/workloads/fnv06' / ('repeat-qualification.json' if mode=='repeat' else 'scale-qualification.json' if mode=='scale' else 'load-qualification.json'))
    if mode=='repeat':
        from benchlib.fnv06_repeat import cell_protocol
        protocol=cell_protocol(protocol,meta['qualification_cell']['id'])
        assert protocol['qualification_cell']==meta['qualification_cell']
    url, user = 'http://engine:8080/engine-rest', 'fnv06bench'
    scope = 'OCI PostgreSQL driver and observer qualification; no performance or capacity claim.'
    manifest = {'study_id': protocol['study_id'], 'scope': scope, 'run_id': meta['run_id'], 'runtime': runtime,
        'database': 'OCI managed PostgreSQL', 'generator': generator, 'engine_url': url,
        'functional_batch_wait_timeout_s': protocol['batch_terminal_timeout_s'], 'batch_poll_interval_s': .5,
        'source_sha256': inventory['files'], 'source_commit': meta['source_commit'],
        'model_calls': 0, 'cloud_resources_accessed': True, 'performance_comparison_complete': False}
    if mode=='repeat':manifest['qualification_cell']=protocol['qualification_cell']
    save(output / 'manifest.json', manifest)
    summary = {'study_id': protocol['study_id'], 'scope': scope, 'run_id': meta['run_id'], 'status': 'running',
        'cases_collected': [], 'owned_process_ids': [], 'owned_batch_ids': [], 'model_calls': 0,
        'cloud_resources_accessed': True, 'performance_comparison_complete': False,
        'owned_engine_stopped': False, 'cleanup_errors': []}
    class BoundedRecorder(PaginatedRecorder if mode in ('scale','repeat') else Recorder):
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
    elif sys.argv[1:] == ['reset']:
        import json
        print(json.dumps(LoadCell(HERE).reset()))
    elif sys.argv[1:] == ['retire']:
        cell = LoadCell(HERE, cleanup_only=True); result = cell.cleanup()
        save(cell.stage / 'retirement.json', result)
        import json
        print(json.dumps(result)); assert not result['cleanup_errors']
    else: raise SystemExit('Use run from an owned stage, or job inside the prepared private container.')
