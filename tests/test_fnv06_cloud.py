import copy
from datetime import datetime, timedelta, timezone
import unittest

from benchlib.fnv06_cloud import database_spec, require_owned_database, require_stage
from scripts.audit_fnv06_cloud import check_database, check_runtime
from tests.test_fnv06 import fixture


RUN = 'a' * 32
NOW = datetime(2026, 10, 31, 18, tzinfo=timezone.utc)


def target():
    return {'id': 'target', 'display_name': 'fluxnova-fnv06-' + RUN[:12], 'instance_count': 1,
            'source': {'source_type': 'NONE'}, 'freeform_tags': {'managed_by': 'fluxnova-benchmarks',
            'fnv06_run_id': RUN, 'comparison_lane': 'fnv06', 'temporary_comparison': 'true'}}


def stage():
    return {'study_id': 'FNV-06', 'run_id': RUN, 'stage': '/opt/fluxnova-bench/workspace/fnv06-' + RUN,
            'source_id': 'source', 'target_id': 'target', 'source_db_host': 'original.example',
            'connection': {'primary_db_endpoint': {'fqdn': 'target.postgresql.us-phoenix-1.oci.oraclecloud.com'},
                           'ca_certificate': 'test-fixture-certificate'}, 'budget_limit_usd': 400,
            'images': {'engine': 'registry.example/engine@sha256:' + '1' * 64,
                       'services': 'registry.example/services@sha256:' + '2' * 64},
            'prepared_at': NOW.isoformat(), 'stop_before_utc': (NOW + timedelta(hours=2)).isoformat()}


