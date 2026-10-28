"""Stdlib HTTP simulator with persistent, atomic effect deduplication.

Run: python -m services.mock_service --database runs/support.sqlite --port 18081
Only synthetic data belongs here. SQLite is the simulator ledger, NOT the engine DB.
"""
import argparse
import hashlib
import hmac
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


def utcnow():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class Ledger:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS receipts (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS effects (key TEXT PRIMARY KEY, amount INTEGER NOT NULL, beneficiary TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, key TEXT NOT NULL, at TEXT NOT NULL, event TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS audit_key ON audit(key);
                CREATE TABLE IF NOT EXISTS faults (key TEXT NOT NULL, fault TEXT NOT NULL, PRIMARY KEY(key,fault));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute('PRAGMA busy_timeout=15000')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def audit(db, key, event, data):
        db.execute('INSERT INTO audit(key,at,event,data) VALUES(?,?,?,?)', (key, utcnow(), event, json.dumps(data, sort_keys=True)))

    def state(self, key):
        with self.connect() as db:
            row = db.execute('SELECT result,fingerprint FROM receipts WHERE key=?', (key,)).fetchone()
            count = db.execute('SELECT count(*) FROM effects WHERE key=?', (key,)).fetchone()[0]
            audit = [{'at': at, 'event': event, 'details': json.loads(data)} for at, event, data in db.execute('SELECT at,event,data FROM audit WHERE key=? ORDER BY id', (key,))]
        return {'idempotency_key': key, 'result': json.loads(row[0]) if row else None, 'fingerprint': row[1] if row else None, 'effect_count': count, 'events': audit, 'violations': ['duplicate_effect'] if count > 1 else []}

    def execute(self, request):
        key = request.get('idempotency_key')
        operation = request.get('operation')
        payload = request.get('payload', {})
        fault = request.get('fault', 'none')
        delay = request.get('delay_ms', 0)
        if not isinstance(key, str) or not key or len(key) > 512:
            return 400, {'error': 'invalid_idempotency_key'}
        if operation not in ('echo', 'kyc', 'risk', 'settle') or not isinstance(payload, dict):
            return 400, {'error': 'unknown_tool_or_invalid_payload'}
        if fault not in ('none', 'before_effect_once', 'after_effect_once'):
            return 400, {'error': 'unknown_fault'}
        if not isinstance(delay, (int, float)) or isinstance(delay, bool) or not 0 <= delay <= 5000:
            return 400, {'error': 'delay_ms_must_be_between_0_and_5000'}
        time.sleep(delay / 1000)
        canonical = json.dumps({'operation': operation, 'payload': payload}, sort_keys=True, separators=(',', ':'))
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT fingerprint,result FROM receipts WHERE key=?', (key,)).fetchone()
            if old:
                if old[0] != fingerprint:
                    self.audit(db, key, 'idempotency_conflict', {'operation': operation})
                    return 409, {'error': 'idempotency_payload_conflict'}
                self.audit(db, key, 'replay', {'operation': operation})
                return 200, dict(json.loads(old[1]), replayed=True)
            fault_used = db.execute('SELECT 1 FROM faults WHERE key=? AND fault=?', (key, fault)).fetchone()
            if fault == 'before_effect_once' and not fault_used:
                db.execute('INSERT INTO faults(key,fault) VALUES(?,?)', (key, fault))
                self.audit(db, key, 'injected_pre_effect_failure', {})
                return 503, {'error': 'injected_before_effect', 'retryable': True}
            result = {'outcome': 'ok', 'operation': operation, 'idempotency_key': key}
            if operation == 'kyc':
                result['outcome'] = 'rejected' if payload.get('sanctions_match', False) else 'review' if payload.get('high_risk', False) else 'approved'
            elif operation == 'risk':
                notional = payload.get('notional_minor', 100)
                limit = payload.get('limit_minor', 1000)
                if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in (notional, limit)):
                    return 400, {'error': 'invalid_risk_numbers'}
                result['outcome'] = 'rejected' if payload.get('stale_market', False) or notional > limit else 'accepted'
            elif operation == 'settle':
                amount = payload.get('amount_minor', 100)
                limit = payload.get('limit_minor', 1000)
                if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in (amount, limit)) or amount == 0:
                    return 400, {'error': 'invalid_amount_or_limit'}
                allowed = payload.get('authorized', False) is True and payload.get('sanctions_match', False) is False
                approved = amount <= limit or payload.get('approved', False) is True
                self.audit(db, key, 'policy_decision', {'allowed': allowed and approved, 'authorized': payload.get('authorized', False), 'approved': payload.get('approved', False), 'approval_required': amount > limit})
                if not allowed or not approved:
                    result['outcome'] = 'rejected'
                else:
                    beneficiary = payload.get('beneficiary', 'synthetic-beneficiary')
                    if not isinstance(beneficiary, str) or len(beneficiary) > 256:
                        return 400, {'error': 'invalid_beneficiary'}
                    db.execute('INSERT INTO effects(key,amount,beneficiary) VALUES(?,?,?)', (key, amount, beneficiary))
                    self.audit(db, key, 'payment_effect', {'amount_minor': amount, 'beneficiary': beneficiary})
                    result['outcome'] = 'settled'
            self.audit(db, key, 'completed', {'operation': operation, 'outcome': result['outcome']})
            db.execute('INSERT INTO receipts(key,fingerprint,result) VALUES(?,?,?)', (key, fingerprint, json.dumps(result, sort_keys=True)))
            if fault == 'after_effect_once' and not fault_used:
                db.execute('INSERT INTO faults(key,fault) VALUES(?,?)', (key, fault))
                self.audit(db, key, 'injected_post_effect_response_loss', {})
                return 503, {'error': 'ambiguous_after_effect', 'retryable': True}
            return 200, dict(result, replayed=False)


