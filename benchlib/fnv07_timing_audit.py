"""Audit saved FNV-07 scheduled-arrival timing, never infer it from counts.

This is a small normal-case qualification gate. A successful gate is not a
resource qualification, PostgreSQL comparison or capacity result.
"""
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote

from benchlib.fnv07_audit import canonical, digest, parse_delegate_log, require
from benchlib.fnv07_load import STUDY, NS, validate

STAGES = ('stage1', 'stage2', 'stage3')


def check_observation(history, variables, receipt, *, run_id, key, pid, case):
    input_hash = digest(canonical(case).encode())
    require(history['id'] == pid and history['businessKey'] == key and history['state'] == 'COMPLETED'
            and history['endTime'] and history.get('deleteReason') is None, 'original_process_not_completed')
    require(receipt['case'] == dict(case_key=key, process_id=pid, business_key=key,
            body=canonical(case), input_sha256=input_hash), 'observation_original_registration')
    effects = receipt['effects']
    require(len(effects) == 3 and {e['stage'] for e in effects} == set(STAGES), 'missing_or_duplicate_receipt')
    wanted = dict(completedStages=3, fnv07RunId=run_id, fnv07CaseKey=key, fnv07InputSha256=input_hash)
    for effect in effects:
        identity = run_id + '|' + key + '|' + pid + '|' + effect['stage']
        require(effect['case_key'] == key and effect['process_id'] == pid and effect['input_sha256'] == input_hash,
                'receipt_input_binding')
        require(effect['effect_id'] == digest(('effect|' + identity).encode())
                and effect['receipt_id'] == digest(('receipt|' + identity).encode()), 'receipt_identity')
        wanted[effect['stage'] + 'Receipt'] = effect['receipt_id']
    require(len(variables) == len(wanted) and all(v['processInstanceId'] == pid for v in variables), 'variable_population')
    require({v['name']: v['value'] for v in variables} == wanted, 'committed_variables_not_correct')
    return effects