class OwnedCloudQualificationTests(unittest.TestCase):
    def test_expiry_allows_owned_cleanup_but_never_another_study(self):
        good = stage()
        self.assertEqual(require_stage(good, good['stage'], now=NOW + timedelta(days=1), require_time=False), RUN)
        with self.assertRaises(ValueError):
            require_stage(good, good['stage'], now=NOW + timedelta(days=1))
        wrong = copy.deepcopy(good); wrong['study_id'] = 'PAY-05'
        with self.assertRaises(ValueError):
            require_stage(wrong, wrong['stage'], require_time=False)

    def test_never_accept_original_or_another_trials_database(self):
        require_owned_database(target(), source_id='source', target_id='target', run_id=RUN)
        for change in ({'id': 'source'}, {'display_name': 'other'}, {'instance_count': 2},
                       {'source': {'source_type': 'BACKUP'}}, {'freeform_tags': {}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                require_owned_database({**target(), **change}, source_id='source', target_id='target', run_id=RUN)
        with self.assertRaises(ValueError):
            require_owned_database(target(), source_id='target', target_id='target', run_id=RUN)

    def test_every_ownership_tag_is_required(self):
        for key in target()['freeform_tags']:
            changed = target(); changed['freeform_tags'][key] = 'different'
            with self.subTest(key=key), self.assertRaises(ValueError):
                require_owned_database(changed, source_id='source', target_id='target', run_id=RUN)

    def test_stage_rejects_reference_endpoint_and_unpinned_images(self):
        good = stage(); self.assertEqual(require_stage(good, good['stage'], now=NOW), RUN)
        mutations = [('target_id', 'source'), ('source_db_host', good['connection']['primary_db_endpoint']['fqdn']),
                     ('study_id', 'w02'), ('budget_limit_usd', 401), ('stage', '/tmp/fnv06-' + RUN)]
        for key, value in mutations:
            changed = copy.deepcopy(good); changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): require_stage(changed, good['stage'], now=NOW)
        changed = copy.deepcopy(good); changed['images']['engine'] = 'registry.example/engine:latest'
        with self.assertRaises(ValueError): require_stage(changed, changed['stage'], now=NOW)
        changed = copy.deepcopy(good); changed['connection']['primary_db_endpoint']['fqdn'] = 'attacker.example'
        with self.assertRaises(ValueError): require_stage(changed, changed['stage'], now=NOW)

    def test_window_is_explicit_bounded_and_expiry_is_inclusive(self):
        good = stage()
        for instant in (NOW - timedelta(seconds=1), NOW + timedelta(hours=2)):
            with self.assertRaises(ValueError): require_stage(good, good['stage'], now=instant)
        for stop in ((NOW + timedelta(hours=7)).isoformat(), NOW.replace(tzinfo=None).isoformat()):
            changed = {**good, 'stop_before_utc': stop}
            with self.assertRaises(ValueError): require_stage(changed, changed['stage'], now=NOW)

    def test_fresh_database_uses_same_size_without_cloning_workflow_data(self):
        source = {'instance_count': 1, 'lifecycle_state': 'ACTIVE', 'system_type': 'OCI_OPTIMIZED_STORAGE',
                  'compartment_id': 'compartment', 'db_version': '16.3', 'config_id': 'config',
                  'shape': 'VM.Standard.E5.Flex', 'instance_ocpu_count': 2, 'instance_memory_size_in_gbs': 32,
                  'storage_details': {'is_regionally_durable': True}, 'network_details': {'subnet_id': 'subnet', 'nsg_ids': ['nsg']}}
        before = copy.deepcopy(source); spec = database_spec(source, RUN)
        self.assertEqual(source, before)
        self.assertEqual((spec['instance_count'], spec['instance_ocpu_count'], spec['instance_memory_size_in_gbs']), (1, 2, 32))
        self.assertEqual(spec['source'], {'source_type': 'NONE'})
        self.assertEqual(spec['freeform_tags']['campaign_budget_usd'], '400')
        require_owned_database({**spec, 'id': 'target'}, source_id='source', target_id='target', run_id=RUN)


def sql_fixture():
    _, rest = fixture()
    tables = {name: [] for name in ('act_hi_procinst', 'act_hi_actinst', 'act_hi_varinst', 'act_ru_execution',
              'act_ru_event_subscr', 'act_hi_batch', 'act_ru_batch', 'act_ru_job', 'act_ru_incident')}
    for r in rest['history']:
        tables['act_hi_procinst'].append({'id_': r['id'], 'proc_inst_id_': r['id'], 'proc_def_id_': r['processDefinitionId'],
            'business_key_': r['businessKey'], 'tenant_id_': r['tenantId'], 'state_': r['state'],
            'delete_reason_': r['deleteReason'], 'start_time_': r['startTime'], 'end_time_': r['endTime']})
    for r in rest['activities']:
        r.update(activityType='fixtureActivity', tenantId=None, startTime=NOW.isoformat())
        tables['act_hi_actinst'].append({'id_': r['id'], 'proc_inst_id_': r['processInstanceId'],
            'proc_def_id_': r['processDefinitionId'], 'act_id_': r['activityId'], 'act_type_': r['activityType'],
            'tenant_id_': None, 'start_time_': r['startTime'], 'end_time_': r['endTime']})
    for r in rest['variables']:
        tables['act_hi_varinst'].append({'id_': r['id'], 'proc_inst_id_': r['processInstanceId'], 'name_': r['name'],
            'text_': r['value'], 'tenant_id_': None, 'var_type_': 'string', 'text2_': None, 'long_': None,
            'double_': None, 'bytearray_id_': None})
    # A catch event's subscription belongs to an execution within its process.
    rest['subscriptions'][0]['executionId'] = 'catch-b'
    for r in rest['subscriptions']:
        tables['act_ru_event_subscr'].append({'id_': r['id'], 'proc_inst_id_': r['processInstanceId'],
            'execution_id_': r['executionId'], 'event_type_': r['eventType'], 'event_name_': r['eventName'],
            'activity_id_': r['activityId'], 'tenant_id_': r['tenantId']})
    common = {'proc_inst_id_': 'pid-b', 'proc_def_id_': 'definition', 'tenant_id_': None, 'suspension_state_': 1,
              'is_scope_': True, 'is_concurrent_': False, 'is_event_scope_': False}
    tables['act_ru_execution'].extend([
        {**common, 'id_': 'pid-b', 'parent_id_': None, 'business_key_': 'key-b', 'act_id_': None, 'is_active_': False},
        {**common, 'id_': 'catch-b', 'parent_id_': 'pid-b', 'business_key_': None, 'act_id_': 'waiting', 'is_active_': True}])
    result = {'study_id': 'FNV-06', 'run_id': RUN, 'target_id': 'target', 'started_at': NOW.isoformat(),
        'finished_at': (NOW + timedelta(seconds=1)).isoformat(),
        'connection': {'database': 'postgres', 'schema': 'fluxnova', 'user': 'owned', 'read_only': 'on',
                       'isolation': 'repeatable read', 'timezone': 'UTC', 'tls': True},
        'tables': {name: {'query': 'SELECT * FROM fluxnova.' + name + ' ORDER BY id_',
                         'count_query': 'SELECT count(*) FROM fluxnova.' + name, 'count': len(rows), 'rows': rows}
                   for name, rows in tables.items()}}
    return result, rest


class DurableEvidenceTests(unittest.TestCase):
    def check(self, sql, rest):
        return check_database(sql, rest, run_id=RUN, target_id='target', database_user='owned')

    def test_expected_sql_and_rest_state_reconcile(self):
        self.assertEqual(self.check(*sql_fixture())['active_processes'], 1)

    def test_wrong_recipient_or_variable_cannot_hide_behind_successful_rest(self):
        for kind, column, value in [('act_hi_varinst', 'text_', 'changed'),
                                    ('act_hi_procinst', 'state_', 'COMPLETED'),
                                    ('act_ru_event_subscr', 'proc_inst_id_', 'pid-a'),
                                    ('act_ru_execution', 'suspension_state_', 2)]:
            sql, rest = sql_fixture()
            sql['tables'][kind]['rows'][-1][column] = value
            with self.subTest(kind=kind), self.assertRaises(AssertionError): self.check(sql, rest)

    def test_missing_extra_duplicate_or_miscounted_row_rejected(self):
        for change in ('missing', 'extra', 'duplicate', 'count'):
            sql, rest = sql_fixture(); table = sql['tables']['act_hi_varinst']
            if change == 'missing': table['rows'].pop()
            if change in ('extra', 'duplicate'):
                row = copy.deepcopy(table['rows'][0])
                if change == 'extra': row['id_'] = 'unknown'
                table['rows'].append(row)
            if change != 'count': table['count'] = len(table['rows'])
            else: table['count'] += 1
            with self.subTest(change=change), self.assertRaises(AssertionError): self.check(sql, rest)

    def test_filtered_query_or_wrong_store_cannot_qualify(self):
        for change in ('query', 'database', 'read_only', 'tls', 'target', 'timezone', 'user', 'isolation'):
            sql, rest = sql_fixture()
            if change == 'query': sql['tables']['act_hi_procinst']['query'] += ' LIMIT 2'
            elif change == 'target': sql['target_id'] = 'source'
            else: sql['connection'][change] = 'wrong'
            with self.subTest(change=change), self.assertRaises(AssertionError): self.check(sql, rest)

    def test_naive_timestamp_and_unfinished_batch_rejected(self):
        sql, rest = sql_fixture(); sql['tables']['act_hi_procinst']['rows'][0]['start_time_'] = NOW.replace(tzinfo=None).isoformat()
        with self.assertRaises(AssertionError): self.check(sql, rest)
        sql, rest = sql_fixture(); sql['tables']['act_ru_batch'].update(count=1, rows=[{'id_': 'unclosed'}])
        with self.assertRaises(AssertionError): self.check(sql, rest)

    def test_unexpected_execution_and_hidden_variable_storage_rejected(self):
        sql, rest = sql_fixture(); table = sql['tables']['act_ru_execution']
        table['rows'].append({**table['rows'][0], 'id_': 'hidden', 'parent_id_': 'pid-b'}); table['count'] += 1
        with self.assertRaises(AssertionError): self.check(sql, rest)
        sql, rest = sql_fixture(); sql['tables']['act_hi_varinst']['rows'][0]['bytearray_id_'] = 'hidden-payload'
        with self.assertRaises(AssertionError): self.check(sql, rest)

    def test_missing_misparented_or_changed_catch_scope_is_rejected(self):
        for change in ('missing', 'parent', 'tenant', 'definition', 'activity', 'active', 'subscription'):
            sql, rest = sql_fixture(); table = sql['tables']['act_ru_execution']; child = table['rows'][1]
            if change == 'missing': table['rows'].pop(); table['count'] -= 1
            if change == 'parent': child['parent_id_'] = 'unknown'
            if change == 'tenant': child['tenant_id_'] = 'another-tenant'
            if change == 'definition': child['proc_def_id_'] = 'another-definition'
            if change == 'activity': child['act_id_'] = 'other-wait'
            if change == 'active': child['is_active_'] = False
            if change == 'subscription':
                # Even mutually consistent REST/SQL subscription IDs cannot point at the process root.
                sql['tables']['act_ru_event_subscr']['rows'][0]['execution_id_'] = 'pid-b'
                rest['subscriptions'][0]['executionId'] = 'pid-b'
            with self.subTest(change=change), self.assertRaises(AssertionError): self.check(sql, rest)

    def test_cloud_runtime_requires_exact_owned_stopped_cell(self):
        identity = stage(); identity['source_commit'] = 'b' * 40
        identity['forbidden_endpoints'] = [['source', p] for p in (8080, 8090, 5432, 80, 443)]
        runtime = {'engine': {'version': '3.0.1'}, 'database_version': 'PostgreSQL fixture',
            'jdbc_tls_verify_full': True, 'internal_network': True, 'jvm_timezone': 'UTC',
            'database_role': 'fnv06_' + RUN[:12], 'database_tls': [[True, 'TLSv1.3']] * 2,
            **{r + '_image': v for r, v in identity['images'].items()}}
        source = {'commit': identity['source_commit'], 'runtime_sha256': 'c' * 64,
                  'files': {'scripts/run_fnv06_cloud.py': 'c' * 64}}
        manifest = {'run_id': RUN, 'source_commit': source['commit'], 'source_sha256': source['files'],
                    'runtime': runtime, 'database': 'OCI managed PostgreSQL', 'cloud_resources_accessed': True}
        summary = {'cloud_resources_accessed': True}
        isolation = {'forbidden_endpoints_blocked': [{'port': p, 'blocked': True} for _, p in identity['forbidden_endpoints']],
                     'resolved_targets': identity['forbidden_endpoints']}
        closeout = {'run_id': RUN, 'cleanup_errors': [], 'owned_engine_stopped': True,
                    'containers': {role: {'id': role, 'label': RUN, 'stopped': True, 'oom_killed': False, 'exit_code': 0}
                                   for role in ('engine', 'job')}}
        args = [manifest, summary, identity, runtime, isolation, closeout, source]
        self.assertEqual(check_runtime(*args)[0], NOW)
        for change in ('source', 'image', 'shutdown', 'label', 'tls', 'network', 'commit', 'failed_job'):
            m, s, i, r, n, c, f = copy.deepcopy(args)
            if change == 'source': i['target_id'] = i['source_id']
            if change == 'image': r['engine_image'] = 'engine:latest'
            if change == 'shutdown': c['containers']['engine']['stopped'] = False
            if change == 'label': c['containers']['job']['label'] = 'another-run'
            if change == 'tls': r['database_tls'][0][0] = False
            if change == 'network': n['forbidden_endpoints_blocked'][0]['blocked'] = False
            if change == 'commit': f['commit'] = 'd' * 40
            if change == 'failed_job': c['containers']['job']['exit_code'] = 1
            with self.subTest(change=change), self.assertRaises(AssertionError): check_runtime(m, s, i, r, n, c, f)


if __name__ == '__main__': unittest.main()
