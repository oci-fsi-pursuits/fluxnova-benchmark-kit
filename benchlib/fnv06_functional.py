"""Collect FNV-06 routing evidence from a fresh, operator-owned engine.

No mutation is retried. HTTP responses, complete inventories and original
inputs are retained; acceptance belongs to the separate saved-evidence auditor.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

BPMN = 'http://www.omg.org/spec/BPMN/20100524/MODEL'
INVENTORIES = {
    'instances': '/process-instance', 'history': '/history/process-instance',
    'activities': '/history/activity-instance', 'variables': '/history/variable-instance',
    'subscriptions': '/event-subscription', 'incidents': '/incident',
    'jobs': '/job', 'batches': '/batch', 'historic_batches': '/history/batch',
}


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Recorder:
    def __init__(self, engine, output):
        self.engine, self.output, self.index = engine, output, 0

    def call(self, method, path, body=None, content_type='application/json', metadata=None):
        headers = {'Accept': 'application/json', 'Authorization': self.engine.auth}
        data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        if data is not None: headers['Content-Type'] = content_type
        request = urllib.request.Request(self.engine.base_url + path, data=data, headers=headers, method=method)
        started = datetime.now(timezone.utc).isoformat(); clock = time.monotonic()
        row = {'index': self.index, 'method': method, 'path': path,
               'body': body if metadata is None else metadata, 'started_at': started}
        self.index += 1
        try:
            try:
                with urllib.request.urlopen(request, timeout=30, context=self.engine.context) as response:
                    status, raw = response.status, response.read()
            except urllib.error.HTTPError as error:
                status, raw = error.code, error.read()
            row.update(status=status, response=json.loads(raw) if raw else None)
        except Exception as error:
            row.update(status=None, transport_error=type(error).__name__)
            raise
        finally:
            row.update(finished_at=datetime.now(timezone.utc).isoformat(), elapsed_s=time.monotonic()-clock)
            with (self.output/'requests.ndjson').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, allow_nan=False) + '\n')
        return row

    def get(self, path):
        row = self.call('GET', path)
        if row['status'] != 200: raise RuntimeError('Read failed: ' + path.split('?')[0])
        return row

    def snapshot(self):
        result = {'at': datetime.now(timezone.utc).isoformat(), 'inventories': {}}
        for name, path in INVENTORIES.items():
            listing = self.get(path + '?maxResults=1000'); count = self.get(path + '/count')
            if len(listing['response']) != count['response']['count']:
                raise RuntimeError('Incomplete or changing inventory: ' + name)
            result['inventories'][name] = {'rows': listing['response'], 'count': count['response']['count'],
                'list_request': listing['index'], 'count_request': count['index']}
        return result


def render_model(template, key, event):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', key) or not re.fullmatch(r'[A-Za-z0-9_.-]+', event):
        raise ValueError('Unsafe model identifier')
    tree = ET.fromstring(template)
    tree.find('{' + BPMN + '}process').set('id', key)
    definitions = [e for e in tree if e.tag in ('{' + BPMN + '}message', '{' + BPMN + '}signal')]
    if len(definitions) != 1: raise ValueError('Expected one named event')
    definitions[0].set('name', event)
    return ET.tostring(tree, encoding='utf-8', xml_declaration=True)


def deploy(api, path, tenant):
    boundary = 'fnv06-' + uuid.uuid4().hex; parts = []
    fields = {'deployment-name': path.stem, 'enable-duplicate-filtering': 'false'}
    if tenant: fields['tenant-id'] = tenant
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="model"; filename="{path.name}"\r\nContent-Type: application/xml\r\n\r\n'.encode(),
                  path.read_bytes(), f'\r\n--{boundary}--\r\n'.encode()])
    row = api.call('POST', '/deployment/create', b''.join(parts), 'multipart/form-data; boundary=' + boundary,
                   {'fields': fields, 'resource': path.name, 'resource_sha256': sha(path)})
    if row['status'] != 200: raise RuntimeError('Deployment failed')
    definitions = list(row['response']['deployedProcessDefinitions'].values())
    if len(definitions) != 1: raise RuntimeError('Unexpected deployment inventory')
    definition = definitions[0]
    xml = api.get('/process-definition/' + definition['id'] + '/xml')
    if xml['response']['bpmn20Xml'].encode() != path.read_bytes(): raise RuntimeError('Deployed model differs')
    return {'definition': definition, 'deployment_request': row['index'], 'xml_request': xml['index'],
            'model_file': path.relative_to(api.output).as_posix(), 'model_sha256': sha(path)}


def resolve(value, context):
    if isinstance(value, dict): return {k: resolve(v, context) for k, v in value.items()}
    if isinstance(value, list): return [resolve(v, context) for v in value]
    if not isinstance(value, str) or not value.startswith('@'): return value
    if value == '@delivery': return context['prefix'] + '-delivered'
    if value == '@missing_execution': return context['prefix'] + '-missing-execution'
    kind, token = value[1:].split(':', 1)
    if kind in ('event', 'key'): return context['prefix'] + '-' + kind + '-' + token
    if kind == 'tenant': return context['tenants'][token]
    if kind == 'pid': return context['processes'][token]['id']
    if kind == 'execution':
        pid = context['processes'][token]['id']
        rows = [s for s in context['before']['inventories']['subscriptions']['rows'] if s['processInstanceId'] == pid]
        if len(rows) != 1: raise ValueError('Target execution is not unique')
        return rows[0]['executionId']
    raise ValueError('Unknown input token')


def await_batch(api, batch, timeout_s=120):
    deadline = time.monotonic() + timeout_s; observations = []
    ids = {batch[k] for k in ('batchJobDefinitionId', 'seedJobDefinitionId', 'monitorJobDefinitionId')}
    while True:
        runtime = api.get('/batch?batchId=' + batch['id'])
        history = api.get('/history/batch?batchId=' + batch['id'])
        incidents = api.get('/incident')
        observations.append({'runtime_request': runtime['index'], 'history_request': history['index'], 'incident_request': incidents['index']})
        if incidents['response']: raise RuntimeError('Batch incident; retain the attempt')
        if not runtime['response'] and len(history['response']) == 1 and history['response'][0].get('endTime'):
            break
        if time.monotonic() >= deadline: raise TimeoutError('Batch did not complete within the fixed functional wait')
        time.sleep(.5)
    logs = []
    for definition in sorted(ids):
        if hasattr(api, 'collection'):
            logs.append(api.collection('/history/job-log', {'jobDefinitionId': definition}))
        else:
            logs.append(api.get('/history/job-log?jobDefinitionId=' + definition + '&maxResults=1000'))
    return {'observations': observations, 'final_history': history['response'][0], 'job_logs': logs}


def exercise(api, corpus, run_id, username, summary):
    output = api.output; (output/'models').mkdir(); (output/'cases').mkdir()
    # The pinned engine's default identity-resource validator permits only
    # alphanumeric tenant IDs. Business keys and event names are independent.
    tenants = {role: 'fnv06' + run_id[:12] + role for role in ('alpha', 'beta')}
    for tenant in tenants.values():
        for method, path, body in [('POST', '/tenant/create', {'id':tenant, 'name':tenant}),
                                   ('PUT', '/tenant/' + tenant + '/user-members/' + username, None)]:
            row = api.call(method, path, body)
            if row['status'] not in (200, 204): raise RuntimeError('Owned tenant setup failed')
    save(output/'baseline.json', api.snapshot())
    for case in corpus['cases']:
        context = {'prefix':'fnv06-' + run_id[:12] + '-' + case['id'], 'tenants':tenants, 'processes':{}}
        record = {'case':case, 'prefix':context['prefix'], 'tenants':tenants, 'models':[], 'processes':{}, 'steps':[]}
        path = output/'cases'/(case['id'] + '.json'); models = {}
        for alias, fixture in case['instances'].items():
            variant = (fixture['event'], fixture['tenant'])
            if variant not in models:
                key = 'bench_fnv06_' + case['id'] + '_' + fixture['event']
                event = resolve('@event:' + fixture['event'], context)
                template = (output/'inputs/workloads/fnv06'/(case['kind']+'.bpmn')).read_bytes()
                model = output/'models'/(case['id']+'-'+fixture['event']+'-'+(fixture['tenant'] or 'none')+'.bpmn')
                model.write_bytes(render_model(template, key, event))
                item = deploy(api, model, tenants.get(fixture['tenant'])); models[variant] = item
                record['models'].append(item)
            definition = models[variant]['definition']
            body = {'businessKey': resolve('@key:' + fixture['key'], context), 'variables': {
                'caseId': {'value':case['id'], 'type':'String'}, 'alias': {'value':alias, 'type':'String'},
                'sentinel': {'value':context['prefix']+'-'+alias, 'type':'String'}}}
            row = api.call('POST', '/process-definition/'+definition['id']+'/start', body)
            if row['status'] != 200: raise RuntimeError('Instance start failed')
            item = {'id':row['response']['id'], 'definition':definition, 'start_request':row['index'], 'input':body}
            record['processes'][alias] = context['processes'][alias] = item
            summary['owned_process_ids'].append(item['id']); save(output/'progress.json', summary)
        record['before'] = context['before'] = api.snapshot(); save(path, record)
        for step in case['steps']:
            row = api.call('POST', step['path'], resolve(step['body'], context))
            entry = {'request':row, 'batch':None}; record['steps'].append(entry); save(path, record)
            if row['status'] != step['http_status']: raise RuntimeError('Unexpected functional response in '+case['id'])
            if step.get('batch'):
                summary['owned_batch_ids'].append(row['response']['id']); save(output/'progress.json', summary)
                entry['batch'] = await_batch(api, row['response'])
            entry['after'] = api.snapshot(); save(path, record)
        summary['cases_collected'].append(case['id']); save(output/'progress.json', summary)
    save(output/'before-cleanup.json', api.snapshot())
