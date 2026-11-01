"""Adversarial evidence fixtures, not native-engine or performance results."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from benchlib.fnv07_audit import parse_delegate_log, verify_semantics

ROOT = Path(__file__).resolve().parents[1]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def fixture():
    """Small explicitly synthetic REST/job/receipt corpus with stable identifiers."""
    corpus = json.loads((ROOT / 'workloads/fnv07/cases.json').read_text())
    manifest = {'run_id': 'fixture', 'nodes': ['n1'], 'database': 'fresh embedded H2'}
    collection = {'instances': []}
    names = ('instances', 'history', 'subscriptions', 'jobs', 'job_log', 'incidents', 'activities', 'variables')
    initial = {name: {'rows': []} for name in names}
    final = deepcopy(initial)
    requests = []
    config = dict(zip(('timezone', 'history.level', 'job.deployment_aware', 'job.isActive',
        'job.getMaxJobsPerAcquisition', 'job.getLockTimeInMillis', 'job.getWaitTimeInMillis',
        'job.getMaxWait', 'job.getBackoffTimeInMillis', 'job.getMaxBackoff', 'pool.getCorePoolSize',
        'pool.getMaxPoolSize', 'pool.getQueueCapacity', 'database.isolation', 'database.product'),
        ('UTC', 'full', 'false', 'true', '2', '30000', '100', '500', '100', '500', '2', '2', '2', '2', 'H2')))
    probe = {'metadata': [{'key': 'run_id', 'value': 'fixture'}, {'key': 'nodes', 'value': '["n1"]'}],
             'nodes': [{'node_id': 'n1', 'body': canonical(config)}], 'cases': [], 'attempts': [], 'effects': []}
    logs = {'NODE': [{'node_id': 'n1', **config}], 'ATTEMPT': [], 'SUCCESS': [], 'FAILURE': []}

    def request(method, path, body, response, status=200):
        i = len(requests)
        requests.append(dict(index=i, method=method, path=path, body=body, response=response, status=status))
        return i

    for case in corpus['cases']:
        key, pid, sha = 'fixture-' + case['id'], 'process-' + case['id'], digest(canonical(case))
        values = dict(completedStages=0, fnv07RunId='fixture', fnv07CaseKey=key, fnv07InputSha256=sha)
        started = request('POST', '/process-definition/key/bench_fnv07_jobs/start',
                          {'businessKey': key, 'variables': {k: {'value': v, 'type': 'Integer' if isinstance(v, int) else 'String'} for k, v in values.items()}}, {'id': pid})
        identity = dict(case_id=case['id'], case_key=key, process_id=pid, start_request=started)
        collection['instances'].append(identity)
        probe['cases'].append(dict(case_key=key, process_id=pid, business_key=key, body=canonical(case), input_sha256=sha))
        initial['instances']['rows'].append({'id': pid})
        initial['history']['rows'].append({'id': pid})
        subscription = dict(processInstanceId=pid, eventType='message', eventName='fnv07-release', activityId='wait')
        initial['subscriptions']['rows'].append(subscription)
        terminal, completed = case['terminal'], case['completed_stages']
        final['history']['rows'].append(dict(id=pid, businessKey=key, deleteReason=None,
            state='COMPLETED' if terminal == 'completed' else 'ACTIVE', endTime='fixture-time' if terminal == 'completed' else None))
        if terminal != 'completed':
            final['instances']['rows'].append({'id': pid})
        else:
            final['activities']['rows'].append(dict(processInstanceId=pid, activityType='endEvent', activityId='end', endTime='fixture-time', canceled=False))
        if terminal == 'waiting':
            final['subscriptions']['rows'].append(deepcopy(subscription))
        else:
            identity['release_request'] = request('POST', '/message', {'messageName': 'fnv07-release', 'businessKey': key}, None, 204)
        values['completedStages'] = completed
        if terminal == 'incident':
            final['jobs']['rows'].append(dict(processInstanceId=pid, id=pid + '-stage2-job', retries=0, failedActivityId='stage2'))
            final['incidents']['rows'].append(dict(processInstanceId=pid, activityId='stage2', configuration=pid + '-stage2-job', incidentType='failedJob'))
        for number in range(1, 4):
            stage = 'stage' + str(number)
            executed = number <= completed or (terminal == 'incident' and number == 2)
            if not executed:
                continue
            outcomes = [(200, 'committed')]
            if number == 2 and case['mode'].startswith('fail_'):
                before = case['mode'].startswith('fail_before')
                failed = 'failed_before_effect' if before else 'committed_response_withheld'
                outcomes = [(503, failed), (200, 'committed' if before else 'cached')]
                if case['mode'].endswith('_always'):
                    outcomes = [(503, failed)] + [(503, 'failed_before_effect' if before else 'cached_response_withheld')] * 2
            job = dict(processInstanceId=pid, activityId=stage, jobId=pid + '-' + stage + '-job', deletionLog=False)
            final['job_log']['rows'].append(dict(job, creationLog=True, successLog=False, failureLog=False))
            effect_identity = 'fixture|' + key + '|' + pid + '|' + stage
            receipt = digest('receipt|' + effect_identity) if stage in case['effect_stages'] else None
            for status, disposition in outcomes:
                seq = len(probe['attempts']) + 1
                body = dict(run_id='fixture', case_key=key, process_id=pid, business_key=key,
                            input_sha256=sha, stage=stage, node_id='n1', invocation_id='invocation-' + str(seq))
                attempt_receipt = receipt if disposition != 'failed_before_effect' else None
                probe['attempts'].append(dict(seq=seq, request=canonical(body), case_key=key, stage=stage,
                    invocation_id=body['invocation_id'], started_ns=seq * 10, finished_ns=seq * 10 + 1,
                    status=status, disposition=disposition, receipt_id=attempt_receipt))
                logs['ATTEMPT'].append(body)
                logs['SUCCESS' if status == 200 else 'FAILURE'].append(dict(body, receipt_id=attempt_receipt, disposition=disposition))
                if disposition.startswith('committed'):
                    probe['effects'].append(dict(case_key=key, stage=stage, process_id=pid, input_sha256=sha,
                        effect_id=digest('effect|' + effect_identity), receipt_id=receipt, first_invocation_id=body['invocation_id']))
                final['job_log']['rows'].append(dict(job, creationLog=False, successLog=status == 200, failureLog=status == 503))
            if number <= completed:
                values[stage + 'Receipt'] = receipt
                final['activities']['rows'].append(dict(processInstanceId=pid, activityType='serviceTask', activityId=stage, endTime='fixture-time', canceled=False))
        final['variables']['rows'].extend(dict(processInstanceId=pid, name=k, value=v) for k, v in values.items())
    for inventories in (initial, final):
        for name, row in inventories.items():
            row['count'] = len(row['rows'])
            row['list_request'] = request('GET', '/' + name, None, deepcopy(row['rows']))
            row['count_request'] = request('GET', '/' + name + '/count', None, {'count': row['count']})
    initial_probe = deepcopy(probe)
    initial_probe.update(effects=[], attempts=[])
    return dict(corpus=corpus, manifest=manifest, collection=collection, initial=initial, final=final,
                initial_probe=initial_probe, probe=probe, requests=requests, logs=logs)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()

    def verify(self):
        return verify_semantics(**self.data)

    def resave_inventory(self, name):
        row = self.data['final'][name]
        row['count'] = len(row['rows'])
        self.data['requests'][row['list_request']]['response'] = deepcopy(row['rows'])
        self.data['requests'][row['count_request']]['response'] = {'count': row['count']}

    def test_distinguishes_blocked_effect_from_workflow_completion(self):
        rows = {r['case']: r for r in self.verify()}
        self.assertEqual(rows['exhaust_after']['state'], 'incident')
        self.assertEqual(rows['exhaust_after']['external_effects'], 2)
        self.assertEqual(rows['exhaust_after']['committed_workflow_stages'], 1)

    def test_rejects_duplicate_effect(self):
        self.data['probe']['effects'].append(deepcopy(self.data['probe']['effects'][0]))
        with self.assertRaisesRegex(AssertionError, 'duplicate_external_effect'):
            self.verify()

    def test_rejects_missing_failed_attempt(self):
        self.data['probe']['attempts'].pop(4)
        with self.assertRaisesRegex(AssertionError, 'attempt_sequence_incomplete'):
            self.verify()

    def test_rejects_changed_original_input(self):
        self.data['probe']['effects'][0]['input_sha256'] = 'changed'
        with self.assertRaisesRegex(AssertionError, 'effect_original_input_mismatch'):
            self.verify()

    def test_rejects_receipt_linked_to_wrong_attempt(self):
        self.data['probe']['effects'][0]['first_invocation_id'] = 'wrong-invocation'
        with self.assertRaisesRegex(AssertionError, 'effect_first_attempt_mismatch'):
            self.verify()

    def test_rejects_dropped_failure_log(self):
        self.data['logs']['FAILURE'].pop()
        with self.assertRaisesRegex(AssertionError, 'delegate_terminal_record_missing'):
            self.verify()

    def test_rejects_unobserved_second_engine(self):
        self.data['manifest']['nodes'].append('n2')
        self.data['probe']['metadata'][1]['value'] = '["n1","n2"]'
        with self.assertRaisesRegex(AssertionError, 'missing_declared_node'):
            self.verify()

    def test_rejects_inactive_executor_even_when_configuration_log_agrees(self):
        config = json.loads(self.data['probe']['nodes'][0]['body'])
        config['job.isActive'] = 'false'
        self.data['probe']['nodes'][0]['body'] = canonical(config)
        self.data['logs']['NODE'][0]['job.isActive'] = 'false'
        with self.assertRaisesRegex(AssertionError, 'effective_configuration_mismatch'):
            self.verify()

    def test_rejects_job_failure_reassigned_to_another_stage(self):
        rows = self.data['final']['job_log']['rows']
        failed = next(r for r in rows if r['failureLog'])
        failed['activityId'] = 'stage1'
        failed['jobId'] = failed['processInstanceId'] + '-stage1-job'
        self.resave_inventory('job_log')
        with self.assertRaisesRegex(AssertionError, 'stage_job_outcomes_mismatch'):
            self.verify()

    def test_rejects_incident_for_another_job(self):
        self.data['final']['incidents']['rows'][0]['configuration'] = 'wrong-job'
        self.resave_inventory('incidents')
        with self.assertRaisesRegex(AssertionError, 'incident_job_binding'):
            self.verify()

    def test_rejects_blocked_instance_claimed_completed(self):
        row = next(r for r in self.data['final']['history']['rows'] if r['id'] == 'process-exhaust_after')
        row.update(state='COMPLETED', endTime='fixture-time')
        self.resave_inventory('history')
        with self.assertRaisesRegex(AssertionError, 'unexpected_workflow_state'):
            self.verify()

    def test_rejects_wrong_waiting_subscription(self):
        self.data['final']['subscriptions']['rows'][0]['eventName'] = 'different-event'
        self.resave_inventory('subscriptions')
        with self.assertRaisesRegex(AssertionError, 'waiting_subscription_binding'):
            self.verify()

    def test_rejects_http_failure_in_original_requests(self):
        self.data['requests'][0]['status'] = 503
        with self.assertRaisesRegex(AssertionError, 'http_failure_retained'):
            self.verify()

    def test_rejects_ambiguous_delegate_field(self):
        with self.assertRaisesRegex(AssertionError, 'duplicate_delegate_field'):
            parse_delegate_log('FNV07_ATTEMPT node_id=n1&node_id=n2')


if __name__ == '__main__':
    unittest.main()
