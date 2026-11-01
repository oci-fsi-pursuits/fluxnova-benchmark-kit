"""Validate sealed fault control without importing the live collector/injector."""
from datetime import datetime
import hashlib,json,math
from pathlib import Path

STUDY='FNV-06-TIMEOUT-CAPTURE'
CELL={'id':'C01','kind':'message','population':10}
LOCK_QUERY="SELECT l.pid,a.usename,l.mode,l.granted FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid WHERE l.locktype='relation' AND l.relation=%s ORDER BY l.pid,l.mode,l.granted"

def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def epoch(s):
    t=datetime.fromisoformat(s);assert t.tzinfo is not None;return t.timestamp()*1000
def finite(x):return type(x) in (int,float) and math.isfinite(x)

def check_protocol(p):
    assert p['schema_version']==1 and p['study_id']==STUDY and p['underlying_study']=='FNV-06-REPEAT-QUAL'
    assert p['cell']==CELL and p['warmup_observation']=='C01W' and p['fault_observation']=='C01M'
    assert (p['warmup_recipients'],p['test_recipients'],p['message_clients'],p['request_timeout_s'],p['recipient_drain_s'])==(100,10,4,30,120)
    assert p['fault']=={'table':'fluxnova.act_ru_event_subscr','mode':'SHARE','release_after_scheduled_dispatch_s':36,
        'maximum_release_error_s':1,'minimum_pre_dispatch_lock_lead_s':.1,'maximum_pre_dispatch_lock_lead_s':3,
        'idle_transaction_timeout_s':45}
    assert p['expected_timeout_request_indices']==[0,1,2,3] and p['expected_successful_test_requests']==6
    assert p['expected_workflow_completions']==110 and p['expected_waiting_controls']==4 and p['expected_job_exit_code']==1

