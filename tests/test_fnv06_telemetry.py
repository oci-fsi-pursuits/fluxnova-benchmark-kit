import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from benchlib.fnv06_telemetry import (Sampler, await_file, binding, clock, declaration,
    job_start, job_finish, owned_binding, save, DB_QUERY)
from scripts.audit_fnv06_resources import audit_window
from scripts.audit_fnv06_telemetry import check_clocks, database_window, work_window
from tests.test_fnv06_resources import fixture
from tests.test_fnv06_load import observer_fixture

ROOT = Path(__file__).resolve().parents[1]


def meta():
    path = ROOT/'workloads/fnv06/resource-observation.json'
    return {'run_id':'a'*32,'source_commit':'b'*40,'qualification_cell':{'id':'C01','kind':'message','population':10},
            'resource_phase':'workload_resources_v1','resource_protocol_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def db_fixture():
    result = []
    for i in range(5):
        result.append({'started_monotonic_s':10+i*.5,'finished_monotonic_s':10.01+i*.5,
            'started_epoch_ms':2000000+i*500,'finished_epoch_ms':2000010+i*500,'query':DB_QUERY,
            'value':{'database':'postgres','user':'fnv06_'+'a'*12,'backend_pid':44,'server_epoch_ms':2000005+i*500,
                'tls':True,'track_activities':'on','track_counts':'on',
                'activity':[{'state':'active','wait_event_type':'Lock','wait_event':'relation','sessions':4}],
                'statistics':{'stats_reset':None,'xact_commit':10+i,'xact_rollback':0,'blks_read':0,
                              'blks_hit':100+i,'deadlocks':0,'temp_bytes':0}}})
    return result


class WorkResourceAuditTests(unittest.TestCase):
    def db(self, sequence):
        return database_window(sequence,2000050,2001750,1990,'fnv06_'+'a'*12)

    def test_database_activity_reports_waits_without_claiming_cpu(self):
        value = self.db(db_fixture())
        self.assertEqual(value['observed_wait_events'],['Lock:relation'])
        self.assertEqual(value['trial_database_counter_deltas']['xact_commit'],4)
        self.assertNotIn('generator_headroom_passed',value)

    def test_database_missing_brackets_clock_drift_reset_and_foreign_role_reject(self):
        for kind in ('before','after','clock','server_clock','reset','counter','identity','tls','tracking','missing_activity','duplicate'):
            seq = db_fixture()
            if kind == 'before': seq = seq[1:]
            elif kind == 'after': seq = seq[:-1]
            elif kind == 'clock': seq[2]['started_epoch_ms'] += 100
            elif kind == 'server_clock': seq[2]['value']['server_epoch_ms'] += 1000
            elif kind == 'reset': seq[2]['value']['statistics']['stats_reset'] = 'changed'
            elif kind == 'counter': seq[2]['value']['statistics']['xact_commit'] = 0
            elif kind == 'identity': seq[2]['value']['user'] = 'unrelated'
            elif kind == 'tls': seq[2]['value']['tls'] = False
            elif kind == 'tracking': seq[2]['value']['track_activities'] = 'off'
            elif kind == 'missing_activity': seq[2]['value'].pop('activity')
            else: seq[2]['value']['activity'] *= 2
            with self.subTest(kind=kind),self.assertRaises((AssertionError,KeyError)):
                self.db(seq)

    def test_engine_throttling_is_an_outcome_not_a_generator_failure(self):
        sequence = fixture()
        for row in sequence:
            row['containers']['engine'] = copy.deepcopy(row['containers']['generator'])
        for row in sequence[2:]:
            data = row['containers']['engine']['files']
            data['cpu.stat'] = data['cpu.stat'].replace('nr_throttled 0','nr_throttled 1').replace('throttled_usec 0','throttled_usec 1000')
        generator = audit_window(sequence,start_epoch_ms=2000050,finish_epoch_ms=2000450)
        engine = audit_window(sequence,start_epoch_ms=2000050,finish_epoch_ms=2000450,generator_role='engine',role_kind='engine')
        self.assertTrue(generator['generator_headroom_passed'])
        self.assertTrue(engine['engine_constraints_observed'])
        self.assertEqual(engine['engine_throttled_delta_usec'],1000)
        self.assertNotIn('generator_headroom_passed',engine)

    def test_work_window_uses_original_release_and_both_completion_and_request_end(self):
        observer,requests = observer_fixture()
        observer['clock_origin'] = {'started_monotonic_s':10,'finished_monotonic_s':10.002,
            'epoch_ms':999000,'release_monotonic_s':11.001}
        observer['finished_clock'] = {'epoch_ms':1000320,'monotonic_s':11.321}
        origin = 988.999
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'actions.json').write_text(json.dumps([{'finished_epoch_ms':1000300}]))
            (folder/'observer.json').write_text(json.dumps(observer))
            self.assertEqual(work_window(folder,{'release_epoch_ms':1000000},origin,requests),(1000000,1000300))
            observer['clock_origin']['release_monotonic_s'] += .1
            (folder/'observer.json').write_text(json.dumps(observer))
            with self.assertRaises(AssertionError): work_window(folder,{'release_epoch_ms':1000000},origin,requests)

    def test_read_gaps_and_nonfinite_clocks_cannot_pass(self):
        for kind in ('gap','duration','nonfinite','error'):
            seq = db_fixture()
            if kind == 'gap': seq = seq[:1]+seq[3:]
            elif kind == 'duration': seq[0]['finished_monotonic_s'] += 1
            elif kind == 'nonfinite': seq[1]['started_epoch_ms'] = float('nan')
            else: seq[1]['error'] = 'unavailable'
            with self.subTest(kind=kind),self.assertRaises(AssertionError):
                check_clocks(seq,1990,max_read_s=.9,max_gap_s=1)


