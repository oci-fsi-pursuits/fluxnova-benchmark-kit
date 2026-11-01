"""Independent original-input, REST, delegate-log and SQLite reconciliation."""
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs

STAGES = ('stage1', 'stage2', 'stage3')
EXPECTED = {
    'normal': ('normal', 'completed', 3, 0, STAGES),
    'retry_before': ('fail_before_once', 'completed', 3, 1, STAGES),
    'retry_after': ('fail_after_once', 'completed', 3, 1, STAGES),
    'exhaust_before': ('fail_before_always', 'incident', 1, 3, ('stage1',)),
    'exhaust_after': ('fail_after_always', 'incident', 1, 3, ('stage1', 'stage2')),
    'waiting_control': ('normal', 'waiting', 0, 0, ()),
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def require(condition, reason):
    if not condition:
        raise AssertionError(reason)


def verify_semantics(corpus, manifest, collection, initial, final, initial_probe, probe, requests, logs):
    cases = corpus['cases']
    require(len(cases) == 6 and {c['id'] for c in cases} == set(EXPECTED), 'declared_six_cases_required')
    require([r['index'] for r in requests] == list(range(len(requests))), 'original_request_sequence')
    require(all(r['status'] in (200, 204) for r in requests), 'http_failure_retained')
    require(len(collection['instances']) == 6, 'missing_original_instance')
    require({r['case_id'] for r in collection['instances']} == set(EXPECTED), 'case_mapping_changed')
    require(len({r['process_id'] for r in collection['instances']}) == 6, 'duplicate_original_process')
    ids = {r['process_id'] for r in collection['instances']}
    require({r['id'] for r in initial['instances']['rows']} == ids, 'initial_instance_population')
    active_ids = {r['process_id'] for r in collection['instances'] if EXPECTED[r['case_id']][1] != 'completed'}
    require({r['id'] for r in final['instances']['rows']} == active_ids, 'final_active_population')
    for inventories in (initial, final):
        for row in inventories.values():
            require(row['rows'] == requests[row['list_request']]['response'], 'snapshot_not_original_response')
            require(len(row['rows']) == row['count'] == requests[row['count_request']]['response']['count'], 'uncounted_inventory')
    require(initial['instances']['count'] == initial['history']['count'] == initial['subscriptions']['count'] == 6, 'initial_waiting_state')
    require(initial['jobs']['count'] == initial['job_log']['count'] == initial['incidents']['count'] == 0, 'initial_work_not_empty')
    require(not initial_probe['effects'] and not initial_probe['attempts'] and len(initial_probe['cases']) == 6, 'initial_probe_not_empty')
    require({r['id'] for r in final['history']['rows']} == ids, 'final_history_population')
    require(dict((r['key'], r['value']) for r in probe['metadata']) == {
        'run_id': manifest['run_id'], 'nodes': canonical(manifest['nodes'])}, 'probe_identity_mismatch')
    require(len(probe['cases']) == 6 and probe['cases'] == initial_probe['cases'], 'registration_changed')
    require({r['node_id'] for r in probe['nodes']} == set(manifest['nodes']), 'missing_declared_node')
    require({r['node_id'] for r in logs['NODE']} == set(manifest['nodes']), 'node_not_observed_in_engine_log')
    for row in probe['nodes']:
        config = json.loads(row['body'])
        require(any(r == {'node_id': row['node_id'], **config} for r in logs['NODE']), 'node_configuration_log_mismatch')
        wanted = {'timezone': 'UTC', 'history.level': 'full', 'job.deployment_aware': 'false',
                  'job.isActive': 'true', 'job.getMaxJobsPerAcquisition': '2', 'job.getLockTimeInMillis': '30000',
                  'job.getWaitTimeInMillis': '100', 'job.getMaxWait': '500', 'job.getBackoffTimeInMillis': '100',
                  'job.getMaxBackoff': '500', 'pool.getCorePoolSize': '2', 'pool.getMaxPoolSize': '2',
                  'pool.getQueueCapacity': '2', 'database.isolation': '2'}
        require(all(config.get(k) == v for k, v in wanted.items()), 'effective_configuration_mismatch')
        require(config['database.product'] == ('H2' if manifest['database'] == 'fresh embedded H2' else 'PostgreSQL'), 'database_product_mismatch')
    attempts = probe['attempts']
    require([r['seq'] for r in attempts] == list(range(1, len(attempts) + 1)), 'attempt_sequence_incomplete')
    require(len(logs['ATTEMPT']) == len(attempts), 'delegate_attempts_not_all_observed')
    require(len({r['invocation_id'] for r in logs['ATTEMPT']}) == len(attempts), 'ambiguous_delegate_invocation')
    require(len(logs['SUCCESS']) + len(logs['FAILURE']) == len(attempts), 'delegate_terminal_record_missing')
    outcomes = []
    for case in cases:
        mode, terminal, completed, failures, stages = EXPECTED[case['id']]
        require((case['mode'], case['terminal'], case['completed_stages'], case['failed_job_records'], tuple(case['effect_stages']))
                == (mode, terminal, completed, failures, stages), 'original_case_expectation_changed')
        identity = next(r for r in collection['instances'] if r['case_id'] == case['id'])
        pid, key = identity['process_id'], manifest['run_id'] + '-' + case['id']
        input_sha = digest(canonical(case).encode())
        require(identity['case_key'] == key, 'original_case_key_mismatch')
        start = requests[identity['start_request']]
        require(start['method'] == 'POST' and start['path'] == '/process-definition/key/bench_fnv07_jobs/start', 'original_start_route')
        require(start['response']['id'] == pid and start['body'] == {'businessKey': key, 'variables': {
            'completedStages': {'value': 0, 'type': 'Integer'}, 'fnv07RunId': {'value': manifest['run_id'], 'type': 'String'},
            'fnv07CaseKey': {'value': key, 'type': 'String'}, 'fnv07InputSha256': {'value': input_sha, 'type': 'String'}}}, 'original_start_binding')
        registered = next(r for r in probe['cases'] if r['case_key'] == key)
        require(registered == {'case_key': key, 'process_id': pid, 'business_key': key, 'body': canonical(case), 'input_sha256': input_sha}, 'original_registration_binding')
        history = [r for r in final['history']['rows'] if r['id'] == pid]
        require(len(history) == 1 and history[0]['businessKey'] == key and history[0]['deleteReason'] is None, 'history_binding')
        require(history[0]['state'] == ('COMPLETED' if terminal == 'completed' else 'ACTIVE'), 'unexpected_workflow_state')
        require(bool(history[0]['endTime']) == (terminal == 'completed'), 'completion_timestamp_mismatch')
        variables = [r for r in final['variables']['rows'] if r['processInstanceId'] == pid]
        require(len({r['name'] for r in variables}) == len(variables), 'duplicate_history_variable')
        values = {r['name']: r['value'] for r in variables}
        effects = [r for r in probe['effects'] if r['case_key'] == key]
        require(len(effects) == len(stages) and {r['stage'] for r in effects} == set(stages), 'wrong_or_duplicate_external_effect')
        wanted = {'completedStages': completed, 'fnv07RunId': manifest['run_id'], 'fnv07CaseKey': key, 'fnv07InputSha256': input_sha}
        for effect in effects:
            effect_identity = manifest['run_id'] + '|' + key + '|' + pid + '|' + effect['stage']
            require(effect['process_id'] == pid and effect['input_sha256'] == input_sha, 'effect_original_input_mismatch')
            require(effect['effect_id'] == digest(('effect|' + effect_identity).encode()) and effect['receipt_id'] == digest(('receipt|' + effect_identity).encode()), 'effect_receipt_identity')
            if effect['stage'] in STAGES[:completed]:
                wanted[effect['stage'] + 'Receipt'] = effect['receipt_id']
        require(values == wanted, 'committed_engine_variables_mismatch')
        activities = [r for r in final['activities']['rows'] if r['processInstanceId'] == pid]
        ends = [r for r in activities if r['activityType'].lower().endswith('endevent')]
        require(len(ends) == (1 if terminal == 'completed' else 0), 'wrong_or_duplicate_end_event')
        require(all(r['activityId'] == 'end' and r['endTime'] and not r['canceled'] for r in ends), 'invalid_end_event')
        for stage in STAGES[:completed]:
            actual = [r for r in activities if r['activityId'] == stage and r['endTime'] and not r['canceled']]
            require(len(actual) == 1, 'committed_stage_activity_mismatch')
        jobs = [r for r in final['jobs']['rows'] if r['processInstanceId'] == pid]
        incidents = [r for r in final['incidents']['rows'] if r['processInstanceId'] == pid]
        subscriptions = [r for r in final['subscriptions']['rows'] if r['processInstanceId'] == pid]
        require(len(jobs) == len(incidents) == (1 if terminal == 'incident' else 0), 'unexpected_blocked_job_or_incident')
        require(all(r['retries'] == 0 for r in jobs), 'job_retries_not_exhausted')
        if jobs:
            require(jobs[0]['failedActivityId'] == incidents[0]['activityId'] == 'stage2'
                    and incidents[0]['configuration'] == jobs[0]['id']
                    and incidents[0]['incidentType'] == 'failedJob', 'incident_job_binding')
        require(len(subscriptions) == (1 if terminal == 'waiting' else 0), 'waiting_control_transitioned')
        require(all(r['eventType'] == 'message' and r['eventName'] == 'fnv07-release'
                    and r['activityId'] == 'wait' for r in subscriptions), 'waiting_subscription_binding')
        if terminal != 'waiting':
            release = requests[identity['release_request']]
            require(release['method'] == 'POST' and release['path'] == '/message' and release['status'] == 204
                    and release['body'] == {'messageName': 'fnv07-release', 'businessKey': key}, 'release_not_original_input')
        else:
            require('release_request' not in identity, 'waiting_control_was_released')
        job_log = [r for r in final['job_log']['rows'] if r['processInstanceId'] == pid]
        require(sum(r['successLog'] for r in job_log) == completed and sum(r['failureLog'] for r in job_log) == failures, 'job_outcome_accounting')
        created = [r for r in job_log if r['creationLog']]
        require(len(created) == len({r['jobId'] for r in created}) == completed + (1 if terminal == 'incident' else 0), 'job_creation_count')
        require(len({r['activityId'] for r in created}) == len(created), 'ambiguous_process_stage_job')
        require(not any(r['deletionLog'] for r in job_log), 'job_deleted_before_evidence')
        require(all(sum(bool(r[k]) for k in ('creationLog', 'successLog', 'failureLog', 'deletionLog')) == 1
                    for r in job_log), 'ambiguous_job_log_type')
        by_stage = {r['activityId']: r['jobId'] for r in created}
        require(all(r['jobId'] == by_stage.get(r['activityId']) for r in job_log), 'job_log_identity_mismatch')
        require(all(r['id'] == by_stage.get('stage2') for r in jobs), 'blocked_job_identity_mismatch')
        for stage in STAGES:
            selected = [r for r in attempts if r['case_key'] == key and r['stage'] == stage]
            expected = [(200, 'committed')] if stage in STAGES[:completed] else []
            if stage == 'stage2' and mode == 'fail_before_once':
                expected = [(503, 'failed_before_effect'), (200, 'committed')]
            elif stage == 'stage2' and mode == 'fail_after_once':
                expected = [(503, 'committed_response_withheld'), (200, 'cached')]
            elif stage == 'stage2' and mode == 'fail_before_always':
                expected = [(503, 'failed_before_effect')] * 3
            elif stage == 'stage2' and mode == 'fail_after_always':
                expected = [(503, 'committed_response_withheld'), (503, 'cached_response_withheld'), (503, 'cached_response_withheld')]
            require([(r['status'], r['disposition']) for r in selected] == expected, 'attempt_failure_or_replay_accounting')
            job_records = [r for r in job_log if r['activityId'] == stage]
            require(sum(r['failureLog'] for r in job_records) == sum(status == 503 for status, _ in expected)
                    and sum(r['successLog'] for r in job_records) == (stage in STAGES[:completed]), 'stage_job_outcomes_mismatch')
            effect = next((r for r in effects if r['stage'] == stage), None)
            commits = [r for r in selected if r['disposition'].startswith('committed')]
            require(len(commits) == (1 if effect else 0), 'effect_commit_attempt_count')
            if effect:
                require(effect['first_invocation_id'] == commits[0]['invocation_id'], 'effect_first_attempt_mismatch')
            for attempt in selected:
                require(attempt['receipt_id'] == (effect['receipt_id'] if effect and attempt['disposition'] != 'failed_before_effect' else None), 'attempt_receipt_binding')
                request = json.loads(attempt['request'])
                require(request['process_id'] == pid and request['case_key'] == key and request['business_key'] == key
                        and request['stage'] == stage and request['input_sha256'] == input_sha
                        and request['run_id'] == manifest['run_id'] and request['node_id'] in manifest['nodes'], 'attempt_original_input_mismatch')
                require(attempt['invocation_id'] == request['invocation_id'] and attempt['started_ns'] <= attempt['finished_ns'], 'attempt_clock_or_invocation')
                require(logs['ATTEMPT'].count(request) == 1, 'attempt_not_original_delegate_log')
                terminal_logs = logs['SUCCESS'] if attempt['status'] == 200 else logs['FAILURE']
                ending = [r for r in terminal_logs if r['invocation_id'] == request['invocation_id']]
                require(len(ending) == 1 and all(ending[0].get(k) == v for k, v in request.items()), 'delegate_response_not_reconciled')
                if attempt['status'] == 200:
                    require(ending[0]['receipt_id'] == attempt['receipt_id'] and ending[0]['disposition'] == attempt['disposition'], 'delegate_receipt_mismatch')
        outcomes.append({'case': case['id'], 'state': terminal, 'committed_workflow_stages': completed,
                         'external_effects': len(effects), 'failed_job_records': failures})
    require(len(attempts) == 19 and len(probe['effects']) == 12, 'global_probe_denominator_mismatch')
    require(final['history']['count'] == 6 and final['instances']['count'] == 3 and final['jobs']['count'] == final['incidents']['count'] == 2, 'final_denominators')
    require(final['job_log']['count'] == 32 and len(logs['SUCCESS']) == 11 and len(logs['FAILURE']) == 8, 'job_and_delegate_denominators')
    return outcomes


def parse_delegate_log(raw):
    logs = {name: [] for name in ('NODE', 'ATTEMPT', 'SUCCESS', 'FAILURE')}
    for line in raw.splitlines():
        if not line.startswith('FNV07_'):
            continue
        kind, data = line.split(' ', 1)
        require(kind[6:] in logs, 'unknown_delegate_record')
        values = parse_qs(data, strict_parsing=True, keep_blank_values=True)
        require(all(len(v) == 1 for v in values.values()), 'duplicate_delegate_field')
        logs[kind[6:]].append({k: v[0] for k, v in values.items()})
    return logs


def audit(folder):
    folder = Path(folder).resolve()
    read = lambda name: json.loads((folder / name).read_text(encoding='utf-8'))
    seal = read('checksums.json')
    required = {'manifest.json', 'delegate-build.json', 'collection.json', 'requests.ndjson', 'engine-n1.log',
                'initial-snapshot.json', 'initial-probe.json', 'final-snapshot.json', 'final-probe.json', 'probe-snapshot.sqlite'}
    require(required <= set(seal['files']), 'incomplete_evidence_seal')
    for name, expected in seal['files'].items():
        path = (folder / name).resolve()
        require(path.is_relative_to(folder) and digest(path.read_bytes()) == expected, 'evidence_seal_mismatch')
    manifest, collection = read('manifest.json'), read('collection.json')
    require(manifest['study_id'] == collection['study_id'] == 'FNV-07-FUNCTIONAL-1', 'study_binding')
    require(collection['status'] == 'collected_pending_independent_audit', 'incomplete_collection')
    require(collection['run_id'] == manifest['run_id'] and collection['owned_engine_stopped'] and collection['owned_probe_stopped'], 'owned_runtime_cleanup_unverified')
    require(manifest['nodes'] == ['n1'] and manifest['database'] == 'fresh embedded H2' and not manifest['cloud_resources_accessed'], 'local_scope_only')
    for name, expected in manifest['source_sha256'].items():
        require(digest((folder / 'inputs' / name).read_bytes()) == expected, 'original_source_mismatch')
    build = read('delegate-build.json')
    require(build['source_sha256'] == manifest['source_sha256']['services/fnv07/RecordingDelegate.java'], 'delegate_source_not_bound')
    require(build['artifact_sha256'] == manifest['delegate_sha256'] == digest((folder / 'userlib/fnv07-delegate.jar').read_bytes()), 'delegate_artifact_not_bound')
    require(build['runtime'] == manifest['runtime'] and build['target_release'] == 21, 'delegate_runtime_mismatch')
    requests = [json.loads(line) for line in (folder / 'requests.ndjson').read_text().splitlines()]
    logs = parse_delegate_log((folder / 'engine-n1.log').read_text(errors='strict'))
    with sqlite3.connect((folder / 'probe-snapshot.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        db.row_factory = sqlite3.Row
        require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'probe_database_integrity')
        probe = {table: [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY ' + order)]
                 for table, order in [('metadata', 'key'), ('nodes', 'node_id'), ('cases', 'case_key'),
                                      ('effects', 'case_key,stage'), ('attempts', 'seq')]}
    require(probe == read('final-probe.json'), 'sqlite_and_collected_probe_mismatch')
    outcomes = verify_semantics(read('inputs/workloads/fnv07/cases.json'), manifest, collection,
                                read('initial-snapshot.json'), read('final-snapshot.json'),
                                read('initial-probe.json'), probe, requests, logs)
    return {'study_id': 'FNV-07-FUNCTIONAL-1', 'status': 'single_engine_retry_effects_verified',
            'cases': outcomes, 'instances': 6, 'completed_instances': 3, 'expected_blocked_instances': 2,
            'unchanged_waiting_controls': 1, 'delegate_attempts': 19, 'delegate_successes': 11,
            'delegate_failures_retained': 8, 'unique_external_effects': 12,
            'blocked_instance_with_external_effect_retained': True, 'effective_configuration_verified': True,
            'independent_sqlite_and_rest_reconciled': True, 'engine_sql_reconciled': False,
            'owned_engine_and_probe_stopped': True, 'cloud_resources_accessed': False,
            'postgresql_cluster_qualified': False, 'performance_comparison_complete': False,
            'source_seal_sha256': digest((folder / 'checksums.json').read_bytes()),
            'auditor_sha256': digest(Path(__file__).read_bytes()),
            'boundary': 'One local H2 engine and a synthetic receipt backend. No multi-engine, PostgreSQL, timing or exactly-once network-execution claim.'}
