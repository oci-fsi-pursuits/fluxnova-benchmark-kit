"""Collect FNV-07 job/receipt evidence without deciding whether it passes."""
import json
import time
import urllib.request

from benchlib.fnv06_functional import deploy, save
from services.workflow_checks import fingerprint

INVENTORIES = {
    'instances': '/process-instance', 'history': '/history/process-instance',
    'activities': '/history/activity-instance', 'variables': '/history/variable-instance',
    'subscriptions': '/event-subscription', 'jobs': '/job', 'job_definitions': '/job-definition',
    'job_log': '/history/job-log', 'incidents': '/incident',
}


class ProbeClient:
    """Administrative client for an operator-owned, private receipt fixture."""
    def __init__(self, base_url, token):
        self.base_url, self.token = base_url.rstrip('/'), token

    def call(self, route, body):
        request = urllib.request.Request(self.base_url + route, data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json', 'X-Fnv07-Token': self.token}, method='POST')
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != 200:
                raise RuntimeError('Probe administration failed')
            return json.loads(response.read())

    def register(self, **body):
        return self.call('/register', body)

    def snapshot(self):
        return self.call('/snapshot', {})


def snapshot(api):
    result = {}
    for name, path in INVENTORIES.items():
        rows, count = api.get(path + '?maxResults=1000'), api.get(path + '/count')
        if len(rows['response']) != count['response']['count']:
            raise RuntimeError('Incomplete or changing inventory: ' + name)
        result[name] = {'rows': rows['response'], 'count': count['response']['count'],
                        'list_request': rows['index'], 'count_request': count['index']}
    return result


def collect(api, store, corpus, model, output, run_id, summary, *, timeout_s=120):
    if corpus['study_id'] != 'FNV-07-FUNCTIONAL-1' or len(corpus['cases']) != 6:
        raise ValueError('Expected the declared six-case qualification corpus')
    deploy(api, model, None)
    for case in corpus['cases']:
        key = run_id + '-' + case['id']
        body = {'businessKey': key, 'variables': {
            'completedStages': {'value': 0, 'type': 'Integer'},
            'fnv07RunId': {'value': run_id, 'type': 'String'},
            'fnv07CaseKey': {'value': key, 'type': 'String'},
            'fnv07InputSha256': {'value': fingerprint(case), 'type': 'String'},
        }}
        started = api.call('POST', '/process-definition/key/bench_fnv07_jobs/start', body)
        if started['status'] != 200:
            raise RuntimeError('Start request did not succeed')
        process_id = started['response']['id']
        original = {'case_key': key, 'process_id': process_id, 'business_key': key, 'case': case}
        registered = store.register(**original)
        summary['instances'].append({'case_id': case['id'], 'case_key': key, 'process_id': process_id,
                                     'start_request': started['index'], 'registration': original,
                                     'registration_response': registered})
        save(output / 'collection-progress.json', summary)
    save(output / 'initial-snapshot.json', snapshot(api))
    save(output / 'initial-probe.json', store.snapshot())
    for case, instance in zip(corpus['cases'], summary['instances'], strict=True):
        if case['terminal'] == 'waiting':
            continue
        released = api.call('POST', '/message', {'messageName': 'fnv07-release', 'businessKey': instance['case_key']})
        instance['release_request'] = released['index']
        if released['status'] != 204:
            raise RuntimeError('Release request did not succeed')
    deadline = time.monotonic() + timeout_s
    stable = 0
    while True:
        history = api.get('/history/process-instance?maxResults=1000')['response']
        incidents = api.get('/incident/count')['response']['count']
        jobs = api.get('/job?maxResults=1000')['response']
        terminal = sum(r['state'] == 'COMPLETED' for r in history) == 3 and incidents == 2 and len(jobs) == 2 and all(j['retries'] == 0 for j in jobs)
        stable = stable + 1 if terminal else 0
        if stable >= 3:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError('Declared functional states were not reached before drain')
        time.sleep(.25)
    save(output / 'final-snapshot.json', snapshot(api))
    save(output / 'final-probe.json', store.snapshot())
    summary['status'] = 'collected_pending_independent_audit'
