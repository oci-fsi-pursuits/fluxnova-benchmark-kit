"""Paired local Java/Python HTTP diagnostic, independent of Fluxnova scheduling."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.cluster_probe import ProbeStore, make_server


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reconcile(client_text, events, requests):
    rows = []
    for line in client_text.splitlines():
        number, outcome, status, duration, error = line.split('\t')
        rows.append(dict(id=int(number), outcome=outcome, status=int(status), duration_ns=int(duration), error=error))
    if any(r['outcome'] not in ('ok', 'bad_response', 'error') or r['duration_ns'] < 0
           or (r['outcome'] == 'ok' and (r['status'] != 200 or r['error'] != 'none')) for r in rows):
        raise ValueError('invalid_client_result')
    ids = [r['id'] for r in rows]
    server_ids = [e['request_id'] for e in events if e['event'] == 'request_received' and 'request_id' in e]
    written = [e['request_id'] for e in events if e['event'] == 'response_sent' and 'request_id' in e]
    complete = sorted(ids) == list(range(requests))
    return dict(planned_requests=requests, client_rows=len(rows), client_denominator_complete=complete,
                client_outcomes=dict(Counter(r['outcome'] for r in rows)),
                client_error_classes=dict(Counter(r['error'] for r in rows if r['outcome'] == 'error')),
                server_requests=len(server_ids), server_responses_written=len(written),
                repeated_server_request_ids=sum(n - 1 for n in Counter(server_ids).values()),
                client_ids_without_server_request=sorted(set(ids) - set(server_ids)),
                server_ids_without_client_row=sorted(set(server_ids) - set(ids)),
                server_error_classes=dict(Counter(e['error_class'] for e in events if 'error_class' in e)),
                all_roundtrips_verified=complete and all(r['outcome'] == 'ok' for r in rows)
                    and sorted(server_ids) == sorted(written) == list(range(requests)))


def run(args):
    output = args.output.resolve()
    if not 1 <= args.requests <= 1000 or not 1 <= args.concurrency <= 16 or not 1 <= args.repeats <= 5:
        raise ValueError('bounded_diagnostic_required')
    java, javac = args.java.resolve(), args.javac.resolve()
    if not java.is_file() or not javac.is_file():
        raise ValueError('explicit_java_toolchain_required')
    version = subprocess.check_output([str(javac), '-version'], text=True).strip()
    if not version.startswith('javac 21.'):
        raise ValueError('Java_21_required')
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / 'inputs'
    for name in ('services/fnv07/HttpProbeClient.java', 'services/cluster_probe.py',
                 'services/transport_diagnostics.py', 'services/workflow_checks.py', 'scripts/run_fnv07_http_probe.py'):
        path = inputs / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    classes = output / 'classes'; classes.mkdir()
    with (output / 'compiler.log').open('wb') as log:
        subprocess.run([str(javac), '--release', '21', '-encoding', 'UTF-8', '-d', str(classes),
                        str(inputs / 'services/fnv07/HttpProbeClient.java')], stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=60)
    manifest = dict(scope='HTTP diagnostic only; no workflow, payment, capacity or performance claim.',
                    requests_per_arm=args.requests, concurrency=args.concurrency, repeats=args.repeats,
                    connection_modes=['default', 'explicit_close'], retry_connect=False,
                    python_version=sys.version, compiler_version=version,
                    java_version=subprocess.run([str(java), '-version'], capture_output=True, text=True, check=True).stderr.strip(),
                    java_sha256=sha(java), javac_sha256=sha(javac), cloud_resources_accessed=False,
                    source_sha256={p.relative_to(inputs).as_posix(): sha(p) for p in inputs.rglob('*') if p.is_file()})
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    arms = []
    try:
        for repeat in range(args.repeats):
            # Alternate order to avoid always giving one arm the first run.
            for close in ((False, True) if repeat % 2 == 0 else (True, False)):
                arm = output / f'pair-{repeat + 1}-' / ('explicit-close' if close else 'default')
                arm.mkdir(parents=True)
                server = make_server(ProbeStore(arm / 'receipts.sqlite', 'http-probe', ['n1']),
                                     admin_token=secrets.token_hex(24), execute_token=(token := secrets.token_hex(24)),
                                     diagnostics_path=arm / 'transport.ndjson', connection_close=close)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                exit_code = None
                try:
                    with (arm / 'client.tsv').open('wb') as out, (arm / 'client.stderr').open('wb') as err:
                        process = subprocess.run([str(java), '-Djdk.httpclient.disableRetryConnect=true',
                                '-cp', str(classes), 'HttpProbeClient',
                                f'http://127.0.0.1:{server.server_port}/node', str(args.concurrency), str(args.requests)],
                                stdout=out, stderr=err, env=dict(os.environ, FNV07_EXECUTE_TOKEN=token),
                                cwd=arm, timeout=min(300, 30 + 10 * args.requests))
                        exit_code = process.returncode
                finally:
                    server.shutdown(); server.server_close(); thread.join(timeout=5)
                    (arm / 'cleanup.json').write_text(json.dumps({'owned_server_stopped': not thread.is_alive(),
                                                                 'client_exit_code': exit_code}), encoding='utf-8')
                events = [json.loads(line) for line in (arm / 'transport.ndjson').read_text().splitlines()]
                result = reconcile((arm / 'client.tsv').read_text(), events, args.requests)
                result.update(pair=repeat + 1, mode='explicit_close' if close else 'default',
                              owned_server_stopped=not thread.is_alive(), client_exit_code=exit_code)
                result['all_roundtrips_verified'] &= exit_code == 0 and not thread.is_alive()
                arms.append(result)
                (arm / 'summary.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    finally:
        summary = dict(scope=manifest['scope'], arms=arms, completed_arms=len(arms), planned_arms=2 * args.repeats)
        (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        seal = {p.relative_to(output).as_posix(): sha(p) for p in output.rglob('*') if p.is_file()}
        (output / 'checksums.json').write_text(json.dumps({'files': seal}, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


def audit_probe(folder):
    """Verify saved bytes and independently recompute every per-arm count."""
    folder = Path(folder).resolve()
    seal = json.loads((folder / 'checksums.json').read_text())['files']
    required = {'manifest.json', 'summary.json', 'classes/HttpProbeClient.class',
                'inputs/services/fnv07/HttpProbeClient.java', 'inputs/scripts/run_fnv07_http_probe.py'}
    if not required.issubset(seal):
        raise ValueError('incomplete_probe_seal')
    for name, digest in seal.items():
        path = (folder / name).resolve()
        if not path.is_relative_to(folder) or sha(path) != digest:
            raise ValueError('changed_probe_evidence')
    manifest = json.loads((folder / 'manifest.json').read_text())
    for name, digest in manifest['source_sha256'].items():
        if seal.get('inputs/' + name) != digest:
            raise ValueError('probe_source_binding')
    saved = json.loads((folder / 'summary.json').read_text())
    expected_arms = 2 * manifest['repeats']
    if saved['planned_arms'] != expected_arms or saved['completed_arms'] != expected_arms:
        raise ValueError('incomplete_probe_population')
    results = []
    for repeat in range(manifest['repeats']):
        for close in ((False, True) if repeat % 2 == 0 else (True, False)):
            arm = folder / f'pair-{repeat + 1}-' / ('explicit-close' if close else 'default')
            for name in ('client.tsv', 'transport.ndjson', 'cleanup.json', 'summary.json'):
                if (arm / name).relative_to(folder).as_posix() not in seal:
                    raise ValueError('missing_sealed_probe_file')
            events = [json.loads(line) for line in (arm / 'transport.ndjson').read_text().splitlines()]
            if [event['seq'] for event in events] != list(range(1, len(events) + 1)):
                raise ValueError('probe_trace_sequence')
            result = reconcile((arm / 'client.tsv').read_text(), events, manifest['requests_per_arm'])
            cleanup = json.loads((arm / 'cleanup.json').read_text())
            result.update(pair=repeat + 1, mode='explicit_close' if close else 'default', **cleanup)
            result['all_roundtrips_verified'] &= cleanup['client_exit_code'] == 0 and cleanup['owned_server_stopped']
            if result != json.loads((arm / 'summary.json').read_text()):
                raise ValueError('changed_probe_summary')
            results.append(result)
    if results != saved['arms']:
        raise ValueError('changed_probe_population')
    return dict(seal_verified=True, counts_reconciled=True, arms=results,
                scope='Diagnostic reconciliation, including failed calls; not performance qualification.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--java', type=Path, required=True)
    parser.add_argument('--javac', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--requests', type=int, default=40)
    parser.add_argument('--concurrency', type=int, default=2)
    parser.add_argument('--repeats', type=int, default=2)
    run(parser.parse_args())