def check_trace(plan, collected, rows):
    spec = plan['protocol']
    count = validate(spec)
    require(plan['study_id'] == collected['study_id'] == STUDY, 'wrong_study')
    require({k: collected[k] for k in plan} == {**plan, 'outcomes': collected['outcomes']}, 'changed_original_plan')
    require(plan['outcomes'] == [], 'plan_already_has_results')
    require(plan['clock']['monotonic'] is True and plan['clock']['adjustable'] is False, 'not_monotonic_clock')
    require(type(collected['finished_offset_ns']) is int and collected['finished_offset_ns'] > 0, 'invalid_collection_clock')
    for key in ('origin_monotonic_ns', 'origin_epoch_ns', 'arrival_start_offset_ns', 'drain_end_offset_ns'):
        require(type(plan[key]) is int and plan[key] > 0, 'invalid_clock_anchor')
    require(plan['drain_end_offset_ns'] == plan['arrival_start_offset_ns'] + (spec['arrival_seconds'] + spec['drain_seconds']) * NS,
            'drain_changed')
    run_id = plan['run_id']
    expected = [dict(index=i, scheduled_offset_ns=plan['arrival_start_offset_ns'] + i * NS // spec['rate_per_s'],
                     case_key=run_id + '-arrival-' + str(i).zfill(4)) for i in range(count)]
    require(plan['planned'] == expected and len(collected['outcomes']) == count, 'original_arrival_denominator')
    require(sorted(r['index'] for r in rows) == list(range(len(rows))), 'request_sequence_missing_or_duplicate')
    requests = {r['index']: r for r in rows}
    referenced = []
    latencies, missed, failed, http_failures, observation_bindings = [], 0, 0, 0, []
    for row in rows:
        require(type(row['arrival']) is int and 0 <= row['arrival'] < count, 'request_outside_population')
        require(type(row['started_offset_ns']) is int and type(row['finished_offset_ns']) is int
                and 0 <= row['started_offset_ns'] <= row['finished_offset_ns'] <= collected['finished_offset_ns'], 'request_clock')
        http_failures += row.get('status') not in (200, 204) or 'transport_error' in row
    for item, outcome in zip(expected, collected['outcomes'], strict=True):
        require(all(outcome.get(k) == v for k, v in item.items()), 'arrival_schedule_or_identity_changed')
        indices = outcome['requests']
        require(indices == sorted(set(indices)) and all(i in requests for i in indices), 'invalid_request_reference')
        referenced.extend(indices)
        selected = [requests[i] for i in indices]
        require(all(r['arrival'] == item['index'] for r in selected), 'cross_arrival_request')
        require(all(a['finished_offset_ns'] <= b['started_offset_ns'] for a, b in zip(selected, selected[1:])), 'request_causal_order')
        if outcome['status'].startswith('missed_') or outcome['status'] == 'unattempted_collector_abort':
            require(not selected and 'observed_offset_ns' not in outcome, 'missed_arrival_has_traffic_or_latency')
            missed += 1
            continue
        require(item['scheduled_offset_ns'] <= outcome['dispatch_offset_ns'] <= outcome['worker_started_offset_ns'], 'early_or_reversed_dispatch')
        require(outcome['worker_started_offset_ns'] - item['scheduled_offset_ns'] <= spec['maximum_dispatch_lag_seconds'] * NS,
                'late_worker_generated_catchup')
        require(all(outcome['worker_started_offset_ns'] <= r['started_offset_ns'] <= r['finished_offset_ns'] <= outcome['finished_offset_ns'] for r in selected),
                'request_outside_worker_lifetime')
        if outcome['status'] != 'observed_correct_pending_audit':
            require('observed_offset_ns' not in outcome, 'failed_arrival_has_latency')
            failed += 1
            continue
        require(len(selected) >= 6, 'missing_original_requests')
        started, registered, released = selected[:3]
        pid, key = outcome['process_id'], item['case_key']
        input_hash = digest(canonical(spec['case']).encode())
        start_body = {'businessKey': key, 'variables': {
            'completedStages': {'value': 0, 'type': 'Integer'}, 'fnv07RunId': {'value': run_id, 'type': 'String'},
            'fnv07CaseKey': {'value': key, 'type': 'String'}, 'fnv07InputSha256': {'value': input_hash, 'type': 'String'}}}
        require((started['service'], started['method'], started['path'], started['body'], started.get('status')) ==
                ('engine', 'POST', '/process-definition/key/bench_fnv07_jobs/start', start_body, 200)
                and started['response']['id'] == pid, 'original_start_binding')
        require((registered['service'], registered['method'], registered['path'], registered['body'], registered.get('status')) ==
                ('probe', 'POST', '/register', dict(case_key=key, process_id=pid, business_key=key, case=spec['case']), 200),
                'original_registration_request')
        require(registered['response'] == dict(case_key=key, process_id=pid, input_sha256=input_hash), 'registration_response_binding')
        require((released['service'], released['method'], released['path'], released['body'], released.get('status'), released.get('response')) ==
                ('engine', 'POST', '/message', {'messageName': 'fnv07-release', 'businessKey': key}, 204, None), 'original_release_binding')
        require(not any('transport_error' in r for r in (started, registered, released)), 'unknown_mutation_outcome')
        encoded = quote(pid, safe='')
        history_path = '/history/process-instance/' + encoded
        variable_path = '/history/variable-instance?processInstanceId=' + encoded + '&maxResults=100'
        # After release there are only reads; no hidden start/release retries.
        for request in selected[3:]:
            require((request['service'], request['method'], request['path'], request['body']) in (
                ('engine', 'GET', history_path, None), ('engine', 'GET', variable_path, None),
                ('probe', 'POST', '/observe', {'case_key': key})), 'unexpected_or_retried_mutation')
        require(outcome['observation_requests'] == indices[-3:], 'completion_not_final_original_observation')
        history, variables, receipts = selected[-3:]
        require([(r['service'], r['path'], r.get('status')) for r in (history, variables, receipts)] ==
                [('engine', history_path, 200), ('engine', variable_path, 200), ('probe', '/observe', 200)], 'observation_routes_or_status')
        require(not any('transport_error' in r for r in (history, variables, receipts)), 'observation_transport_error')
        check_observation(history['response'], variables['response'], receipts['response'], run_id=run_id, key=key, pid=pid, case=spec['case'])
        require(outcome['observed_offset_ns'] == receipts['finished_offset_ns'] <= plan['drain_end_offset_ns'], 'completion_clock_or_drain')
        latencies.append((outcome['observed_offset_ns'] - item['scheduled_offset_ns']) / NS)
        observation_bindings.append(dict(process_id=pid, case_key=key, receipt=receipts['response']))
    require(sorted(referenced) == list(range(len(rows))), 'unattributed_or_hidden_requests')
    return dict(planned_arrivals=count, correct_observed=len(latencies), missed_arrivals=missed, failed_arrivals=failed,
                original_http_failures=http_failures, timely_observed=sum(t <= spec['deadline_seconds'] for t in latencies),
                observation_latency_seconds=latencies, bindings=observation_bindings,
                driver_qualified=len(latencies) == count and http_failures == 0)


