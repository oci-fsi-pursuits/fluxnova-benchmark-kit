"""Independent FNV-06 cloud identity, shutdown and SQL/REST evidence checks.

This module imports neither the SQL reader nor the workflow collector. Its
column mappings are written against the pinned engine's PostgreSQL schema.
"""
from datetime import datetime, timezone
import re


def instant(value):
    result = datetime.fromisoformat(value)
    assert result.tzinfo is not None, 'Timestamp lacks a declared timezone'
    return result.astimezone(timezone.utc)


def check_runtime(manifest, summary, identity, runtime, isolation, closeout, source):
    run_id = manifest['run_id']
    assert re.fullmatch('[0-9a-f]{32}', run_id)
    assert identity['study_id'] == 'FNV-06' and identity['run_id'] == closeout['run_id'] == run_id
    assert identity['stage'] == '/opt/fluxnova-bench/workspace/fnv06-' + run_id
    assert identity['target_id'] and identity['source_id'] and identity['target_id'] != identity['source_id']
    host = identity['connection']['primary_db_endpoint']['fqdn']
    assert host != identity['source_db_host'] and identity['connection']['ca_certificate']
    assert re.fullmatch(r'[a-z0-9.-]+\.postgresql\.us-phoenix-1\.oci\.oraclecloud\.com', host)
    assert identity['budget_limit_usd'] == 400
    start, stop = instant(identity['prepared_at']), instant(identity['stop_before_utc'])
    assert 0 < (stop - start).total_seconds() <= 21600
    assert identity['source_commit'] == manifest['source_commit'] == source['commit']
    assert re.fullmatch('[0-9a-f]{40}', source['commit'])
    assert source['files'] == manifest['source_sha256']
    assert source['runtime_sha256'] == source['files']['scripts/run_fnv06_cloud.py']
    assert manifest['runtime'] == runtime and manifest['database'] == 'OCI managed PostgreSQL'
    assert runtime['engine'] == {'version': '3.0.1'}
    assert runtime['jdbc_tls_verify_full'] is True and runtime['internal_network'] is True
    assert runtime['jvm_timezone'] == 'UTC' and runtime['database_role'] == 'fnv06_' + run_id[:12]
    assert 'PostgreSQL' in runtime['database_version']
    assert len(runtime['database_tls']) > 1 and all(r[0] is True and r[1] in ('TLSv1.2', 'TLSv1.3') for r in runtime['database_tls'])
    assert set(identity['images']) == {'engine', 'services'}
    for role in ('engine', 'services'):
        assert runtime[role + '_image'] == identity['images'][role]
        assert re.fullmatch(r'[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}', runtime[role + '_image'])
    blocked = isolation['forbidden_endpoints_blocked']
    assert len(blocked) == len(isolation['resolved_targets']) == len(identity['forbidden_endpoints']) == 5
    for result, resolved, declared in zip(blocked, isolation['resolved_targets'], identity['forbidden_endpoints'], strict=True):
        assert result == {'port': declared[1], 'blocked': True} and resolved[0] and resolved[1] == declared[1]
    assert not closeout['cleanup_errors'] and closeout['owned_engine_stopped'] is True
    assert set(closeout['containers']) == {'engine', 'job'}
    for role, container in closeout['containers'].items():
        assert container['id'] and container['label'] == run_id and container['stopped'] is True and not container['oom_killed']
        if role == 'job': assert container['exit_code'] == 0
    assert manifest['cloud_resources_accessed'] is summary['cloud_resources_accessed'] is True
    return start, stop


