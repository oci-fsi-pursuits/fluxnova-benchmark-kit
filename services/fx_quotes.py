"""Bound PAY-05 quote simulator and synthetic ledger, never a payment gateway."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

from benchlib.fx_quotes import ROUTES, select_quote, validate_intent
from services.workflow_checks import canonical, fingerprint

TOPIC = 'bench-pay05-quotes'
ACTIVITIES = {**{'quote_' + r: ('quote_' + r, 'pay05Quote' + r.upper()) for r in ROUTES},
              'select_route': ('select', 'pay05Outcome')}
OPERATIONS = tuple('quote_' + r for r in ROUTES) + ('select',)


def validate_case(case):
    if not isinstance(case, dict) or not isinstance(case.get('case_id'), str) or not 1 <= len(case['case_id']) <= 128:
        raise ValueError('invalid_case')
    validate_intent(case.get('payment'))
    minimum = case.get('minimum_valid_quotes')
    if type(minimum) is not int or not 1 <= minimum <= 3:
        raise ValueError('invalid_quote_quorum')
    if not isinstance(case.get('quotes'), dict) or set(case['quotes']) != set(ROUTES):
        raise ValueError('three_quote_routes_required')
    for schedule in case['quotes'].values():
        if not isinstance(schedule, dict) or set(schedule) != {'delay_ms', 'outcome', 'quote'}:
            raise ValueError('invalid_quote_schedule')
        if type(schedule['delay_ms']) is not int or not 0 <= schedule['delay_ms'] <= 5000:
            raise ValueError('invalid_quote_delay')
        if schedule['outcome'] not in ('quote', 'timeout'):
            raise ValueError('invalid_quote_outcome')
        if schedule['outcome'] == 'timeout' and schedule['quote'] is not None:
            raise ValueError('timeout_has_quote')
    if len(canonical(case).encode()) > 32768:
        raise ValueError('case_too_large')


class QuoteStore:
    def __init__(self, path, *, clock_ns=time.monotonic_ns, sleep=time.sleep):
        self.path = str(path)
        self.clock_ns, self.sleep = clock_ns, sleep
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS cases (
                  key TEXT PRIMARY KEY, process_id TEXT UNIQUE NOT NULL,
                  payment_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL,
                  fingerprint TEXT NOT NULL, registered_ns INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (
                  key TEXT NOT NULL, operation TEXT NOT NULL, body TEXT NOT NULL,
                  PRIMARY KEY(key,operation));
                CREATE TABLE IF NOT EXISTS effects (
                  key TEXT PRIMARY KEY, payment_id TEXT UNIQUE NOT NULL,
                  beneficiary TEXT NOT NULL, target_amount_minor INTEGER NOT NULL,
                  source_currency TEXT NOT NULL, target_currency TEXT NOT NULL,
                  route_id TEXT NOT NULL, source_total_minor INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                  id INTEGER PRIMARY KEY, key TEXT NOT NULL,
                  operation TEXT NOT NULL, event TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def register(self, key, process_id, case):
        validate_case(case)
        if any(not isinstance(v, str) or not 1 <= len(v) <= 160 for v in (key, process_id)):
            raise ValueError('invalid_binding')
        body, digest = canonical(case), fingerprint(case)
        payment_id = case['payment']['payment_id']
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM cases WHERE key=? OR process_id=? OR payment_id=?',
                             (key, process_id, payment_id)).fetchall()
            if old:
                if len(old) != 1 or any(old[0][k] != v for k, v in
                       {'key': key, 'process_id': process_id, 'payment_id': payment_id, 'body': body, 'fingerprint': digest}.items()):
                    raise ValueError('case_or_payment_binding_conflict')
            else:
                db.execute('INSERT INTO cases VALUES(?,?,?,?,?,?)',
                           (key, process_id, payment_id, body, digest, self.clock_ns()))
        return {'case_key': key, 'process_id': process_id, 'input_sha256': digest}

    def state(self, key):
        with self.connect() as db:
            registered = db.execute('SELECT * FROM cases WHERE key=?', (key,)).fetchone()
            receipts = {r['operation']: json.loads(r['body']) for r in
                        db.execute('SELECT operation,body FROM receipts WHERE key=?', (key,))}
            effects = [dict(r) for r in db.execute('SELECT * FROM effects WHERE key=?', (key,))]
            audit = [dict(r) for r in db.execute('SELECT operation,event FROM audit WHERE key=? ORDER BY id', (key,))]
        return {'case_key': key, 'process_id': registered['process_id'] if registered else None,
                'input_sha256': registered['fingerprint'] if registered else None,
                'registered_ns': registered['registered_ns'] if registered else None,
                'receipts': receipts, 'effects': effects, 'audit': audit}

    def execute(self, request):
        if not isinstance(request, dict) or set(request) != {'case_key', 'process_id', 'operation'}:
            return 400, {'error': 'binding_and_operation_only'}
        key, process_id, operation = (request[k] for k in ('case_key', 'process_id', 'operation'))
        if any(not isinstance(v, str) or not v for v in (key, process_id, operation)) or operation not in OPERATIONS:
            return 400, {'error': 'invalid_operation'}
        with self.connect() as db:
            registered = db.execute('SELECT * FROM cases WHERE key=?', (key,)).fetchone()
        if not registered:
            return 409, {'error': 'case_not_registered'}
        if registered['process_id'] != process_id:
            return 409, {'error': 'process_binding_mismatch'}
        case = json.loads(registered['body'])
        started_ns = self.clock_ns()
        with self.connect() as db:
            old = db.execute('SELECT body FROM receipts WHERE key=? AND operation=?', (key, operation)).fetchone()
        if not old and operation != 'select':
            self.sleep(case['quotes'][operation[-1]]['delay_ms'] / 1000)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT body FROM receipts WHERE key=? AND operation=?', (key, operation)).fetchone()
            if old:
                db.execute('INSERT INTO audit(key,operation,event) VALUES(?,?,?)', (key, operation, 'replay'))
                return 200, {**json.loads(old['body']), 'replayed': True}
            receipt = {'case_key': key, 'process_id': process_id, 'operation': operation,
                       'input_sha256': registered['fingerprint'], 'started_ns': started_ns}
            if operation != 'select':
                schedule = case['quotes'][operation[-1]]
                receipt.update(outcome=schedule['outcome'], quote=schedule['quote'])
            else:
                saved = {r['operation']: json.loads(r['body']) for r in
                         db.execute('SELECT operation,body FROM receipts WHERE key=?', (key,))}
                if set(saved) != set(OPERATIONS[:-1]):
                    return 409, {'error': 'quotes_incomplete'}
                quotes = {}
                for route in ROUTES:
                    op = 'quote_' + route
                    r, expected = saved[op], case['quotes'][route]
                    wanted = {'case_key': key, 'process_id': process_id, 'operation': op,
                              'input_sha256': registered['fingerprint'],
                              'outcome': expected['outcome'], 'quote': expected['quote']}
                    if any(r.get(k) != v for k, v in wanted.items()):
                        return 409, {'error': 'quote_receipt_binding_mismatch'}
                    quotes[route] = r['quote'] if r['outcome'] == 'quote' else None
                decision_ns = self.clock_ns()
                if decision_ns < registered['registered_ns']:
                    return 409, {'error': 'registration_clock_changed'}
                decision = select_quote(case['payment'], quotes, (decision_ns - registered['registered_ns']) // 1000000,
                                        minimum_valid_quotes=case['minimum_valid_quotes'])
                receipt.update(decision)
                receipt['decision_ns'] = decision_ns
                if decision['outcome'] == 'settled':
                    p = case['payment']
                    db.execute('INSERT INTO effects VALUES(?,?,?,?,?,?,?,?)',
                               (key, p['payment_id'], p['beneficiary'], p['target_amount_minor'], p['source_currency'],
                                p['target_currency'], decision['selected_route'], decision['source_total_minor']))
            receipt['finished_ns'] = self.clock_ns()
            db.execute('INSERT INTO receipts VALUES(?,?,?)', (key, operation, canonical(receipt)))
            db.execute('INSERT INTO audit(key,operation,event) VALUES(?,?,?)', (key, operation, receipt['outcome']))
            return 200, {**receipt, 'replayed': False}


def main():
    import argparse
    import os
    from services.workflow_checks import make_server
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=28181)
    parser.add_argument('--database', required=True)
    args = parser.parse_args()
    server = make_server(args.host, args.port, QuoteStore(args.database),
        os.environ['PAY05_WORKER_TOKEN'], os.environ['PAY05_REGISTRATION_TOKEN'], service_name='pay05-synthetic-quotes')
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__': main()
