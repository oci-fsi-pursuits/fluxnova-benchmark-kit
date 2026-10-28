"""Separate benchmark signed intent registry, verification worker and review client.

HMAC authority/registry and engine worker credentials belong to the trusted
operator. The agent/reviewer receive an intent token, never the signing key.
This worker handles only bench-bound-review; baseline images are unchanged.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import urllib.parse
import uuid
from contextlib import contextmanager

from services.payment_approval import snapshot, typed
from services.recoverable_agent import atomic_json, digest
from services.worker import task_request
from services.approval_agent import ApprovalAgentTools, DeadlineClient
from services.recoverable_agent import owner
from services.signing_keys import (SigningKeys, authority_fingerprint, signing_fields,
                                   token_for_raw, authenticate_token, authenticate_registered)

DEFINITION = 'bench_bound_payment_approval'
TOPIC = 'bench-bound-review'
BENEFICIARY = 'synthetic-beneficiary'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def payment_request(intent, scenario='settlement', key=None):
    p = intent['payment']
    values = {**p, 'scenario': scenario, 'approved': scenario == 'settlement',
              'logical_request_id': key or intent['key']}
    request = task_request({'variables': typed(values)})
    if scenario == 'settlement': request['payload']['beneficiary'] = BENEFICIARY
    return request


class IntentRegistry:
    def __init__(self, path, secret):
        fingerprint = authority_fingerprint(secret)
        self.path, self.secret = str(path), secret
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS intents (key TEXT PRIMARY KEY, body TEXT NOT NULL, token TEXT NOT NULL, process_id TEXT UNIQUE, screening_id TEXT, screening_key TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS registry_meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS issuance_requests (request_id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL)')
            stored = db.execute("SELECT value FROM registry_meta WHERE name='signing_key_sha256'").fetchone()
            if stored and stored[0] != fingerprint: raise ValueError('registry_signing_key_mismatch')
            rows = db.execute('SELECT key,body,token FROM intents').fetchall()
            if not stored and rows and isinstance(secret, SigningKeys):
                raise ValueError('registry_requires_explicit_key_rotation')
            for intent_key, raw, token in rows:
                try:
                    authenticate_registered(raw, token, secret)
                    if json.loads(raw).get('key') != intent_key:raise ValueError('registry_intent_key_mismatch')
                except ValueError:raise ValueError('registry_signing_key_mismatch') from None
            if not stored:
                db.execute("INSERT INTO registry_meta(name,value) VALUES('signing_key_sha256',?)",(fingerprint,))
            generation = db.execute("SELECT value FROM registry_meta WHERE name='key_generation'").fetchone()
            self.key_generation = generation[0] if generation else '0'
            if (not self.key_generation.isdecimal() or len(self.key_generation)>20
                    or str(int(self.key_generation))!=self.key_generation):
                raise ValueError('registry_key_generation_invalid')
            self.key_fingerprint = fingerprint

    def require_key_policy(self, db):
        fingerprint = db.execute("SELECT value FROM registry_meta WHERE name='signing_key_sha256'").fetchone()
        generation = db.execute("SELECT value FROM registry_meta WHERE name='key_generation'").fetchone()
        if (fingerprint != (self.key_fingerprint,)
                or (generation[0] if generation else '0') != self.key_generation):
            raise ValueError('registry_signing_key_mismatch')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db: yield db
        finally: db.close()

    def issue(self, payment, reviewer, ttl_s=3600, request_id=None):
        if type(ttl_s) is not int or not 1 <= ttl_s <= 3600 or not reviewer: raise ValueError('invalid_intent_policy')
        if request_id is not None and (not isinstance(request_id,str) or not 1 <= len(request_id) <= 128):
            raise ValueError('invalid_issuance_request_id')
        key = 'bound-' + uuid.uuid4().hex
        snapshot(payment, key)
        body = {**signing_fields(self.secret), 'key': key, 'payment': dict(payment), 'beneficiary': BENEFICIARY,
                'reviewer': reviewer, 'expires_utc_s': time.time() + ttl_s, 'definition': DEFINITION}
        raw = canonical(body)
        token = token_for_raw(raw, self.secret)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.require_key_policy(db)
            fingerprint = digest({'payment':payment,'reviewer':reviewer,'ttl_s':ttl_s})
            if request_id is not None:
                old = db.execute('SELECT key,fingerprint FROM issuance_requests WHERE request_id=?',(request_id,)).fetchone()
                if old:
                    if old[1] != fingerprint: raise ValueError('issuance_request_conflict')
                    previous = db.execute('SELECT body,token FROM intents WHERE key=?',(old[0],)).fetchone()
                    if not previous: raise ValueError('issuance_registry_corrupt')
                    authenticate_registered(previous[0], previous[1], self.secret)
                    original = json.loads(previous[0])
                    return {'version':1,'key':original['key'],'payment':original['payment'],'token':previous[1],'reviewer':original['reviewer']}
            db.execute('INSERT INTO intents(key,body,token) VALUES(?,?,?)', (key, raw.decode(), token))
            if request_id is not None:
                db.execute('INSERT INTO issuance_requests(request_id,key,fingerprint) VALUES(?,?,?)',(request_id,key,fingerprint))
        return {'version': 1, 'key': key, 'payment': dict(payment), 'token': token, 'reviewer': reviewer}

    def load(self, key):
        with self.connect() as db:
            self.require_key_policy(db)
            row = db.execute('SELECT body,token,process_id,screening_id,screening_key FROM intents WHERE key=?', (key,)).fetchone()
        if not row: raise ValueError('unknown_intent')
        authenticate_registered(row[0], row[1], self.secret)
        if json.loads(row[0]).get('key') != key:raise ValueError('intent_registry_mismatch')
        return {'body': json.loads(row[0]), 'token': row[1], 'process_id': row[2], 'screening_id': row[3], 'screening_key': row[4]}

    def bind(self, key, process_id, screening_id, screening_key):
        if not all(isinstance(v, str) and v for v in (process_id, screening_id, screening_key)): raise ValueError('invalid_binding')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.require_key_policy(db)
            row = db.execute('SELECT process_id,screening_id,screening_key,body,token FROM intents WHERE key=?', (key,)).fetchone()
            if row is None: raise ValueError('unknown_intent')
            wanted = (process_id, screening_id, screening_key)
            if row[0] is not None and tuple(row[:3]) != wanted: raise ValueError('intent_already_bound')
            authenticate_registered(row[3], row[4], self.secret)
            if isinstance(self.secret, SigningKeys) and tuple(row[:3]) != wanted:
                self.secret.require_authorized(json.loads(row[3]))
            db.execute('UPDATE intents SET process_id=?,screening_id=?,screening_key=? WHERE key=?', (*wanted, key))

    def require_send_authority(self, body):
        with self.connect() as db:self.require_key_policy(db)
        if isinstance(self.secret, SigningKeys):self.secret.require_authorized(body)

    def verify(self, task, values, now=None, allow_expired=False, *, receipt_only=False):
        # businessKey/processInstanceId are engine task metadata, not variables.
        record = self.load(task.get('businessKey'))
        body = record['body']
        token = values.get('boundIntent')
        raw = authenticate_token(token, self.secret, archival=receipt_only)
        if token != record['token'] or raw != canonical(body): raise ValueError('intent_registry_mismatch')
        if record['process_id'] != task.get('processInstanceId'): raise ValueError('intent_process_mismatch')
        if not allow_expired and (time.time() if now is None else now) >= body['expires_utc_s']: raise ValueError('intent_expired')
        expected = {**snapshot(body['payment'], body['key']), 'paymentBeneficiary': body['beneficiary'],
                    'reviewedPaymentSha256': digest(body['payment']), 'reviewerDecision': 'approve'}
        if any(values.get(k) != v or type(values.get(k)) is not type(v) for k, v in expected.items()):
            raise ValueError('review_payment_changed')
        return record


def verify_history(engine, support, record):
    body = record['body']; pid = record['process_id']
    history = engine.request('GET', '/history/process-instance/' + pid)
    if history.get('businessKey') != body['key'] or history.get('processDefinitionKey') != DEFINITION:
        raise ValueError('review_process_identity_mismatch')
    query = urllib.parse.urlencode({'processInstanceId': pid})
    tasks = engine.request('GET', '/history/task?' + query)
    reviews = [t for t in tasks if t.get('taskDefinitionKey') == 'payment_review' and t.get('endTime')]
    if len(reviews) != 1: raise ValueError('missing_completed_review')
    operations = engine.request('GET', '/history/user-operation?' + query)
    completed = [op for op in operations if op.get('taskId') == reviews[0]['id'] and op.get('operationType') == 'Complete']
    if not completed or any(op.get('userId') != body['reviewer'] for op in completed):
        raise ValueError('review_actor_mismatch')
    screen = engine.request('GET', '/history/process-instance/' + record['screening_id'])
    if (screen.get('state') != 'COMPLETED' or screen.get('processDefinitionKey') != 'bench_agent_screen'
            or screen.get('businessKey') != record['screening_key']):
        raise ValueError('screening_identity_mismatch')
    state = support.request('GET', '/state/' + record['screening_key'])
    request = payment_request(body, 'kyc', record['screening_key'])
    fingerprint = digest({'operation': request['operation'], 'payload': request['payload']})
    if (state.get('fingerprint') != fingerprint or (state.get('result') or {}).get('outcome') != 'approved'
            or state.get('effect_count') != 0 or state.get('violations')):
        raise ValueError('screening_payment_mismatch')


class BoundWorker:
    def __init__(self, engine, support, registry, emit=lambda row: None, checkpoint=lambda event,task:None):
        self.engine, self.support, self.registry, self.emit = engine, support, registry, emit
        self.worker_id = 'bound-worker-' + uuid.uuid4().hex
        self.checkpoint = checkpoint

    def process(self, task):
        values = {k: v.get('value') for k, v in task.get('variables', {}).items()}
        reason = None
        try:
            # An expired intent may reconcile a prior receipt, never authorize
            # a new send. Identity, signature, snapshot and actor still match.
            # Archival MAC verification permits receipt lookup, never a send.
            # Exact registered bytes, process binding and review still match.
            record = self.registry.verify(task, values, allow_expired=True, receipt_only=True)
            verify_history(self.engine, self.support, record)
        except ValueError as error:
            outcome, reason = 'integrity_rejected', str(error)
        else:
            request = payment_request(record['body'])
            # Reconcile before every possible send, including worker restart.
            state = self.support.request('GET', '/state/' + record['body']['key'])
            fingerprint = digest({'operation': request['operation'], 'payload': request['payload']})
            if state.get('result'):
                if state.get('fingerprint') != fingerprint or state.get('violations'): raise ValueError('ledger_conflict')
                result = state['result']
            else:
                denial = None
                try:self.registry.require_send_authority(record['body'])
                except ValueError as error:denial = str(error)
                if denial is None and time.time() >= record['body']['expires_utc_s']:denial = 'intent_expired'
                if denial:
                    self.engine.request('POST','/external-task/'+task['id']+'/complete',{'workerId':self.worker_id,
                        'variables':typed({'workerResult':'integrity_rejected','integrityReason':denial})})
                    self.emit({'process_id':task['processInstanceId'],'key':task.get('businessKey'),'outcome':'integrity_rejected','reason':denial})
                    return
                self.checkpoint('before_ledger_send',task)
                self.registry.require_send_authority(record['body'])
                if time.time() >= record['body']['expires_utc_s']: raise ValueError('intent_expired_before_send')
                result = self.support.request('POST', '/tools/execute', request)
            state = self.support.request('GET', '/state/' + record['body']['key'])
            expected = 'settled' if record['body']['payment']['authorized'] and not record['body']['payment']['sanctions_match'] else 'rejected'
            if (state.get('fingerprint') != fingerprint or result.get('outcome') != expected
                    or state.get('effect_count') != int(expected == 'settled') or state.get('violations')):
                raise ValueError('ledger_verification_failed')
            outcome = result['outcome']
            self.checkpoint('after_ledger_commit',task)
        self.engine.request('POST', '/external-task/' + task['id'] + '/complete',
                            {'workerId': self.worker_id, 'variables': typed({'workerResult': outcome, 'integrityReason': reason or 'verified'})})
        self.emit({'process_id': task['processInstanceId'], 'key': task.get('businessKey'), 'outcome': outcome, 'reason': reason})

    def poll(self):
        tasks = self.engine.request('POST', '/external-task/fetchAndLock', {'workerId': self.worker_id, 'maxTasks': 1,
            'topics': [{'topicName': TOPIC, 'lockDuration': 60000}]})
        for task in tasks or []: self.process(task)
        return len(tasks or [])


class BoundReview:
    def __init__(self, directory, engine, support, intent):
        self.path = Path(directory) / 'bound-review.json'
        self.engine, self.support, self.intent = engine, support, intent
        snapshot(intent['payment'], intent['key'])
        root = Path(__file__).resolve().parents[1]
        context = {'engine': engine.base, 'support': support.base, 'intent_sha256': digest(intent),
                   'source_sha256': hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
                   'bpmn_sha256': hashlib.sha256((root/'workloads/bound-payment-approval.bpmn').read_bytes().replace(b'\r\n', b'\n')).hexdigest()}
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if self.data.get('version') != 1 or self.data['context'] != context: raise ValueError('bound_review_context_mismatch')
        else:
            self.data = {'version': 1, 'context': context, 'key': intent['key']}; self.save()

    def save(self): atomic_json(self.path, self.data)

    def history(self):
        query = urllib.parse.urlencode({'processInstanceBusinessKey': self.intent['key'], 'processDefinitionKey': DEFINITION})
        return self.engine.request('GET', '/history/process-instance?' + query)

    def start(self):
        if not self.data.get('start_intent'):
            self.data['start_intent'] = True; self.save()
            variables = {**snapshot(self.intent['payment'], self.intent['key']), 'paymentBeneficiary': BENEFICIARY,
                         'boundIntent': self.intent['token'], 'reviewerDecision': 'pending'}
            response = self.engine.request('POST', '/process-definition/key/' + DEFINITION + '/start',
                                           {'businessKey': self.intent['key'], 'variables': typed(variables)})
            self.data['process_id'] = response['id']; self.save()
        if not self.data.get('process_id'):
            matches = self.history()
            if len(matches) != 1: raise ValueError('unresolved_bound_start_no_retry')
            self.data['process_id'] = matches[0]['id']; self.save()
        return self.status()

    def status(self):
        matches = self.history(); pid = self.data['process_id']
        if len(matches) != 1 or matches[0]['id'] != pid: raise ValueError('nonunique_bound_process')
        query = urllib.parse.urlencode({'processInstanceId': pid})
        state = self.support.request('GET', '/state/' + self.intent['key'])
        tasks = self.engine.request('GET', '/task?' + query)
        if not matches[0].get('endTime'):
            if tasks:
                if len(tasks) != 1 or tasks[0]['taskDefinitionKey'] != 'payment_review' or state['effect_count'] != 0 or state['result']:
                    raise ValueError('invalid_pending_bound_review')
                return {'state': 'pending', 'process_id': pid, 'task_id': tasks[0]['id'], 'effect_count': 0}
            return {'state': 'settling', 'process_id': pid}
        if matches[0]['state'] != 'COMPLETED' or tasks: raise ValueError('bound_terminal_mismatch')
        values = {v['name']: v.get('value') for v in self.engine.request('GET', '/history/variable-instance?' + query)}
        if values.get('reviewerDecision') == 'reject': outcome = 'review_rejected'
        else: outcome = values.get('workerResult')
        if outcome in ('integrity_rejected', 'review_rejected'):
            if state['result'] or state['effect_count']: raise ValueError('rejected_intent_had_effect')
        else:
            request = payment_request(self.intent)
            expected = 'settled' if self.intent['payment']['authorized'] and not self.intent['payment']['sanctions_match'] else 'rejected'
            if (outcome != expected or state['fingerprint'] != digest({'operation': request['operation'], 'payload': request['payload']})
                    or (state['result'] or {}).get('outcome') != expected or state['effect_count'] != int(expected == 'settled')):
                raise ValueError('bound_ledger_mismatch')
        if state['violations']: raise ValueError('bound_ledger_violation')
        result = {'state': 'completed', 'process_id': pid, 'outcome': outcome, 'effect_count': state['effect_count'],
                  'reason': values.get('integrityReason')}
        self.data['verified_result'] = result; self.save(); return result

    def decide(self, decision, username):
        if username != self.intent['reviewer'] or decision not in ('approve', 'reject'): raise ValueError('invalid_bound_reviewer')
        if self.data.get('decision') not in (None, decision): raise ValueError('conflicting_bound_decision')
        current = self.status()
        if current['state'] == 'completed': return current
        if self.data.get('complete_intent'):
            if current['state'] == 'pending': raise ValueError('unresolved_bound_completion_no_retry')
            return current
        if current['state'] != 'pending': raise ValueError('bound_review_not_pending')
        self.data.update(decision=decision, complete_intent=True); self.save()
        self.engine.request('POST', '/task/' + current['task_id'] + '/complete', {'variables': typed({
            'reviewerDecision': decision, 'reviewerIdentity': username, 'reviewedPaymentSha256': digest(self.intent['payment'])})})
        return self.status()


class BoundAgentTools(ApprovalAgentTools):
    def __init__(self, engine, support, journal, emit, intent):
        super().__init__(engine, support, journal, emit)
        if {k: v for k, v in self.payment.items() if k != 'approved'} != intent['payment']:
            raise ValueError('agent_intent_payment_mismatch')
        self.intent = intent

    def __call__(self, scenario, deadline):
        if scenario != 'settlement' or self.payment['amount_minor'] <= 1000:
            return super().__call__(scenario, deadline)
        folder = self.journal.path.parent / 'review'; folder.mkdir(exist_ok=True)
        with owner(folder):
            if not (folder/'bound-review.json').exists(): self.journal.allow_action()
            review = BoundReview(folder, DeadlineClient(self.engine, deadline), DeadlineClient(self.support, deadline), self.intent)
            if not review.data.get('start_intent'): self.journal.allow_action()
            result = review.start()
        self.emit({'event': 'bound_review_observed', **result})
        return result['outcome'] if result['state'] == 'completed' else 'pending_review' if result['state'] == 'pending' else 'settlement_in_progress'