def check_database(snapshot, rest, *, run_id, target_id, database_user):
    assert snapshot['study_id'] == 'FNV-06' and snapshot['run_id'] == run_id and snapshot['target_id'] == target_id
    assert instant(snapshot['started_at']) <= instant(snapshot['finished_at'])
    assert snapshot['connection'] == {'database': 'postgres', 'schema': 'fluxnova', 'user': database_user,
                                      'read_only': 'on', 'isolation': 'repeatable read', 'timezone': 'UTC', 'tls': True}
    tables = snapshot['tables']
    expected_tables = {'act_hi_procinst', 'act_hi_actinst', 'act_hi_varinst', 'act_ru_execution',
                       'act_ru_event_subscr', 'act_hi_batch', 'act_ru_batch', 'act_ru_job', 'act_ru_incident'}
    assert set(tables) == expected_tables
    for name, table in tables.items():
        assert table['query'] == 'SELECT * FROM fluxnova.' + name + ' ORDER BY id_'
        assert table['count_query'] == 'SELECT count(*) FROM fluxnova.' + name
        assert len(table['rows']) == len({r['id_'] for r in table['rows']}) == table['count']
    mappings = {
        'history': ('act_hi_procinst', {'id': 'id_', 'processDefinitionId': 'proc_def_id_', 'businessKey': 'business_key_',
                   'tenantId': 'tenant_id_', 'state': 'state_', 'deleteReason': 'delete_reason_', 'startTime': 'start_time_', 'endTime': 'end_time_'}),
        'activities': ('act_hi_actinst', {'id': 'id_', 'processInstanceId': 'proc_inst_id_', 'processDefinitionId': 'proc_def_id_',
                      'activityId': 'act_id_', 'activityType': 'act_type_', 'tenantId': 'tenant_id_', 'startTime': 'start_time_', 'endTime': 'end_time_'}),
        'variables': ('act_hi_varinst', {'id': 'id_', 'processInstanceId': 'proc_inst_id_', 'name': 'name_', 'value': 'text_', 'tenantId': 'tenant_id_'}),
        'subscriptions': ('act_ru_event_subscr', {'id': 'id_', 'processInstanceId': 'proc_inst_id_', 'executionId': 'execution_id_',
                          'eventType': 'event_type_', 'eventName': 'event_name_', 'activityId': 'activity_id_', 'tenantId': 'tenant_id_'}),
        'historic_batches': ('act_hi_batch', {'id': 'id_', 'type': 'type_', 'totalJobs': 'total_jobs_',
                            'invocationsPerBatchJob': 'invocations_per_job_', 'batchJobDefinitionId': 'batch_job_def_id_',
                            'seedJobDefinitionId': 'seed_job_def_id_', 'monitorJobDefinitionId': 'monitor_job_def_id_',
                            'tenantId': 'tenant_id_', 'startTime': 'start_time_', 'endTime': 'end_time_'})}
    for kind, (table, fields) in mappings.items():
        sql_rows = {r['id_']: r for r in tables[table]['rows']}
        assert set(sql_rows) == {r['id'] for r in rest[kind]}, 'SQL/REST population differs: ' + kind
        for api in rest[kind]:
            stored = sql_rows[api['id']]
            for key, column in fields.items():
                left, right = api[key], stored[column]
                if key in ('startTime', 'endTime') and left is not None and right is not None:
                    left, right = instant(left), instant(right)
                assert left == right, 'SQL/REST field differs: ' + kind + '.' + key
            if kind == 'variables':
                assert api['type'] == 'String' and stored['var_type_'] == 'string'
                assert all(stored[c] is None for c in ('text2_', 'long_', 'double_', 'bytearray_id_'))
    assert all(r['id_'] == r['proc_inst_id_'] for r in tables['act_hi_procinst']['rows'])
    executions = tables['act_ru_execution']['rows']
    roots = {r['id_']: r for r in executions if r['parent_id_'] is None}
    assert set(roots) == {r['id'] for r in rest['instances']}
    # BpmnParse marks a standalone intermediate catch as a scope. The pinned
    # PvmAtomicOperationCreateScope creates its child and deactivates the parent.
    # Require that exact tree, rather than treating every execution as a process.
    children = {r['id_']: r for r in executions if r['parent_id_'] is not None}
    subscriptions = tables['act_ru_event_subscr']['rows']
    assert len(executions) == 2 * len(roots) and len(children) == len(roots)
    assert {r['parent_id_'] for r in children.values()} == set(roots)
    assert {r['execution_id_'] for r in subscriptions} == set(children)
    for child in children.values():
        root = roots[child['parent_id_']]
        assert child['proc_inst_id_'] == root['id_'] and child['business_key_'] is None
        assert all(child[k] == root[k] for k in ('proc_def_id_', 'tenant_id_', 'suspension_state_'))
        assert child['act_id_'] == 'waiting' and child['is_active_'] is True
        assert root['act_id_'] is None and root['is_active_'] is False
        for execution in (root, child):
            assert execution['is_scope_'] is True and execution['is_concurrent_'] is False and execution['is_event_scope_'] is False
        subscription = next(r for r in subscriptions if r['execution_id_'] == child['id_'])
        assert subscription['proc_inst_id_'] == root['id_'] and subscription['activity_id_'] == child['act_id_']
    for api in rest['instances']:
        stored = roots[api['id']]
        assert stored['proc_inst_id_'] == api['id'] and stored['proc_def_id_'] == api['definitionId']
        assert stored['business_key_'] == api['businessKey'] and stored['tenant_id_'] == api['tenantId']
        assert stored['suspension_state_'] == (2 if api['suspended'] else 1)
    assert all(r['execution_id_'] in {e['id_'] for e in executions} for r in tables['act_ru_event_subscr']['rows'])
    for kind, table in (('jobs', 'act_ru_job'), ('batches', 'act_ru_batch'), ('incidents', 'act_ru_incident')):
        assert {r['id_'] for r in tables[table]['rows']} == {r['id'] for r in rest[kind]}
    return {'tables_reconciled': len(tables), 'process_histories': len(rest['history']),
            'active_processes': len(roots), 'catch_scope_executions': len(children), 'read_only_transaction': True,
            'execution_scope_source_revision': 'c465509aa71f76508274d9f464a2a90f83667525',
            'scope': 'Final durable state before cleanup; intermediate suspension states remain REST-observed.'}
