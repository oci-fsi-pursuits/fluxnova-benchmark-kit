"""Bounded open-loop FNV-07 timing collector; acceptance is audited separately.

Each scheduled arrival includes start, immutable receipt registration, release,
and observation. The wait before release is fixture overhead, not engine work.
All HTTP responses and all scheduled arrivals survive failed/late outcomes.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from benchlib.fnv06_functional import deploy, save
from benchlib.fnv07_functional import snapshot
from services.workflow_checks import fingerprint

STUDY = 'FNV-07-TIMING-QUALIFICATION-1'
NS = 1_000_000_000


def validate(spec):
    if (spec.get('schema_version') != 1 or spec.get('study_id') != STUDY
            or spec.get('qualification_only') is not True or spec.get('case') != {'id': 'normal', 'mode': 'normal'}):
        raise ValueError('Expected the normal-case timing qualification protocol')
    for name, low, high in [('rate_per_s', 1, 10), ('arrival_seconds', 1, 10), ('workers', 1, 32)]:
        if type(spec.get(name)) is not int or not low <= spec[name] <= high:
            raise ValueError('Unbounded qualification setting: ' + name)
    # Complete final inventories use a 1000-row bound; at most 60 workflows
    # keep the normal-case activity/variable/job inventories below that bound.
    if spec['rate_per_s'] * spec['arrival_seconds'] > 60:
        raise ValueError('Qualification population exceeds the inventory bound')
    for name, expected in [('poll_seconds', .1), ('http_timeout_seconds', 5), ('drain_seconds', 20),
                           ('maximum_dispatch_lag_seconds', .25), ('deadline_seconds', 2)]:
        if spec.get(name) != expected:
            raise ValueError('Timing qualification boundary changed: ' + name)
    return spec['rate_per_s'] * spec['arrival_seconds']


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class Trace:
    """A single original monotonic clock; requests may finish out of order."""
    def __init__(self, path, timeout):
        self.path, self.timeout = Path(path), timeout
        self.lock, self.index = threading.Lock(), 0
        self.origin_ns = time.monotonic_ns()
        self.epoch_ns = time.time_ns()
        self.stream = self.path.open('x', encoding='utf-8')

    def now(self):
        return time.monotonic_ns() - self.origin_ns

    def close(self):
        self.stream.close()

    def request(self, arrival, service, base, auth, method, path, body=None):
        with self.lock:
            index = self.index
            self.index += 1
        row = dict(index=index, arrival=arrival, service=service, method=method, path=path, body=body,
                   started_offset_ns=self.now())
        headers = {'Accept': 'application/json'}
        headers['Authorization' if service == 'engine' else 'X-Fnv07-Token'] = auth
        raw = None if body is None else json.dumps(body).encode()
        if raw is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(base.rstrip('/') + path, data=raw, headers=headers, method=method)
        try:
            # Per-call opener avoids shared handler state; no redirect or retry.
            opener = urllib.request.build_opener(NoRedirect())
            try:
                response = opener.open(request, timeout=self.timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                row['status'] = response.code
                content = response.read(1_048_577)
                if len(content) > 1_048_576:
                    raise ValueError('bounded_response_exceeded')
                row['response'] = json.loads(content) if content else None
        except Exception as error:
            # No exception text: it may include caller credentials or payloads.
            row.update(transport_error=type(error).__name__)
        finally:
            row['finished_offset_ns'] = self.now()
            with self.lock:
                self.stream.write(json.dumps(row, allow_nan=False) + '\n')
                self.stream.flush()
        return row


def observed_correct(history, variables, receipt, *, process_id, case_key, digest):
    """Collector hint only. The saved-evidence auditor recomputes this result."""
    if not isinstance(history, dict) or (history.get('id'), history.get('businessKey'), history.get('state')) != (process_id, case_key, 'COMPLETED') or not history.get('endTime'):
        return False
    if not isinstance(variables, list) or not isinstance(receipt, dict):
        return False
    if any(v.get('processInstanceId') != process_id for v in variables) or len({v.get('name') for v in variables}) != len(variables):
        return False
    values = {v.get('name'): v.get('value') for v in variables}
    binding = receipt.get('case') or {}
    effects = receipt.get('effects', [])
    if (binding.get('case_key'), binding.get('process_id'), binding.get('business_key'), binding.get('input_sha256')) != (case_key, process_id, case_key, digest):
        return False
    if values.get('completedStages') != 3 or values.get('fnv07InputSha256') != digest or values.get('fnv07CaseKey') != case_key:
        return False
    return (len(effects) == 3 and {e.get('stage') for e in effects} == {'stage1', 'stage2', 'stage3'}
            and all(e.get('case_key') == case_key and e.get('process_id') == process_id and e.get('input_sha256') == digest
                    and values.get(e['stage'] + 'Receipt') == e.get('receipt_id') for e in effects))


def run_arrivals(engine, probe, output, run_id, spec):
    count = validate(spec)
    trace = Trace(Path(output) / 'load-requests.ndjson', spec['http_timeout_seconds'])
    start = trace.now() + NS // 2
    end = start + (spec['arrival_seconds'] + spec['drain_seconds']) * NS
    planned = [dict(index=i, scheduled_offset_ns=start + i * NS // spec['rate_per_s'],
                    case_key=run_id + '-arrival-' + str(i).zfill(4)) for i in range(count)]
    record = dict(study_id=STUDY, run_id=run_id, protocol=spec, origin_monotonic_ns=trace.origin_ns,
                  origin_epoch_ns=trace.epoch_ns, clock=time.get_clock_info('monotonic').__dict__,
                  planned=planned, arrival_start_offset_ns=start, drain_end_offset_ns=end,
                  engine_url=engine.base_url, probe_url=probe.base_url, outcomes=[])
    save(Path(output) / 'load-plan.json', record)
    slots = threading.BoundedSemaphore(spec['workers'])
    results, futures = {}, []
    digest = fingerprint(spec['case'])

    def exercise(item):
        result = dict(item, worker_started_offset_ns=trace.now(), status='started', requests=[])
        def call(service, method, path, body=None):
            base, auth = ((engine.base_url, engine.auth) if service == 'engine' else (probe.base_url, probe.token))
            row = trace.request(item['index'], service, base, auth, method, path, body)
            result['requests'].append(row['index'])
            return row
        def succeeded(row, code=200):
            return row.get('status') == code and 'transport_error' not in row
        try:
            # Late worker execution is a missed arrival, never catch-up traffic.
            if result['worker_started_offset_ns'] - item['scheduled_offset_ns'] > spec['maximum_dispatch_lag_seconds'] * NS:
                result['status'] = 'missed_worker_late'
                return result
            body = {'businessKey': item['case_key'], 'variables': {
                'completedStages': {'value': 0, 'type': 'Integer'},
                'fnv07RunId': {'value': run_id, 'type': 'String'},
                'fnv07CaseKey': {'value': item['case_key'], 'type': 'String'},
                'fnv07InputSha256': {'value': digest, 'type': 'String'}}}
            started = call('engine', 'POST', '/process-definition/key/bench_fnv07_jobs/start', body)
            if not succeeded(started) or not isinstance(started.get('response'), dict) or not started['response'].get('id'):
                result['status'] = 'start_failed_or_unknown'
                return result
            pid = result['process_id'] = started['response']['id']
            registered = call('probe', 'POST', '/register', dict(case_key=item['case_key'], process_id=pid,
                business_key=item['case_key'], case=spec['case']))
            if not succeeded(registered):
                result['status'] = 'registration_failed_or_unknown'
                return result
            release = call('engine', 'POST', '/message', {'messageName': 'fnv07-release', 'businessKey': item['case_key']})
            if not succeeded(release, 204):
                result['status'] = 'release_failed_or_unknown'
                return result
            encoded = urllib.parse.quote(pid, safe='')
            while trace.now() < end:
                history = call('engine', 'GET', '/history/process-instance/' + encoded)
                if succeeded(history) and isinstance(history.get('response'), dict) and history['response'].get('state') == 'COMPLETED':
                    variables = call('engine', 'GET', '/history/variable-instance?processInstanceId=' + encoded + '&maxResults=100')
                    receipts = call('probe', 'POST', '/observe', {'case_key': item['case_key']})
                    if succeeded(variables) and succeeded(receipts) and observed_correct(history['response'], variables.get('response'),
                            receipts.get('response'), process_id=pid, case_key=item['case_key'], digest=digest):
                        result.update(status='observed_correct_pending_audit', observed_offset_ns=receipts['finished_offset_ns'],
                                      observation_requests=[history['index'], variables['index'], receipts['index']])
                        return result
                time.sleep(min(spec['poll_seconds'], max(0, (end - trace.now()) / NS)))
            result['status'] = 'drain_expired'
            return result
        except Exception as error:
            result.update(status='collector_error', error_type=type(error).__name__)
            return result
        finally:
            result['finished_offset_ns'] = trace.now()
            slots.release()

    try:
        with ThreadPoolExecutor(max_workers=spec['workers'], thread_name_prefix='fnv07-arrival') as pool:
            for item in planned:
                pause = (item['scheduled_offset_ns'] - trace.now()) / NS
                if pause > 0:
                    time.sleep(pause)
                item = dict(item, dispatch_offset_ns=trace.now())
                if item['dispatch_offset_ns'] - item['scheduled_offset_ns'] > spec['maximum_dispatch_lag_seconds'] * NS:
                    results[item['index']] = dict(item, status='missed_dispatch_late', requests=[])
                elif not slots.acquire(blocking=False):
                    results[item['index']] = dict(item, status='missed_no_worker', requests=[])
                else:
                    try:
                        futures.append((item['index'], pool.submit(exercise, item)))
                    except BaseException:
                        slots.release()
                        raise
            for index, future in futures:
                results[index] = future.result()
    finally:
        trace.close()
        # A missing result remains explicitly unattempted in the denominator.
        record['outcomes'] = [results.get(i, dict(planned[i], status='unattempted_collector_abort', requests=[])) for i in range(count)]
        record['finished_offset_ns'] = trace.now()
        save(Path(output) / 'load-collection.json', record)
    return record


def collect(api, probe, spec, model, output, run_id, summary, **_kwargs):
    validate(spec)
    deploy(api, model, None)
    save(output / 'initial-snapshot.json', snapshot(api))
    save(output / 'initial-probe.json', probe.snapshot())
    result = run_arrivals(api.engine, probe, output, run_id, spec)
    save(output / 'final-snapshot.json', snapshot(api))
    save(output / 'final-probe.json', probe.snapshot())
    summary.update(status='collected_pending_independent_audit', arrivals=len(result['planned']),
                   qualification_only=True, performance_comparison_complete=False)