def audit(folder):
    folder = Path(folder).resolve()
    read = lambda name: json.loads((folder / name).read_text(encoding='utf-8'))
    seal = read('checksums.json')['files']
    needed = {'manifest.json', 'collection.json', 'load-plan.json', 'load-collection.json', 'load-requests.ndjson',
              'requests.ndjson', 'initial-snapshot.json', 'final-snapshot.json', 'initial-probe.json', 'final-probe.json',
              'probe-snapshot.sqlite', 'engine-n1.log', 'delegate-build.json', 'engine-version.json',
              'inputs/workloads/fnv07/load-qualification.json', 'inputs/services/fnv07/RecordingDelegate.java',
              'userlib/fnv07-delegate.jar'}
    require(needed <= set(seal), 'incomplete_evidence_seal')
    for name, expected in seal.items():
        path = (folder / name).resolve()
        require(path.is_relative_to(folder) and path.is_file() and digest(path.read_bytes()) == expected, 'changed_sealed_evidence')
    manifest, collection = read('manifest.json'), read('collection.json')
    require(manifest['study_id'] == collection['study_id'] == STUDY and manifest['nodes'] == ['n1']
            and manifest['database'] == 'fresh embedded H2' and manifest['qualification_only'] is True,
            'local_qualification_scope')
    require(collection['status'] == 'collected_pending_independent_audit' and collection['owned_engine_stopped']
            and collection['owned_probe_stopped'] and collection['cloud_resources_accessed'] is False, 'unclosed_collection')
    require(read('engine-version.json') == {'version': '3.0.1'}, 'wrong_engine_release')
    build = read('delegate-build.json')
    require(build['artifact_sha256'] == digest((folder / 'userlib/fnv07-delegate.jar').read_bytes()) == manifest['delegate_sha256']
            and build['source_sha256'] == digest((folder / 'inputs/services/fnv07/RecordingDelegate.java').read_bytes()), 'delegate_build_binding')
    for name, expected in manifest['source_sha256'].items():
        require('inputs/' + name in seal and seal['inputs/' + name] == expected, 'source_input_binding')
    plan, collected = read('load-plan.json'), read('load-collection.json')
    require(plan['run_id'] == manifest['run_id'] == collection['run_id']
            and plan['protocol'] == read('inputs/workloads/fnv07/load-qualification.json'), 'original_protocol_binding')
    result = check_trace(plan, collected, [json.loads(line) for line in (folder / 'load-requests.ndjson').read_text().splitlines()])
    require(result['driver_qualified'], 'missed_failed_or_unverified_arrivals_retained')
    requests = [json.loads(line) for line in (folder / 'requests.ndjson').read_text().splitlines()]
    require([r['index'] for r in requests] == list(range(len(requests))) and all(r['status'] in (200, 204) for r in requests), 'setup_or_final_read_failure')
    initial, final, probe = read('initial-snapshot.json'), read('final-snapshot.json'), read('final-probe.json')
    for snapshot in (initial, final):
        for name, entry in snapshot.items():
            require(entry['rows'] == requests[entry['list_request']]['response']
                    and len(entry['rows']) == entry['count'] == requests[entry['count_request']]['response']['count'], 'inventory_evidence_not_counted')
    for name in ('instances', 'history', 'activities', 'variables', 'subscriptions', 'jobs', 'job_log', 'incidents'):
        require(initial[name]['count'] == 0, 'nonempty_initial_workload')
    require(all(not read('initial-probe.json')[k] for k in ('cases', 'effects', 'attempts')), 'nonempty_initial_probe')
    for name in ('instances', 'subscriptions', 'jobs', 'incidents'):
        require(final[name]['count'] == 0, 'unfinished_or_blocked_work')
    count = result['planned_arrivals']
    require(len(probe['cases']) == final['history']['count'] == count and len(probe['effects']) == 3 * count, 'final_population_mismatch')
    ids = {b['process_id'] for b in result['bindings']}
    require(len(ids) == count and {r['id'] for r in final['history']['rows']} == ids, 'final_process_ids')
    require(dict((r['key'], r['value']) for r in probe['metadata']) == {'run_id': manifest['run_id'], 'nodes': canonical(['n1'])}, 'probe_run_binding')
    logs = parse_delegate_log((folder / 'engine-n1.log').read_text(encoding='utf-8', errors='strict'))
    require(len(probe['nodes']) == len(logs['NODE']) == 1 and probe['nodes'][0]['node_id'] == 'n1', 'node_not_attributed')
    require(logs['NODE'][0] == {'node_id': 'n1', **json.loads(probe['nodes'][0]['body'])}, 'node_config_log_binding')
    configuration = json.loads(probe['nodes'][0]['body'])
    wanted_config = {'timezone': 'UTC', 'history.level': 'full', 'job.deployment_aware': 'false',
        'job.isActive': 'true', 'job.getMaxJobsPerAcquisition': '2', 'job.getLockTimeInMillis': '30000',
        'job.getWaitTimeInMillis': '100', 'job.getMaxWait': '500', 'job.getBackoffTimeInMillis': '100',
        'job.getMaxBackoff': '500', 'pool.getCorePoolSize': '2', 'pool.getMaxPoolSize': '2',
        'pool.getQueueCapacity': '2', 'database.isolation': '2', 'database.product': 'H2'}
    require(all(configuration.get(k) == v for k, v in wanted_config.items()), 'effective_executor_configuration')
    attempts = probe['attempts']
    require([a['seq'] for a in attempts] == list(range(1, len(attempts) + 1)), 'missing_attempt')
    # This deliberately narrow gate requires no retries. A later load pilot
    # must retain and reconcile retries rather than silently reuse this gate.
    require(len(attempts) == len(logs['ATTEMPT']) == len(logs['SUCCESS']) == 3 * count
            and not logs['FAILURE'] and final['job_log']['count'] == 6 * count, 'unexpected_attempt_or_job_outcomes')
    require(len({a['invocation_id'] for a in attempts}) == len(attempts), 'duplicate_invocation')
    for binding in result['bindings']:
        key, pid = binding['case_key'], binding['process_id']
        receipt = dict(case=next(c for c in probe['cases'] if c['case_key'] == key),
                       effects=[e for e in probe['effects'] if e['case_key'] == key])
        require(receipt == binding['receipt'], 'observed_receipt_differs_from_durable_ledger')
        check_observation(next(h for h in final['history']['rows'] if h['id'] == pid),
            [v for v in final['variables']['rows'] if v['processInstanceId'] == pid], receipt,
            run_id=manifest['run_id'], key=key, pid=pid, case=plan['protocol']['case'])
        activities = [a for a in final['activities']['rows'] if a['processInstanceId'] == pid]
        require(len(activities) == 6 and {a['activityId'] for a in activities} == {'start', 'wait', *STAGES, 'end'}
                and all(a['endTime'] and not a['canceled'] for a in activities), 'committed_activity_population')
        jobs = [j for j in final['job_log']['rows'] if j['processInstanceId'] == pid]
        require(len(jobs) == 6 and not any(j['failureLog'] or j['deletionLog'] for j in jobs), 'unexpected_job_failure_or_deletion')
        require(all(sum(bool(j[k]) for k in ('creationLog', 'successLog', 'failureLog', 'deletionLog')) == 1 for j in jobs), 'ambiguous_job_outcome')
        for effect in receipt['effects']:
            stage = effect['stage']
            stage_jobs = [j for j in jobs if j['activityId'] == stage]
            require(len(stage_jobs) == 2 and sum(j['creationLog'] for j in stage_jobs) == 1
                    and sum(j['successLog'] for j in stage_jobs) == 1 and len({j['jobId'] for j in stage_jobs}) == 1, 'stage_job_identity')
            matching = [a for a in attempts if a['case_key'] == key and a['stage'] == stage]
            require(len(matching) == 1, 'stage_attempt_population')
            attempt = matching[0]
            request = json.loads(attempt['request'])
            wanted = dict(run_id=manifest['run_id'], case_key=key, process_id=pid, business_key=key,
                          input_sha256=receipt['case']['input_sha256'], stage=stage, node_id='n1', invocation_id=effect['first_invocation_id'])
            require(request == wanted and logs['ATTEMPT'].count(wanted) == 1, 'attempt_original_delegate_binding')
            require(attempt['status'] == 200 and attempt['disposition'] == 'committed' and attempt['receipt_id'] == effect['receipt_id']
                    and attempt['invocation_id'] == effect['first_invocation_id'] and attempt['started_ns'] <= attempt['finished_ns'], 'effect_commit_not_reconciled')
            require(logs['SUCCESS'].count({**wanted, 'receipt_id': effect['receipt_id'], 'disposition': 'committed'}) == 1, 'delegate_success_not_reconciled')
    # This checksum-verified backup is closed and self-contained. Do not create
    # SQLite WAL/SHM files beside historical evidence during a read-only audit.
    with sqlite3.connect((folder / 'probe-snapshot.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        db.row_factory = sqlite3.Row
        require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'ledger_integrity')
        for table, order in [('metadata', 'key'), ('nodes', 'node_id'), ('cases', 'case_key'), ('effects', 'case_key,stage'), ('attempts', 'seq')]:
            require([dict(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY ' + order)] == probe[table], 'sqlite_not_original_probe')
    return {k: v for k, v in result.items() if k not in ('bindings', 'observation_latency_seconds')} | dict(
        status='local_timing_qualification_passed', external_effects=3 * count, successful_jobs=3 * count,
        resource_headroom_qualified=False, performance_comparison_complete=False, postgres_qualified=False,
        latency_scope='Scheduled arrival through original process/variable/committed-receipt observation; local H2 qualification only.')
