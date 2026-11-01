"""Synthetic FNV-07 receipts with durable attempt/effect separation.

This is a benchmark fixture, not a payment backend. Mutations bind to an
immutable registered process and original input. Every accepted execute request
is retained, including failed responses and cached replays.
"""
from contextlib import contextmanager
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import parse_qs

from services.workflow_checks import canonical, fingerprint

STAGES = ('stage1', 'stage2', 'stage3')
MODES = ('normal', 'fail_before_once', 'fail_after_once', 'fail_before_always', 'fail_after_always')
FIELDS = {'run_id', 'case_key', 'process_id', 'business_key', 'input_sha256', 'stage', 'node_id', 'invocation_id'}


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', value) is not None


class ProbeStore:
    def __init__(self, path, run_id, nodes):
        if not identifier(run_id) or not nodes or len(set(nodes)) != len(nodes) or not all(identifier(n) for n in nodes):
            raise ValueError('invalid_owned_identity')
        self.path, self.run_id, self.nodes = str(path), run_id, tuple(nodes)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS nodes (node_id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cases (
                  case_key TEXT PRIMARY KEY, process_id TEXT UNIQUE NOT NULL,
                  business_key TEXT UNIQUE NOT NULL, body TEXT NOT NULL, input_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS effects (
                  case_key TEXT NOT NULL, stage TEXT NOT NULL, process_id TEXT NOT NULL,
                  input_sha256 TEXT NOT NULL, effect_id TEXT UNIQUE NOT NULL, receipt_id TEXT UNIQUE NOT NULL,
                  first_invocation_id TEXT NOT NULL, PRIMARY KEY(case_key,stage));
                CREATE TABLE IF NOT EXISTS attempts (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, request TEXT NOT NULL,
                  case_key TEXT NOT NULL, stage TEXT NOT NULL, invocation_id TEXT NOT NULL,
                  started_ns INTEGER NOT NULL, finished_ns INTEGER NOT NULL,
                  status INTEGER NOT NULL, disposition TEXT NOT NULL, receipt_id TEXT);
                CREATE INDEX IF NOT EXISTS attempts_case_stage ON attempts(case_key,stage);
                CREATE INDEX IF NOT EXISTS attempts_invocation ON attempts(invocation_id);
            ''')
            wanted = {'run_id': run_id, 'nodes': canonical(list(nodes))}
            old = dict(db.execute('SELECT key,value FROM metadata'))
            if old and old != wanted:
                raise ValueError('store_identity_mismatch')
            db.executemany('INSERT OR IGNORE INTO metadata VALUES(?,?)', wanted.items())

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def node(self, node_id, data):
        if node_id not in self.nodes or not isinstance(data, dict) or not data or len(canonical(data)) > 16000:
            raise ValueError('invalid_node_identity')
        if any(not isinstance(k, str) or not isinstance(v, str) or len(k) > 100 or len(v) > 300 for k, v in data.items()):
            raise ValueError('invalid_node_configuration')
        body = canonical(data)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT body FROM nodes WHERE node_id=?', (node_id,)).fetchone()
            if old and old['body'] != body:
                raise ValueError('node_configuration_changed')
            db.execute('INSERT OR IGNORE INTO nodes VALUES(?,?)', (node_id, body))

    def register(self, case_key, process_id, business_key, case):
        if not all(identifier(v) for v in (case_key, process_id, business_key)) or not isinstance(case, dict):
            raise ValueError('invalid_binding')
        if case.get('mode') not in MODES or not identifier(case.get('id')) or len(canonical(case)) > 8000:
            raise ValueError('invalid_case')
        body, digest = canonical(case), fingerprint(case)
        wanted = (case_key, process_id, business_key, body, digest)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM cases WHERE case_key=? OR process_id=? OR business_key=?', wanted[:3]).fetchall()
            if old and (len(old) != 1 or tuple(old[0]) != wanted):
                raise ValueError('case_binding_conflict')
            db.execute('INSERT OR IGNORE INTO cases VALUES(?,?,?,?,?)', wanted)
        return {'case_key': case_key, 'process_id': process_id, 'input_sha256': digest}

    def execute(self, request):
        if not isinstance(request, dict) or set(request) != FIELDS or not all(identifier(v) for v in request.values()):
            return 400, {'error': 'invalid_request'}
        started = time.time_ns()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM cases WHERE case_key=?', (request['case_key'],)).fetchone()
            valid = (row is not None and request['run_id'] == self.run_id and request['node_id'] in self.nodes
                     and db.execute('SELECT 1 FROM nodes WHERE node_id=?', (request['node_id'],)).fetchone()
                     and request['stage'] in STAGES
                     and all(request[k] == row[k] for k in ('process_id', 'business_key', 'input_sha256')))
            status, disposition, receipt = 403, 'binding_rejected', None
            reused = db.execute('SELECT request FROM attempts WHERE invocation_id=?', (request['invocation_id'],)).fetchall()
            if any(r['request'] != canonical(request) for r in reused):
                valid = False
            if valid:
                stage, key = request['stage'], request['case_key']
                earlier = STAGES[:STAGES.index(stage)]
                existing = {r['stage'] for r in db.execute('SELECT stage FROM effects WHERE case_key=?', (key,))}
                if not set(earlier) <= existing:
                    disposition = 'stage_order_rejected'
                else:
                    calls = db.execute('SELECT count(*) FROM attempts WHERE case_key=? AND stage=? AND status IN (200,503)',
                                       (key, stage)).fetchone()[0]
                    mode = json.loads(row['body'])['mode'] if stage == 'stage2' else 'normal'
                    fail = mode.endswith('_always') or (mode.endswith('_once') and calls == 0)
                    if mode.startswith('fail_before') and fail:
                        status, disposition = 503, 'failed_before_effect'
                    else:
                        old = db.execute('SELECT * FROM effects WHERE case_key=? AND stage=?', (key, stage)).fetchone()
                        if old is None:
                            identity = self.run_id + '|' + key + '|' + row['process_id'] + '|' + stage
                            effect_id = hashlib.sha256(('effect|' + identity).encode()).hexdigest()
                            receipt_id = hashlib.sha256(('receipt|' + identity).encode()).hexdigest()
                            db.execute('INSERT INTO effects VALUES(?,?,?,?,?,?,?)',
                                       (key, stage, row['process_id'], row['input_sha256'], effect_id, receipt_id, request['invocation_id']))
                            disposition = 'committed'
                            old = db.execute('SELECT * FROM effects WHERE case_key=? AND stage=?', (key, stage)).fetchone()
                        else:
                            disposition = 'cached'
                        receipt = dict(old)
                        status = 503 if mode.startswith('fail_after') and fail else 200
                        if status == 503:
                            disposition += '_response_withheld'
            db.execute('INSERT INTO attempts(request,case_key,stage,invocation_id,started_ns,finished_ns,status,disposition,receipt_id) VALUES(?,?,?,?,?,?,?,?,?)',
                       (canonical(request), request['case_key'], request['stage'], request['invocation_id'],
                        started, time.time_ns(), status, disposition, receipt['receipt_id'] if receipt else None))
        response = {'disposition': disposition}
        if receipt is not None:
            response.update(receipt)
        return status, response

    def snapshot(self):
        with self.connect() as db:
            db.execute('BEGIN')
            return {table: [dict(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY ' + order)]
                    for table, order in [('metadata', 'key'), ('nodes', 'node_id'), ('cases', 'case_key'),
                                         ('effects', 'case_key,stage'), ('attempts', 'seq')]}


def make_server(store, *, admin_token, execute_token, address=('127.0.0.1', 0)):
    if not admin_token or not execute_token or admin_token == execute_token:
        raise ValueError('separate_private_tokens_required')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, data, properties=False):
            raw = (''.join(str(k) + '=' + str(v) + '\n' for k, v in sorted(data.items())).encode('ascii')
                   if properties else json.dumps(data, allow_nan=False).encode())
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; charset=us-ascii' if properties else 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            try:
                self.connection.settimeout(10)
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 32768:
                    raise ValueError('invalid_length')
                # Consume a bounded body before denying credentials. Unread
                # request bytes can reset a Windows client's socket and hide 403.
                raw = self.rfile.read(length)
                expected = admin_token if self.path in ('/register', '/snapshot') else execute_token
                if not hmac.compare_digest(self.headers.get('X-Fnv07-Token', ''), expected):
                    return self.reply(403, {'error': 'unauthorized'})
                if self.path == '/snapshot':
                    if json.loads(raw) != {}:
                        raise ValueError('invalid_snapshot_request')
                    return self.reply(200, store.snapshot())
                if self.path == '/register':
                    body = json.loads(raw)
                    if set(body) != {'case_key', 'process_id', 'business_key', 'case'}:
                        raise ValueError('invalid_registration')
                    return self.reply(200, store.register(**body))
                form = parse_qs(raw.decode('ascii'), strict_parsing=True, keep_blank_values=True)
                if any(len(values) != 1 for values in form.values()):
                    raise ValueError('duplicate_fields')
                body = {k: v[0] for k, v in form.items()}
                if self.path == '/node':
                    node_id = body.pop('node_id')
                    store.node(node_id, body)
                    return self.reply(200, {'registered': node_id}, properties=True)
                if self.path == '/execute':
                    status, response = store.execute(body)
                    return self.reply(status, response, properties=True)
                return self.reply(404, {'error': 'unknown_route'})
            except (ValueError, KeyError, TypeError, UnicodeError):
                return self.reply(400, {'error': 'invalid_request'})

    server = ThreadingHTTPServer(address, Handler)
    server.daemon_threads = True
    return server


if __name__ == '__main__':
    import argparse
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--nodes', nargs='+', required=True)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8090)
    args = parser.parse_args()
    server = make_server(ProbeStore(args.database, args.run_id, args.nodes),
        admin_token=os.environ['FNV07_ADMIN_TOKEN'], execute_token=os.environ['FNV07_EXECUTE_TOKEN'],
        address=(args.bind, args.port))
    try:
        server.serve_forever()
    finally:
        server.server_close()
