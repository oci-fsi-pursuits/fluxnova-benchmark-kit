#!/usr/bin/env python3
"""Prespecified PAY-05 qualification and five paired measurements in an owned stage.

The operator owns allocation, the external final audit, source preservation and
database deletion. Any invalid arm stops this attempt; no arm is replaced.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import sys
import traceback

HERE = Path(__file__).resolve().parent
if (HERE / 'identity.json').is_file(): sys.path.insert(0, str(HERE / 'source'))
from scripts.run_pay05_load_cloud import LoadCell, read, save, sha, utc
from benchlib.checkpoint import sqlite_copy
from benchlib.pay05_performance import resolve_corpus, designs, assess_profile_lane, qualified_gate, summarize_pairs
from scripts.audit_pay05_performance import check_lane, seal, verify_seal


class PerformanceCell(LoadCell):
    def configure(self):
        self.output = self.stage / 'study'
        self.protocol = read(self.stage / 'source/workloads/pay05/performance-protocol.json')
        self.corpus_path = self.stage / 'performance-corpus.json'
        self.corpus = read(self.corpus_path)
        assert self.corpus == resolve_corpus(self.protocol, read(self.stage / 'source/workloads/pay05/cases.json'))
        self.plan, self.measurement = designs(self.protocol, self.corpus, self.run_id)

    def source_guard(self):
        manifest = super().source_guard()
        assert sha(self.corpus_path) == manifest['performance_corpus_sha256']
        return manifest

    def setup(self):
        super().setup()
        # Controls hash the original stage bytes, including JSON whitespace.
        (self.output / 'source-manifest.json').write_bytes((self.stage / 'source-manifest.json').read_bytes())
        (self.output / 'performance-corpus.json').write_bytes(self.corpus_path.read_bytes())
        save(self.output / 'candidate-measurement-plan.json', {**self.measurement, 'source_commit': self.meta['source_commit'],
             'frozen_at': utc(), 'stage_stop_before': self.meta['stop_before_utc']})

    def assess_lane(self, folder, events, summary, execution, calibration, final, rows, reset):
        raw = self.docker('logs', self.names['worker']).decode()
        records = []
        for line in raw.splitlines():
            try: record = json.loads(line)
            except ValueError: continue
            if isinstance(record, dict) and record.get('type') == 'pay05_worker_run': records.append(record)
        # A restarted worker emits one report on each shutdown. Bind the latest
        # report to this generator's time interval; stale reports fail assessment.
        worker = records[-1] if records else {}
        save(folder / 'worker-records.json', worker)
        return assess_profile_lane(events, summary, execution, calibration, final, rows, reset, self.corpus, worker)

    def checked_lane(self, index, settings, calibration, name, plan):
        result = self.lane(index, settings, calibration, name=name, plan=plan)
        # Re-read the retained SQLite store, SQL history and raw generator log
        # before advancing. A driver's declaration of success is insufficient.
        checked = check_lane(self.output, name, settings, plan, self.corpus, read(self.output / 'controls.json'),
                             read(self.output / 'checkpoint.json'), calibration)
        assert checked == result
        seal(self.output / name)
        return result

    def run(self):
        self.bound(); failure = None
        status = {'status': 'running', 'study_id': 'PAY-05', 'run_id': self.run_id, 'started_at': utc(),
                  'qualification': [], 'measurements': [], 'cloud_resources_accessed': True,
                  'performance_comparison_complete': False, 'external_final_audit_required': True}
        try:
            self.setup(); save(self.output / 'controls.json', self.controls()); calibration = self.calibrate()
            for index, settings in enumerate(self.plan['lanes'], 1):
                name = 'q' + str(index) + '-' + settings['arm']
                status['current_lane'] = name; save(self.output / 'summary.json', status)
                result = self.checked_lane(index, settings, calibration, name, self.plan)
                status['qualification'].append(result); save(self.output / 'summary.json', status)
                assert result['status'] == 'passed', 'profile_qualification_gate_failed'
            qualified_gate(status['qualification'], self.plan)
            save(self.output / 'qualification-gate.json', {'status': 'passed', 'at': utc(),
                 'lanes': {r['folder']: sha(self.output / r['folder'] / 'checksums.json') for r in status['qualification']}})
            # Candidate order and parameters were already fixed before any load.
            frozen = {**read(self.output / 'candidate-measurement-plan.json'), 'qualified_at': utc(),
                      'qualification_sha256': sha(self.output / 'qualification-gate.json'),
                      'controls_sha256': sha(self.output / 'controls.json')}
            save(self.output / 'measurement-plan.json', frozen)
            plan_hash = sha(self.output / 'measurement-plan.json')
            save(self.output / 'measurement-freeze.json', {'at': utc(), 'sha256': plan_hash})
            worker_ids = {read(self.output / r['folder'] / 'worker-records.json')['execution_id'] for r in status['qualification']}
            assert len(worker_ids) == 2
            for pair, arms in enumerate(frozen['pairs'], 1):
                for position, arm in enumerate(arms, 1):
                    self.bound(); assert sha(self.output / 'measurement-plan.json') == plan_hash
                    # Leave enough time for this arm and safe shutdown. The
                    # systemd watchdog bounds a stalled runner independently.
                    remaining = (datetime.fromisoformat(self.meta['stop_before_utc']) - datetime.now(timezone.utc)).total_seconds()
                    assert remaining > frozen['warmup_s'] + frozen['measurement_s'] + 300
                    for previous in [*status['qualification'], *status['measurements']]: verify_seal(self.output / previous['folder'])
                    name = f'p{pair}-{position}-{arm}'; status['current_lane'] = name; save(self.output / 'summary.json', status)
                    result = self.checked_lane(pair, {'arm': arm, 'rate': .5}, calibration, name, frozen)
                    worker_id = read(self.output / name / 'worker-records.json')['execution_id']
                    assert worker_id not in worker_ids; worker_ids.add(worker_id)
                    status['measurements'].append(result)
                    status['comparison'] = summarize_pairs(frozen, status['measurements'])
                    save(self.output / 'summary.json', status)
                    assert result['status'] == 'passed', 'measurement_gate_failed'
            status['performance_comparison_complete'] = status['comparison']['performance_comparison_complete']
            assert status['performance_comparison_complete']
            status['status'] = 'passed'
        except BaseException as error:
            failure = error; status.update(status='failed', error_type=type(error).__name__)
            save(self.stage / 'failure.json', {'error_type': type(error).__name__, 'traceback': traceback.format_exc()})
        finally:
            closeout = self.cleanup(); save(self.stage / 'closeout.json', closeout)
            if self.output.exists():
                if self.backend.exists() and not (self.output / 'final-ledger.sqlite').exists(): sqlite_copy(self.backend, self.output / 'final-ledger.sqlite')
                status.update(owned_engine_stopped=closeout['owned_engine_stopped'], cleanup_errors=closeout['cleanup_errors'], finished_at=utc())
                if closeout['cleanup_errors'] or not closeout['owned_engine_stopped']:
                    status.update(status='failed', performance_comparison_complete=False)
                save(self.output / 'summary.json', status); save(self.output / 'engine-closeout.json', closeout); seal(self.output)
        if failure: raise failure
        assert status['status'] == 'passed'
        print(json.dumps({'status': 'passed', 'qualified_lanes': 2, 'paired_arms': 10, 'external_final_audit_required': True}), flush=True)


if __name__ == '__main__':
    def terminated(*_): raise RuntimeError('owned_performance_run_terminated')
    signal.signal(signal.SIGTERM, terminated)
    if sys.argv[1:] == ['run']: PerformanceCell(HERE).run()
    elif sys.argv[1:] == ['retire']:
        cell = PerformanceCell(HERE, cleanup_only=True); result = cell.cleanup(); save(HERE / 'retirement.json', result)
        print(json.dumps(result)); assert not result['cleanup_errors']
    else: raise SystemExit('Use run or retire from an owned stage.')
