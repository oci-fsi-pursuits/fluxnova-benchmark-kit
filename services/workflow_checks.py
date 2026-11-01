"""W02 synthetic dependencies and bound settlement, isolated from baseline services.

The trusted harness registers immutable cases and process IDs. Workers submit only
the registered key, process ID and operation; engine variables cannot authorize a
payment. This is a deterministic research fixture, not a production payment service.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import unquote, urlsplit
from services.http_support import drain_rejected_body


OPERATIONS = ('kyc', 'risk', 'settle')
FAULTS = ('none', 'unknown', 'before_once', 'after_once')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate_case(case):
    if not isinstance(case, dict) or not isinstance(case.get('case_id'), str):
        raise ValueError('invalid_case')
    p = case.get('payment', {})
    for name in ('authorized', 'sanctions_match', 'high_risk', 'stale_market'):
        if type(p.get(name)) is not bool:
            raise ValueError('invalid_payment_boolean')
    for name in ('amount_minor', 'limit_minor'):
        if type(p.get(name)) is not int or p[name] < 0:
            raise ValueError('invalid_payment_number')
    if p['amount_minor'] == 0:
        raise ValueError('invalid_payment_number')
    for name in ('payment_id', 'beneficiary'):
        if not isinstance(p.get(name), str) or not 1 <= len(p[name]) <= 128:
            raise ValueError('invalid_payment_identity')
    for operation in ('kyc', 'risk'):
        schedule = case.get('schedule', {}).get(operation, {})
        if type(schedule.get('delay_ms')) is not int or not 0 <= schedule['delay_ms'] <= 5000:
            raise ValueError('invalid_delay')
        if schedule.get('fault') not in FAULTS:
            raise ValueError('invalid_fault')
    canonical(case)


class StudyStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS cases (
                  key TEXT PRIMARY KEY, process_id TEXT UNIQUE NOT NULL,
                  body TEXT NOT NULL, fingerprint TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (
                  key TEXT NOT NULL, operation TEXT NOT NULL, body TEXT NOT NULL,
                  PRIMARY KEY(key, operation));
                CREATE TABLE IF NOT EXISTS effects (
                  key TEXT PRIMARY KEY, payment_id TEXT NOT NULL,
                  amount_minor INTEGER NOT NULL, beneficiary TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS faults (
                  key TEXT NOT NULL, operation TEXT NOT NULL, fault TEXT NOT NULL,
                  PRIMARY KEY(key,operation,fault));
                CREATE TABLE IF NOT EXISTS audit (
                  id INTEGER PRIMARY KEY, key TEXT NOT NULL,
                  operation TEXT NOT NULL, event TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def register(self, key, process_id, case):
        validate_case(case)
        if any(not isinstance(v, str) or not 1 <= len(v) <= 160 for v in (key, process_id)):
            raise ValueError('invalid_binding')
        wanted = (process_id, canonical(case), fingerprint(case))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT process_id,body,fingerprint FROM cases WHERE key=?', (key,)).fetchone()
            if old and old != wanted:
                raise ValueError('case_binding_conflict')
            if not old:
                db.execute('INSERT INTO cases VALUES(?,?,?,?)', (key, *wanted))
        return {'case_key': key, 'process_id': process_id, 'input_sha256': wanted[2]}

    def state(self, key):
        with self.connect() as db:
            row = db.execute('SELECT process_id,body,fingerprint FROM cases WHERE key=?', (key,)).fetchone()
            receipts = {op: json.loads(body) for op, body in db.execute('SELECT operation,body FROM receipts WHERE key=?', (key,))}
            effects = [dict(payment_id=p, amount_minor=a, beneficiary=b) for p, a, b in
                       db.execute('SELECT payment_id,amount_minor,beneficiary FROM effects WHERE key=?', (key,))]
            audit = [dict(operation=op, event=event) for op, event in
                     db.execute('SELECT operation,event FROM audit WHERE key=? ORDER BY id', (key,))]
        return {'case_key': key, 'process_id': row[0] if row else None,
                'input_sha256': row[2] if row else None, 'receipts': receipts,
                'effects': effects, 'audit': audit}

    def execute(self, request):
        if not isinstance(request, dict) or set(request) != {'case_key', 'process_id', 'operation'}:
            return 400, {'error': 'binding_and_operation_only'}
        key, process_id, op = (request[k] for k in ('case_key', 'process_id', 'operation'))
        if any(not isinstance(v, str) or not v for v in (key, process_id, op)) or op not in OPERATIONS:
            return 400, {'error': 'invalid_operation'}
        with self.connect() as db:
            registered = db.execute('SELECT process_id,body,fingerprint FROM cases WHERE key=?', (key,)).fetchone()
        if not registered:
            return 409, {'error': 'case_not_registered'}
        if registered[0] != process_id:
            return 409, {'error': 'process_binding_mismatch'}
        case = json.loads(registered[1])
        schedule = case['schedule'].get(op, {'delay_ms': 0, 'fault': 'none'})
        started_ns = time.monotonic_ns()
        started_at = datetime.now(timezone.utc).isoformat()
        # Dependency waits are outside the write transaction so the two checks
        # really can overlap. A second receipt check serializes duplicate calls.
        with self.connect() as db:
            old = db.execute('SELECT body FROM receipts WHERE key=? AND operation=?', (key, op)).fetchone()
        if not old:
            time.sleep(schedule['delay_ms'] / 1000)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT body FROM receipts WHERE key=? AND operation=?', (key, op)).fetchone()
            if old:
                db.execute('INSERT INTO audit(key,operation,event) VALUES(?,?,?)', (key, op, 'replay'))
                return 200, {**json.loads(old[0]), 'replayed': True}
            fault = schedule['fault']
            used = db.execute('SELECT 1 FROM faults WHERE key=? AND operation=? AND fault=?', (key, op, fault)).fetchone()
            if fault == 'before_once' and not used:
                db.execute('INSERT INTO faults VALUES(?,?,?)', (key, op, fault))
                db.execute('INSERT INTO audit(key,operation,event) VALUES(?,?,?)', (key, op, 'failure_before_receipt'))
                return 503, {'error': 'injected_before_receipt'}
            p = case['payment']
            if op == 'kyc':
                outcome = ('unknown' if fault == 'unknown' else 'rejected' if p['sanctions_match']
                           else 'review' if p['high_risk'] else 'approved')
            elif op == 'risk':
                outcome = ('unknown' if fault == 'unknown' else 'rejected' if p['stale_market']
                           or p['amount_minor'] > p['limit_minor'] else 'accepted')
            else:
                checks = {operation: json.loads(body) for operation, body in
                          db.execute("SELECT operation,body FROM receipts WHERE key=? AND operation IN ('kyc','risk')", (key,))}
                if set(checks) != {'kyc', 'risk'}:
                    return 409, {'error': 'checks_incomplete'}
                if any(r['input_sha256'] != registered[2] or r['process_id'] != process_id
                       or r['case_key'] != key or r['operation'] != operation for operation, r in checks.items()):
                    return 409, {'error': 'receipt_binding_mismatch'}
                if any(r['outcome'] == 'unknown' for r in checks.values()):
                    outcome = 'blocked'
                elif (checks['kyc']['outcome'] == 'approved' and checks['risk']['outcome'] == 'accepted'
                      and p['authorized'] is True and p['amount_minor'] <= p['limit_minor']):
                    outcome = 'settled'
                    db.execute('INSERT INTO effects VALUES(?,?,?,?)',
                               (key, p['payment_id'], p['amount_minor'], p['beneficiary']))
                else:
                    outcome = 'rejected'
            receipt = {'case_key': key, 'process_id': process_id, 'operation': op,
                       'input_sha256': registered[2], 'outcome': outcome,
                       'started_at': started_at, 'started_ns': started_ns,
                       'finished_ns': time.monotonic_ns()}
            db.execute('INSERT INTO receipts VALUES(?,?,?)', (key, op, canonical(receipt)))
            db.execute('INSERT INTO audit(key,operation,event) VALUES(?,?,?)', (key, op, outcome))
            if fault == 'after_once' and not used:
                db.execute('INSERT INTO faults VALUES(?,?,?)', (key, op, fault))
                db.execute('INSERT INTO audit(key,operation,event) VALUES(?,?,?)', (key, op, 'response_lost_after_receipt'))
                return 503, {'error': 'injected_after_receipt'}
            return 200, {**receipt, 'replayed': False}


def make_server(host, port, store, worker_token, registration_token, *, service_name='w02-synthetic-checks'):
    if not worker_token or not registration_token or worker_token == registration_token:
        raise ValueError('distinct_worker_and_registration_tokens_required')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, data):
            raw = canonical(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authorized(self, token):
            return hmac.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + token).encode())

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/health':
                return self.reply(200, {'service': service_name, 'status': 'ok'})
            if not (self.authorized(worker_token) or self.authorized(registration_token)):
                return self.reply(401, {'error': 'unauthorized'})
            if path.startswith('/state/'):
                return self.reply(200, store.state(unquote(path[len('/state/'):])) )
            self.reply(404, {'error': 'not_found'})

        def do_POST(self):
            path = urlsplit(self.path).path
            needed = registration_token if path == '/cases/register' else worker_token
            if not self.authorized(needed):
                drain_rejected_body(self, 65536)
                return self.reply(401, {'error': 'unauthorized'})
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 65536:
                    return self.reply(413, {'error': 'request_size'})
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('object_required')
                if path == '/cases/register':
                    return self.reply(200, store.register(body['case_key'], body['process_id'], body['case']))
                if path == '/tools/execute':
                    status, value = store.execute(body)
                    return self.reply(status, value)
                self.reply(404, {'error': 'not_found'})
            except (ValueError, KeyError, TypeError):
                self.reply(400, {'error': 'invalid_or_conflicting_case'})
            except sqlite3.Error:
                self.reply(503, {'error': 'store_unavailable'})
    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=28181)
    parser.add_argument('--database', required=True)
    args = parser.parse_args()
    server = make_server(args.host, args.port, StudyStore(args.database),
                         os.environ['W02_WORKER_TOKEN'], os.environ['W02_REGISTRATION_TOKEN'])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
