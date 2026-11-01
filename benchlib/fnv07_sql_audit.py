"""Independent pinned-schema reconciliation; does not import the SQL collector."""
from datetime import datetime, timezone


def instant(value):
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, 'sql_timestamp_without_timezone'
    return parsed.astimezone(timezone.utc)


def check_database(snapshot, rest, *, run_id, database, user):
    assert snapshot['study_id'] == 'FNV-07-FUNCTIONAL-1' and snapshot['run_id'] == run_id, 'sql_run_binding'
    assert instant(snapshot['started_at']) <= instant(snapshot['finished_at']), 'sql_snapshot_clock'
    assert snapshot['connection'] == dict(database=database, schema='fluxnova', user=user, read_only='on',
        isolation='repeatable read', timezone='UTC', tls=True), 'sql_connection_binding'
    tables = snapshot['tables']
    assert set(tables) == {'act_hi_procinst', 'act_hi_actinst', 'act_hi_varinst', 'act_ru_execution',
        'act_ru_event_subscr', 'act_ru_job', 'act_ru_incident', 'act_hi_job_log'}, 'sql_table_population'
    for name, table in tables.items():
        assert table['query'] == 'SELECT * FROM fluxnova.' + name + ' ORDER BY id_', 'sql_query_changed'
        assert table['count_query'] == 'SELECT count(*) FROM fluxnova.' + name, 'sql_count_query_changed'
        assert len(table['rows']) == len({r['id_'] for r in table['rows']}) == table['count'], 'sql_inventory_incomplete'
    mappings = {
        'history': ('act_hi_procinst', {'id': 'id_', 'processDefinitionId': 'proc_def_id_', 'businessKey': 'business_key_',
            'state': 'state_', 'deleteReason': 'delete_reason_', 'startTime': 'start_time_', 'endTime': 'end_time_'}),
        'activities': ('act_hi_actinst', {'id': 'id_', 'processInstanceId': 'proc_inst_id_', 'activityId': 'act_id_',
            'activityType': 'act_type_', 'startTime': 'start_time_', 'endTime': 'end_time_'}),
        'subscriptions': ('act_ru_event_subscr', {'id': 'id_', 'processInstanceId': 'proc_inst_id_', 'executionId': 'execution_id_',
            'eventType': 'event_type_', 'eventName': 'event_name_', 'activityId': 'activity_id_'}),
        'jobs': ('act_ru_job', {'id': 'id_', 'processInstanceId': 'process_instance_id_', 'executionId': 'execution_id_',
            'jobDefinitionId': 'job_def_id_', 'retries': 'retries_'}),
        'incidents': ('act_ru_incident', {'id': 'id_', 'processInstanceId': 'proc_inst_id_', 'activityId': 'activity_id_',
            'configuration': 'configuration_', 'incidentType': 'incident_type_'}),
        'job_log': ('act_hi_job_log', {'id': 'id_', 'processInstanceId': 'process_instance_id_', 'activityId': 'act_id_',
            'jobId': 'job_id_', 'jobRetries': 'job_retries_', 'timestamp': 'timestamp_'})}
    for kind, (table, fields) in mappings.items():
        rows = {r['id_']: r for r in tables[table]['rows']}
        assert set(rows) == {r['id'] for r in rest[kind]['rows']}, 'sql_rest_population:' + kind
        for api in rest[kind]['rows']:
            stored = rows[api['id']]
            for field, column in fields.items():
                left, right = api[field], stored[column]
                if field in ('startTime', 'endTime', 'timestamp') and left is not None and right is not None:
                    left, right = instant(left), instant(right)
                assert left == right, 'sql_rest_field:' + kind + '.' + field
            if kind == 'job_log':
                flags = [api[k] for k in ('creationLog', 'failureLog', 'successLog', 'deletionLog')]
                assert flags.count(True) == 1 and stored['job_state_'] == flags.index(True), 'sql_job_outcome_mismatch'
    variables = {r['id_']: r for r in tables['act_hi_varinst']['rows']}
    assert set(variables) == {r['id'] for r in rest['variables']['rows']}, 'sql_variable_population'
    for api in rest['variables']['rows']:
        row = variables[api['id']]
        assert row['proc_inst_id_'] == api['processInstanceId'] and row['name_'] == api['name'], 'sql_variable_identity'
        assert api['type'] in ('String', 'Integer') and row['var_type_'] == api['type'].lower(), 'sql_variable_type'
        column = 'text_' if api['type'] == 'String' else 'long_'
        assert row[column] == api['value'], 'sql_variable_value'
        used = {column}
        if api['type'] == 'Integer':
            # Pinned IntegerValueSerializer.writeValue writes both LONG_ and
            # TEXT_. Require them to agree; the textual copy is not extra data.
            assert row['text_'] == (str(api['value']) if api['value'] is not None else None), 'sql_integer_text_mismatch'
            used.add('text_')
        assert all(row[k] is None for k in ('text_', 'long_', 'text2_', 'double_', 'bytearray_id_') if k not in used), 'sql_extra_variable_value'
    executions = tables['act_ru_execution']['rows']
    roots = {r['id_']: r for r in executions if r['parent_id_'] is None}
    assert set(roots) == {r['id'] for r in rest['instances']['rows']}, 'sql_active_population'
    for api in rest['instances']['rows']:
        row = roots[api['id']]
        assert row['proc_inst_id_'] == api['id'] and row['business_key_'] == api['businessKey'], 'sql_active_binding'
        assert row['proc_def_id_'] == api['definitionId'] and row['suspension_state_'] == (2 if api['suspended'] else 1), 'sql_active_definition'
    assert all(r['proc_inst_id_'] in roots for r in executions), 'sql_orphan_execution'
    assert all(r['execution_id_'] in {e['id_'] for e in executions} for r in tables['act_ru_event_subscr']['rows']), 'sql_subscription_execution'
    return {'tables_reconciled': len(tables), 'read_only_transaction': True,
            'process_histories': len(rest['history']['rows']), 'job_log_records': len(rest['job_log']['rows']),
            'active_processes': len(roots), 'scope': 'Final durable PostgreSQL state after engine shutdown, before container retirement.'}