class ResourceLifecycleTests(unittest.TestCase):
    def test_protocol_is_opt_in_and_exact_hash_bound(self):
        self.assertIsNone(declaration({},ROOT))
        self.assertEqual(declaration(meta(),ROOT)['container_sample_interval_s'],.1)
        for key in ('resource_phase','resource_protocol_sha256'):
            wrong = meta(); wrong[key] = 'changed'
            with self.assertRaises(AssertionError): declaration(wrong,ROOT)
        with self.assertRaises(ValueError): declaration({'resource_protocol_sha256':'orphan'},ROOT)

    def test_sampler_flushes_and_retains_failure_instead_of_zero(self):
        count = []
        def failing(_):
            count.append(1)
            if len(count) > 1: raise OSError('read unavailable')
            return {'started_epoch_ms':1,'value':7}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'samples.ndjson'
            sampler = Sampler(path,.001,failing); sampler.start()
            sampler.thread.join(timeout=2); sampler.stop()
            evidence = [json.loads(x) for x in path.read_text().splitlines()]
            self.assertEqual(evidence[0]['value'],7)
            self.assertEqual(evidence[1]['error'],'OSError')
            self.assertEqual(sampler.count,1)
            with self.assertRaises(RuntimeError): sampler.check()

    def test_sampler_close_failure_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            def close(_): raise OSError('close failed')
            sampler = Sampler(Path(directory)/'samples',.01,lambda _: {'started_epoch_ms':1},enter=lambda: True,leave=close)
            sampler.start(); self.assertTrue(sampler.ready.wait(2)); sampler.stop()
            self.assertEqual(sampler.error,'OSError')

    def test_stuck_reader_cannot_claim_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            sampler = Sampler(Path(directory)/'unused',.1,lambda _: None)
            sampler.thread = SimpleNamespace(join=lambda **kwargs: None,is_alive=lambda: True)
            with self.assertRaisesRegex(RuntimeError,'did not stop'): sampler.stop()

    def test_job_stays_alive_for_host_flush_and_rejects_failed_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory); identity = meta(); seen = []
            def host():
                resource = output/'resources'
                await_file(resource/'job-ready.json',bound=lambda: None,timeout=2)
                save(resource/'host-ready.json',{'binding':binding(identity),'readers_ready':True})
                await_file(resource/'job-finished.json',bound=lambda: None,timeout=2)
                seen.append('work_finished')
                save(resource/'host-finished.json',{'binding':binding(identity),'readers_stopped':True,'status':'collected'})
            thread = threading.Thread(target=host); thread.start()
            folder = job_start(identity,output,ROOT,bound=lambda: None)
            self.assertEqual(seen,[])
            job_finish(identity,folder,bound=lambda: None); thread.join(2)
            self.assertEqual(seen,['work_finished'])
            (folder/'host-finished.json').write_text(json.dumps({'binding':binding(identity),'readers_stopped':False,'status':'failed'}))
            with self.assertRaises(AssertionError): job_finish(identity,folder,bound=lambda: None)

    def test_missing_handshake_and_foreign_container_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TimeoutError): await_file(Path(directory)/'absent',bound=lambda: None,timeout=.01)
        identity = meta(); identity['images'] = {'engine':'pinned-engine','services':'pinned-services'}
        cell = SimpleNamespace(meta=identity,run_id=identity['run_id'],names={'job':'owned'})
        cell.inspect = lambda _: {'State':{'Running':True,'OOMKilled':False,'Pid':8},'Id':'a'*64,
            'Name':'/unrelated','Config':{'Image':'pinned-services'},'HostConfig':{'NanoCpus':1000000000,'Memory':536870912}}
        with self.assertRaises(AssertionError): owned_binding(cell,'job')


if __name__ == '__main__': unittest.main()
