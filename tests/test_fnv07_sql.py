"""Small SQL/REST mapping fixtures; native functional acceptance is separate."""
from copy import deepcopy
import unittest

from benchlib.fnv07_sql_audit import check_database


def fixture():
    tables = ('act_hi_procinst', 'act_hi_actinst', 'act_hi_varinst', 'act_ru_execution',
              'act_ru_event_subscr', 'act_ru_job', 'act_ru_incident', 'act_hi_job_log')
    snapshot = dict(study_id='FNV-07-FUNCTIONAL-1', run_id='fixture',
        started_at='2026-11-01T00:00:00Z', finished_at='2026-11-01T00:00:01Z',
        connection=dict(database='bench', schema='fluxnova', user='fixture', read_only='on',
                        isolation='repeatable read', timezone='UTC', tls=True),
        tables={name: dict(query='SELECT * FROM fluxnova.' + name + ' ORDER BY id_',
            count_query='SELECT count(*) FROM fluxnova.' + name, count=0, rows=[]) for name in tables})
    rest = {name: {'rows': []} for name in ('history', 'activities', 'subscriptions', 'jobs', 'incidents', 'job_log', 'variables', 'instances')}
    snapshot['tables']['act_hi_job_log'].update(count=1, rows=[dict(id_='log1', timestamp_='2026-11-01T00:00:00Z',
        process_instance_id_='process1', act_id_='stage2', job_id_='job1', job_retries_=2, job_state_=1)])
    rest['job_log']['rows'] = [dict(id='log1', timestamp='2026-11-01T00:00:00+00:00', processInstanceId='process1',
        activityId='stage2', jobId='job1', jobRetries=2, creationLog=False, failureLog=True, successLog=False, deletionLog=False)]
    snapshot['tables']['act_hi_varinst'].update(count=1, rows=[dict(id_='var1', proc_inst_id_='process1', name_='completedStages',
        var_type_='integer', long_=1, text_='1', text2_=None, double_=None, bytearray_id_=None)])
    rest['variables']['rows'] = [dict(id='var1', processInstanceId='process1', name='completedStages', type='Integer', value=1)]
    return snapshot, rest


class SqlEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.sql, self.rest = fixture()

    def verify(self):
        return check_database(self.sql, self.rest, run_id='fixture', database='bench', user='fixture')

    def test_pinned_historic_job_activity_column_and_integer_storage(self):
        self.assertEqual(self.verify()['job_log_records'], 1)

    def test_rejects_changed_completed_counter(self):
        self.sql['tables']['act_hi_varinst']['rows'][0]['long_'] = 3
        with self.assertRaisesRegex(AssertionError, 'sql_variable_value'):
            self.verify()

    def test_rejects_failure_claimed_as_success(self):
        self.sql['tables']['act_hi_job_log']['rows'][0]['job_state_'] = 2
        with self.assertRaisesRegex(AssertionError, 'sql_job_outcome_mismatch'):
            self.verify()

    def test_rejects_integer_text_disagreeing_with_numeric_value(self):
        self.sql['tables']['act_hi_varinst']['rows'][0]['text_'] = '3'
        with self.assertRaisesRegex(AssertionError, 'sql_integer_text_mismatch'):
            self.verify()

    def test_rejects_missing_original_job_row(self):
        self.sql['tables']['act_hi_job_log'].update(rows=[], count=0)
        with self.assertRaisesRegex(AssertionError, 'sql_rest_population:job_log'):
            self.verify()

    def test_rejects_wrong_database_or_unverified_connection(self):
        for key, value in [('database', 'other'), ('tls', False), ('read_only', 'off')]:
            with self.subTest(key=key):
                original = deepcopy(self.sql['connection'])
                self.sql['connection'][key] = value
                with self.assertRaisesRegex(AssertionError, 'sql_connection_binding'):
                    self.verify()
                self.sql['connection'] = original


if __name__ == '__main__':
    unittest.main()
