"""Summarize sealed FNV-07 evidence without altering it or relaxing its auditor."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchlib.fnv07_audit import parse_delegate_log
from benchlib.fnv07_timing_audit import audit


def diagnose(folder):
    folder = Path(folder).resolve()
    seal = json.loads((folder / 'checksums.json').read_text())['files']
    for name, expected in seal.items():
        target = (folder / name).resolve()
        if not target.is_relative_to(folder) or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError('evidence_seal_mismatch')
    required = {'engine-n1.log', 'final-probe.json', 'final-snapshot.json', 'probe-snapshot.sqlite',
                'load-plan.json', 'load-collection.json', 'load-requests.ndjson'}
    if not required.issubset(seal):
        raise ValueError('incomplete_timing_evidence_seal')
    logs = parse_delegate_log((folder / 'engine-n1.log').read_text(encoding='utf-8'))
    final = json.loads((folder / 'final-snapshot.json').read_text())
    # The sealed backup is self-contained and closed; immutable mode prevents
    # SQLite trying to create WAL/SHM files beside read-only historical evidence.
    with sqlite3.connect((folder / 'probe-snapshot.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        counts = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                  for table in ('cases', 'attempts', 'effects')}
    timing_result = None
    try:
        timing_result = audit(folder)
        qualification = 'passed'
        reason = None
    except AssertionError as error:
        qualification = 'failed'
        # Auditors use fixed reason codes; never expose arbitrary exception text.
        reason = str(error) if str(error).replace('_', '').isalnum() else 'audit_rejected'
    except Exception as error:
        qualification, reason = 'error', type(error).__name__
    trace = folder / 'transport.ndjson'
    events = []
    if trace.exists():
        if trace.name not in seal:
            raise ValueError('unsealed_transport_trace')
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        if [row['seq'] for row in events] != list(range(1, len(events) + 1)):
            raise ValueError('diagnostic_sequence_gap')
    return {'scope': 'Local timing diagnostic only; no capacity, scale-out or transport root-cause claim.',
            'timing_qualification': qualification, 'audit_reason': reason, 'seal_verified': True,
            'timing_audit': timing_result,
            'delegate_attempts': len(logs['ATTEMPT']), 'delegate_successes': len(logs['SUCCESS']),
            'delegate_failures': len(logs['FAILURE']), 'backend': counts,
            'history_rows': final['history']['count'],
            'completed_workflows': sum(row.get('state') == 'COMPLETED' for row in final['history']['rows']),
            'job_log_rows': final['job_log']['count'],
            'trace_captured': trace.exists(), 'trace_events': dict(Counter(row['event'] for row in events)),
            'server_error_classes': dict(Counter(row['error_class'] for row in events if 'error_class' in row)),
            'note': 'response_sent means server write returned, not client receipt or committed engine state.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.folder.resolve()):
        parser.error('Keep the diagnostic report outside original evidence')
    result = diagnose(args.folder)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result, indent=2))