def verify_control(control,cell):
    control=Path(control).resolve();cell=Path(cell).resolve();seal=read(control/'checksums.json')
    assert seal['algorithm']=='sha256'
    names={p.relative_to(control).as_posix() for p in control.rglob('*') if p.is_file() and p.name!='checksums.json'}
    assert names==set(seal['files'])=={'operator.py','request.json','input.json','receipt.json','samples.ndjson','ready.json'}
    for name,digest in seal['files'].items():
        p=control/name;assert p.resolve().is_relative_to(control) and not p.is_symlink() and sha(p)==digest
    identity=read(cell/'identity.json');manifest=read(cell/'manifest.json');source=read(cell/'source-manifest.json')
    assert identity['qualification_protocol']=='repeat' and identity['qualification_cell']==CELL
    assert identity['qualification_phase']=='timeout_capture' and identity['target_id']!=identity['source_id']
    assert identity['source_commit']==manifest['source_commit']==source['commit']
    protocol_path=cell/'inputs/workloads/fnv06/timeout-capture.json';protocol=read(protocol_path);check_protocol(protocol)
    assert sha(protocol_path)==source['files']['workloads/fnv06/timeout-capture.json']==identity['fault_protocol_sha256']
    assert sha(control/'operator.py')==identity['fault_operator_sha256']
    proof=read(control/'request.json');receipt=read(control/'receipt.json');ready=read(control/'ready.json')
    assert proof['operator_sha256']==identity['fault_operator_sha256'] and proof['source_commit']==identity['source_commit']
    target=proof['target'];run=identity['run_id'];role='fnv06_'+run[:12]
    assert target['id']==identity['target_id'] and target['lifecycle_state']=='ACTIVE' and target['instance_count']==1
    assert target['display_name']=='fluxnova-fnv06-'+run[:12] and target['source']['source_type']=='NONE'
    for k,v in {'managed_by':'fluxnova-benchmarks','fnv06_run_id':run,'temporary_comparison':'true','comparison_lane':'fnv06'}.items():
        assert target['freeform_tags'][k]==v
    assert ready['run_id']==receipt['run_id']==run and ready['source_commit']==receipt['source_commit']==identity['source_commit']
    assert receipt['status']=='released' and receipt['target_id']==identity['target_id'] and receipt['source_id']==identity['source_id']
    assert receipt['case']=='C01M' and receipt['table']=='fluxnova.act_ru_event_subscr' and receipt['mode']=='SHARE'
    assert receipt['database']=='postgres' and receipt['tls'] is True and receipt['database_user']==identity['admin_username']
    assert receipt['host']==identity['connection']['primary_db_endpoint']['fqdn']!=identity['source_db_host']
    assert receipt['table_owner']==role and receipt['schema_owner']==identity['admin_username']
    assert type(receipt['table_oid']) is int and receipt['table_oid']>0 and type(receipt['backend_pid']) is int and receipt['backend_pid']>0
    assert receipt['idle_transaction_timeout_s']==45 and receipt['transaction_ended'] is True and receipt['lock_absent_after_commit'] is True
    fixture=read(control/'input.json');assert sha(control/'input.json')==sha(cell/'observations/C01M/driver-input.json')==receipt['driver_input_sha256']
    assert fixture['study_id']=='FNV-06-REPEAT-QUAL' and fixture['run_id']==run and fixture['observation_id']=='C01M'
    assert fixture['population']==10 and fixture['kind']=='message' and fixture['vus']==4 and len(fixture['requests'])==10
    release=fixture['release_epoch_ms'];assert receipt['release_epoch_ms']==release
    acquired=receipt['lock_acquired_epoch_ms'];ended=receipt['lock_released_epoch_ms']
    assert all(finite(receipt[k]) for k in ('lock_acquired_epoch_ms','lock_released_epoch_ms','lock_acquired_monotonic_s','lock_released_monotonic_s'))
    assert epoch(identity['prepared_at'])<=epoch(proof['issued_at'])<=epoch(ready['at'])<=acquired<ended<epoch(identity['stop_before_utc'])
    assert acquired-epoch(proof['issued_at'])<120000 and 100<=release-acquired<=3000
    assert abs(ended-release-36000)<=1000
    assert abs((ended-acquired)/1000-(receipt['lock_released_monotonic_s']-receipt['lock_acquired_monotonic_s']))<=.1
    rows=[json.loads(l) for l in (control/'samples.ndjson').read_text().splitlines()];assert len(rows)>=20
    previous=None;blocked=False;origin=acquired/1000-receipt['lock_acquired_monotonic_s']
    for row in rows:
        assert row['query']==LOCK_QUERY and row['table_oid']==receipt['table_oid']
        assert finite(row['epoch_ms']) and finite(row['monotonic_s']) and acquired<=row['epoch_ms']<ended
        assert abs(row['epoch_ms']/1000-row['monotonic_s']-origin)<=.1
        if previous is not None:assert 0<row['monotonic_s']-previous<=2
        previous=row['monotonic_s']
        locks=row['rows'];assert all(len(v)==4 and type(v[0]) is int and type(v[3]) is bool for v in locks)
        assert [receipt['backend_pid'],identity['admin_username'],'ShareLock',True] in locks
        if release+1000<=row['epoch_ms']<release+29000:
            waiters={v[0] for v in locks if v[1]==role and v[2]=='RowExclusiveLock' and v[3] is False}
            blocked=blocked or len(waiters)>=4
    assert blocked,'No independently retained database lock contention'
    assert not any(r[0]==receipt['backend_pid'] and r[2]=='ShareLock' and r[3] is True for r in receipt['after_release_rows'])
    return {'study_id':STUDY,'status':'verified','driver_input_sha256':receipt['driver_input_sha256'],
        'control_checksums_sha256':sha(control/'checksums.json'),'operator_sha256':identity['fault_operator_sha256'],
        'four_owned_waiters_observed':True,'fixed_request_timeout_s':30,'scheduled_lock_release_s':36,
        'actual_lock_release_elapsed_s':(ended-release)/1000,'samples':len(rows),'transaction_released':True,
        'scope':'Deliberate lock in the fresh owned trial database; not a performance observation.'}
