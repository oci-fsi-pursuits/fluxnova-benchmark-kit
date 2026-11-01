"""Owned, read-only resource collection with job/host lifetime handshakes.

The job stays alive until its host has retained post-work samples and stopped
both readers. No Docker socket or host proc filesystem is mounted in the job.
"""
import hashlib
import json
from pathlib import Path
import threading
import time

from benchlib.fnv06_resources import CgroupReader, sample

PROTOCOL_PATH = 'workloads/fnv06/resource-observation.json'
PHASE = 'workload_resources_v1'
DB_QUERY = """SELECT json_build_object(
 'database',current_database(),'user',current_user,'backend_pid',pg_backend_pid(),
 'server_epoch_ms',floor(extract(epoch FROM clock_timestamp())*1000)::bigint,
 'tls',(SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),
 'track_activities',current_setting('track_activities'),'track_counts',current_setting('track_counts'),
 'activity',COALESCE((SELECT json_agg(x) FROM (
   SELECT state,wait_event_type,wait_event,count(*) AS sessions FROM pg_stat_activity
   WHERE datname=current_database() AND usename=current_user AND pid<>pg_backend_pid()
   GROUP BY state,wait_event_type,wait_event ORDER BY state,wait_event_type,wait_event) x),'[]'::json),
 'statistics',(SELECT row_to_json(x) FROM (
   SELECT stats_reset::text,xact_commit,xact_rollback,blks_read,blks_hit,deadlocks,temp_bytes
   FROM pg_stat_database WHERE datname=current_database()) x))"""


def read(path):
    return json.loads(Path(path).read_text())


def clock():
    return {'epoch_ms': time.time()*1000, 'monotonic_s': time.monotonic()}


def save(path, value, *, shared=False):
    path = Path(path)
    pending = path.with_name(path.name+'.pending')
    with pending.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, allow_nan=False)+'\n')
    pending.chmod(0o644 if shared else 0o600)
    pending.replace(path)


def declaration(meta, source):
    if 'resource_phase' not in meta:
        if 'resource_protocol_sha256' in meta:
            raise ValueError('Resource hash without a declared phase')
        return None
    assert meta['resource_phase'] == PHASE
    raw = (Path(source)/PROTOCOL_PATH).read_bytes()
    spec = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == meta['resource_protocol_sha256']
    assert spec['schema_version'] == 1 and spec['phase'] == PHASE
    assert spec['study_id'] == 'FNV-06-RESOURCE-OBSERVATION' and spec['qualification_only'] is True
    assert [spec[k] for k in ('container_sample_interval_s','maximum_container_sample_gap_s',
        'maximum_container_read_s','database_sample_interval_s','maximum_database_sample_gap_s',
        'maximum_database_read_s','clock_tolerance_s','handshake_timeout_s')] == [.1,.25,.1,.5,1,.9,.05,30]
    return spec


def binding(meta):
    return {k: meta[k] for k in ('run_id','source_commit','qualification_cell','resource_protocol_sha256')}


def await_file(path, *, bound, timeout, running=lambda: True):
    end = time.monotonic()+timeout
    next_process_check = 0
    while not path.exists():
        bound()
        now = time.monotonic()
        if now >= end:
            raise TimeoutError('Resource handshake did not complete')
        if now >= next_process_check:
            if not running(): raise TimeoutError('Owned job exited before resource handoff')
            next_process_check = now+1
        time.sleep(.02)
    return read(path)


def job_start(meta, output, source, *, bound):
    spec = declaration(meta, source)
    if spec is None:
        return None
    folder = Path(output)/'resources'
    folder.mkdir()
    save(folder/'job-ready.json', {'binding':binding(meta), 'clock':clock()})
    ready = await_file(folder/'host-ready.json', bound=bound, timeout=spec['handshake_timeout_s'])
    assert ready['binding'] == binding(meta) and ready['readers_ready'] is True
    return folder


def job_finish(meta, folder, *, bound):
    if folder is None:
        return
    save(folder/'job-finished.json', {'binding':binding(meta), 'clock':clock()})
    stopped = await_file(folder/'host-finished.json', bound=bound, timeout=30)
    assert stopped['binding'] == binding(meta) and stopped['readers_stopped'] is True
    assert stopped['status'] == 'collected'


class Sampler:
    """Flush each original observation; preserve errors and never substitute zeros."""
    def __init__(self, path, interval, reader, *, enter=None, leave=None):
        self.path = Path(path); self.interval = interval; self.reader = reader
        self.enter = enter or (lambda: None); self.leave = leave or (lambda _: None)
        self.stop_event = threading.Event(); self.ready = threading.Event()
        self.error = None; self.count = 0; self.last_started_epoch_ms = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        state = None
        try:
            with self.path.open('x', encoding='utf-8') as stream:
                try:
                    state = self.enter()
                    while not self.stop_event.is_set():
                        start = time.monotonic()
                        row = self.reader(state)
                        stream.write(json.dumps(row, allow_nan=False)+'\n'); stream.flush()
                        self.count += 1; self.last_started_epoch_ms = row['started_epoch_ms']; self.ready.set()
                        self.stop_event.wait(max(0, self.interval-(time.monotonic()-start)))
                except BaseException as error:
                    self.error = type(error).__name__
                    stream.write(json.dumps({'error':self.error, 'clock':clock()})+'\n'); stream.flush()
                    self.ready.set()
        except BaseException as error:
            self.error = type(error).__name__; self.ready.set()
        finally:
            try:
                if state is not None: self.leave(state)
            except BaseException as error:
                self.error = type(error).__name__

    def start(self):
        self.thread.start()

    def check(self):
        if self.error or not self.thread.is_alive():
            raise RuntimeError('Resource sampler failed: '+str(self.error))

    def stop(self):
        self.stop_event.set(); self.thread.join(timeout=15)
        if self.thread.is_alive():
            raise RuntimeError('Resource sampler did not stop')