def mock_model(request):
    """Provider-neutral deterministic model contract; no claims of LLM quality."""
    turn = request.get('turn', 0)
    mode = request.get('mode', 'normal')
    if not isinstance(turn, int) or turn < 0:
        return 400, {'error': 'invalid_turn'}
    if mode == 'throttle':
        return 429, {'error': 'injected_throttle'}
    if mode == 'invalid_tool':
        return 200, {'tool_calls': [{'id': 'call-0', 'name': 'forbidden_tool', 'arguments': {}}], 'final': False}
    if mode == 'loop' or turn == 0:
        return 200, {'tool_calls': [{'id': f'call-{turn}', 'name': 'screen_beneficiary', 'arguments': {'beneficiary': 'synthetic-beneficiary'}}], 'final': False}
    return 200, {'tool_calls': [], 'final': True, 'output': 'complete'}


def make_server(host, port, ledger, token=''):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, value):
            data = json.dumps(value, separators=(',', ':')).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authorized(self):
            return not token or hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/health':
                return self.reply(200, {'status': 'ok', 'service': 'synthetic-benchmark-support'})
            if not self.authorized():
                return self.reply(401, {'error': 'unauthorized'})
            if path.startswith('/state/'):
                return self.reply(200, ledger.state(unquote(path[len('/state/'):])) )
            self.reply(404, {'error': 'not_found'})

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', 0))
                if length < 1 or length > 1048576:
                    return self.reply(413, {'error': 'request_size'})
                raw = self.rfile.read(length)
                if not self.authorized():
                    return self.reply(401, {'error': 'unauthorized'})
                body = json.loads(raw)
                if not isinstance(body, dict):
                    return self.reply(400, {'error': 'object_required'})
                path = urlsplit(self.path).path
                if path == '/tools/execute':
                    status, value = ledger.execute(body)
                elif path == '/model':
                    status, value = mock_model(body)
                else:
                    status, value = 404, {'error': 'not_found'}
                self.reply(status, value)
            except (ValueError, TypeError, UnicodeDecodeError):
                self.reply(400, {'error': 'invalid_request'})
            except sqlite3.Error:
                self.reply(503, {'error': 'ledger_unavailable'})

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default=os.environ.get('BENCH_BIND', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('BENCH_PORT', '18081')))
    parser.add_argument('--database', default=os.environ.get('BENCH_DB', 'runs/support.sqlite'))
    args = parser.parse_args()
    token = os.environ.get('BENCH_SUPPORT_TOKEN', '')
    if args.host not in ('127.0.0.1', 'localhost', '::1') and not token:
        parser.error('BENCH_SUPPORT_TOKEN is required when listening beyond loopback')
    server = make_server(args.host, args.port, Ledger(args.database), token)
    print(json.dumps({'service': 'benchmark-support', 'host': args.host, 'port': server.server_port}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
