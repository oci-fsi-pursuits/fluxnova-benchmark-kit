"""W03 immutable synthetic policy fixture; separate from the deployed verifier.

The trusted harness registers original inputs and a process binding. An agent
cannot authorize payment with variables. Required approval is read from actual
engine task history and its authenticated completion actor, not an identity field.
This deterministic research fixture makes no production-policy or model claim.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import urllib.parse

DEFINITIONS={'all_review':'bench_w03_all_review','policy_routing':'bench_w03_policy_routing'}
TOPIC='bench-w03-routing'
PAYMENT_FIELDS={'payment_id','amount_minor','limit_minor','beneficiary','authorized',
                'sanctions_match','high_risk','stale_market','checks_known'}


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def fingerprint(value):return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate_payment(payment):
    if not isinstance(payment,dict) or set(payment)!=PAYMENT_FIELDS:raise ValueError('payment_fields')
    for key in ('payment_id','beneficiary'):
        if not isinstance(payment[key],str) or not 1<=len(payment[key])<=128:raise ValueError('payment_identity')
    if type(payment['amount_minor']) is not int or payment['amount_minor']<=0:raise ValueError('amount')
    if type(payment['limit_minor']) is not int or payment['limit_minor']!=1000:raise ValueError('fixed_policy_limit')
    for key in ('authorized','sanctions_match','high_risk','stale_market','checks_known'):
        if type(payment[key]) is not bool:raise ValueError('payment_boolean')


def policy(payment):
    validate_payment(payment)
    if not payment['authorized'] or payment['sanctions_match']:return 'rejected'
    if not payment['checks_known'] or payment['stale_market']:return 'blocked'
    if payment['amount_minor']>payment['limit_minor'] or payment['high_risk']:return 'review_required'
    return 'eligible'


def snapshot(payment,key):
    validate_payment(payment)
    return {'studyCaseKey':key,'originalPaymentSha256':fingerprint(payment),
            **{'payment_'+k:v for k,v in payment.items()}}


class RoutingStore:
    def __init__(self,path,engine):
        self.path=str(path);self.engine=engine
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS cases(key TEXT PRIMARY KEY,process_id TEXT UNIQUE NOT NULL,
                arm TEXT NOT NULL,payment TEXT NOT NULL,reviewer TEXT NOT NULL,input_sha256 TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS receipts(key TEXT NOT NULL,operation TEXT NOT NULL,body TEXT NOT NULL,
                PRIMARY KEY(key,operation));
              CREATE TABLE IF NOT EXISTS effects(key TEXT PRIMARY KEY,payment_id TEXT NOT NULL,
                amount_minor INTEGER NOT NULL,beneficiary TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,key TEXT NOT NULL,event TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15)
        try:
            with db:yield db
        finally:db.close()

    def register(self,key,pid,arm,payment,reviewer):
        validate_payment(payment)
        if arm not in DEFINITIONS or any(not isinstance(v,str) or not 1<=len(v)<=160 for v in (key,pid,reviewer)):
            raise ValueError('invalid_binding')
        wanted=(pid,arm,canonical(payment),reviewer,fingerprint(payment))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT process_id,arm,payment,reviewer,input_sha256 FROM cases WHERE key=?',(key,)).fetchone()
            if old and old!=wanted:raise ValueError('immutable_binding_conflict')
            if not old:db.execute('INSERT INTO cases VALUES(?,?,?,?,?,?)',(key,*wanted))

    def registered(self,key,pid):
        with self.connect() as db:
            row=db.execute('SELECT process_id,arm,payment,reviewer,input_sha256 FROM cases WHERE key=?',(key,)).fetchone()
        if not row or row[0]!=pid:raise ValueError('process_binding_mismatch')
        return {'process_id':row[0],'arm':row[1],'payment':json.loads(row[2]),'reviewer':row[3],'input_sha256':row[4]}

    def state(self,key):
        with self.connect() as db:
            receipts={op:json.loads(body) for op,body in db.execute('SELECT operation,body FROM receipts WHERE key=?',(key,))}
            effects=[dict(payment_id=p,amount_minor=a,beneficiary=b) for p,a,b in
                db.execute('SELECT payment_id,amount_minor,beneficiary FROM effects WHERE key=?',(key,))]
            audit=[r[0] for r in db.execute('SELECT event FROM audit WHERE key=? ORDER BY id',(key,))]
        return {'receipts':receipts,'effects':effects,'audit':audit}

    def history(self,key,record):
        pid=record['process_id'];query=urllib.parse.urlencode({'processInstanceId':pid})
        instance=self.engine.request('GET','/history/process-instance/'+pid)
        if instance.get('businessKey')!=key or instance.get('processDefinitionKey')!=DEFINITIONS[record['arm']]:
            raise ValueError('process_identity_mismatch')
        raw=self.engine.request('GET','/history/variable-instance?'+query)
        values={}
        for row in raw:
            if row.get('taskId'):continue
            if row['name'] in values:raise ValueError('duplicate_history_variable')
            values[row['name']]=row.get('value')
        for name,expected in snapshot(record['payment'],key).items():
            if values.get(name)!=expected or type(values.get(name)) is not type(expected):raise ValueError('original_payment_changed')
        tasks=self.engine.request('GET','/history/task?'+query)
        return instance,values,tasks,query

    def execute(self,key,pid,operation):
        if operation not in ('route','settle'):raise ValueError('fixed_operation_only')
        record=self.registered(key,pid);payment=record['payment']
        _,values,tasks,query=self.history(key,record)
        route=policy(payment);requires_review=record['arm']=='all_review' or route=='review_required'
        review=None
        if operation=='settle':
            route_receipt=self.state(key)['receipts'].get('route')
            if not route_receipt or route_receipt['outcome']!=route:raise ValueError('missing_original_policy_receipt')
            reviews=[t for t in tasks if t.get('taskDefinitionKey')=='payment_review']
            if len(reviews)!=int(requires_review):raise ValueError('required_review_count_mismatch')
            if requires_review:
                if not reviews[0].get('endTime'):raise ValueError('review_not_completed')
                ops=self.engine.request('GET','/history/user-operation?'+query)
                completions=[op for op in ops if op.get('taskId')==reviews[0]['id'] and op.get('operationType')=='Complete']
                if not completions or any(op.get('userId')!=record['reviewer'] for op in completions):
                    raise ValueError('review_actor_mismatch')
                decision=values.get('reviewerDecision')
                if decision not in ('approve','reject'):raise ValueError('missing_review_decision')
                if values.get('reviewedPaymentSha256')!=record['input_sha256']:raise ValueError('reviewed_intent_mismatch')
                review={'task_id':reviews[0]['id'],'actor':record['reviewer'],'decision':decision,
                        'start_time':reviews[0].get('startTime'),'end_time':reviews[0]['endTime'],
                        'duration_ms':reviews[0].get('duration')}
            outcome=(route if route in ('rejected','blocked') else
                     'review_rejected' if review and review['decision']=='reject' else 'settled')
        else:outcome=route
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT body FROM receipts WHERE key=? AND operation=?',(key,operation)).fetchone()
            if previous:
                db.execute('INSERT INTO audit(key,event) VALUES(?,?)',(key,'replay_'+operation))
                return {**json.loads(previous[0]),'replayed':True}
            if operation=='settle' and outcome=='settled':
                db.execute('INSERT INTO effects VALUES(?,?,?,?)',(key,payment['payment_id'],payment['amount_minor'],payment['beneficiary']))
            receipt={'case_key':key,'process_id':pid,'operation':operation,'input_sha256':record['input_sha256'],
                'outcome':outcome,'requires_review':requires_review,'review':review,'recorded_utc_s':time.time()}
            db.execute('INSERT INTO receipts VALUES(?,?,?)',(key,operation,canonical(receipt)))
            db.execute('INSERT INTO audit(key,event) VALUES(?,?)',(key,'receipt_'+operation))
            return {**receipt,'replayed':False}