def database_reader(db):
    start = time.monotonic(); epoch = time.time()*1000
    value = db.execute(DB_QUERY).fetchone()[0]
    return {'started_monotonic_s':start,'started_epoch_ms':epoch,
            'finished_monotonic_s':time.monotonic(),'finished_epoch_ms':time.time()*1000,
            'query':DB_QUERY, 'value':value}


def owned_binding(cell, role):
    state = cell.inspect(role)
    assert state['State']['Running'] and not state['State']['OOMKilled']
    assert state['Name'] == '/'+cell.names[role]
    expected = cell.meta['images']['engine' if role == 'engine' else 'services']
    assert state['Config']['Image'] == expected
    limits = state['HostConfig']
    assert (limits['NanoCpus'],limits['Memory']) == ((2000000000,4294967296) if role == 'engine' else (1000000000,536870912))
    return {'container_id':state['Id'],'host_pid':state['State']['Pid'],'image':expected,
            'owner':cell.run_id,'name':cell.names[role], 'nano_cpus':limits['NanoCpus'],'memory_bytes':limits['Memory']}


def execute_observed_job(cell):
    spec = declaration(cell.meta, cell.stage/'source'); assert spec is not None
    folder = cell.output/'resources'; streams = []; error = None; records = None
    cell.docker('start', cell.names['job'])
    try:
        ready = await_file(folder/'job-ready.json', bound=cell.bound, timeout=30,
                           running=lambda: cell.inspect('job')['State']['Running'])
        assert ready['binding'] == binding(cell.meta)
        records = {role:owned_binding(cell, role) for role in ('engine','job')}
        readers = {('generator' if role == 'job' else role):CgroupReader(v['host_pid'],v['container_id']) for role,v in records.items()}
        containers = Sampler(folder/'containers.ndjson', spec['container_sample_interval_s'], lambda _: sample(readers))
        def connect():
            db = cell.database()
            try:
                db.execute("SET default_transaction_read_only=on")
                db.execute("SET statement_timeout='750ms'")
                db.execute("SET application_name='fnv06-resource-observer'")
                return db
            except BaseException:
                db.close(); raise
        database = Sampler(folder/'database.ndjson', spec['database_sample_interval_s'], database_reader,
                           enter=connect, leave=lambda db: db.close())
        streams = [containers,database]
        for stream in streams: stream.start()
        end = time.monotonic()+15
        while not all(s.ready.is_set() for s in streams):
            cell.bound()
            assert time.monotonic() < end, 'Resource readers did not become ready'
            for stream in streams: stream.check()
            time.sleep(.02)
        for stream in streams: stream.check()
        save(folder/'host-ready.json', {'binding':binding(cell.meta),'clock':clock(),'readers_ready':True}, shared=True)
        finished = await_file(folder/'job-finished.json', bound=lambda: (cell.bound(),[s.check() for s in streams]),
                              timeout=900, running=lambda: cell.inspect('job')['State']['Running'])
        assert finished['binding'] == binding(cell.meta)
        end = time.monotonic()+5
        while not all(s.last_started_epoch_ms > finished['clock']['epoch_ms'] for s in streams):
            cell.bound(); assert time.monotonic() < end, 'Missing post-work resource sample'
            for stream in streams: stream.check()
            time.sleep(.02)
    except BaseException as problem:
        error = problem
    finally:
        stop_errors = []
        for stream in streams:
            try: stream.stop()
            except BaseException as problem: stop_errors.append(type(problem).__name__)
        if folder.exists():
            result = {'binding':binding(cell.meta),'clock':clock(),'status':'collected' if error is None and not stop_errors and all(s.error is None for s in streams) else 'failed',
                'readers_stopped':not any(s.thread.is_alive() for s in streams),'container_bindings':records,
                'database_binding':{'target_id':cell.meta['target_id'],'host':cell.dbhost,'role':cell.dbuser},
                'samples':{s.path.name:s.count for s in streams},'sampler_errors':{s.path.name:s.error for s in streams if s.error},
                'stop_errors':stop_errors,'error_type':type(error).__name__ if error else None}
            save(folder/'host-finished.json', result, shared=True)
        if stop_errors and error is None: error = RuntimeError('Resource sampler shutdown failed')
    if error is not None: raise error
    end = time.monotonic()+30
    while cell.inspect('job')['State']['Running']:
        cell.bound(); assert time.monotonic() < end, 'Job did not exit after resource handoff'; time.sleep(.1)
