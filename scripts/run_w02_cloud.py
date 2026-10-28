#!/usr/bin/env python3
"""Run W02 in an owned, isolated Linux controller stack and a fresh PostgreSQL.

Stage this file as runtime.py beside identity.json, fixed_db_forward.py and a
frozen source/ checkout. Provisioning/retirement must separately verify the OCI
resource ID and W02 ownership tags. No original application service is changed.
"""
import base64
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
from datetime import datetime, timezone

HERE=Path(__file__).resolve().parent
if not (HERE/'identity.json').is_file():
    raise SystemExit('Run only from a prepared private controller stage containing identity.json.')
meta=json.loads((HERE/'identity.json').read_text())
nonce=meta['run_id']
assert re.fullmatch('[0-9a-f]{32}',nonce) and HERE==Path(meta['stage'])
assert HERE.parent==Path('/opt/fluxnova-bench/workspace')
assert meta['target_id']!=meta['source_id']
assert meta.get('study_id','w02') in ('w02','w02_m2')
sys.path.insert(0,str(HERE/'source'))
from benchlib.util import ROOT
assert ROOT==HERE/'source'
from benchlib.analysis import percentile
from benchlib.checkpoint import postgres_fingerprint, sqlite_fingerprint, sqlite_copy, restore_listing
from benchlib.evidence import calibrate_generator, linux_host_sample
from benchlib.fluxnova import FluxnovaClient
from benchlib.runner import extract_event, normalize_event
from benchlib.w02_cloud import assess_cloud, measurement_plan, require_matched_controls
from benchlib.w02_mixed_cloud import resolve_cloud_corpus, mixed_measurement_plan, QUALIFICATION_LANES
from scripts.run_w02_functional import require_outcome
from services.workflow_checks import fingerprint
from services.worker import Client
from psycopg import sql
import psycopg

network='w02-'+nonce[:12]
label='w02_id='+nonce
names={k:network+'-'+k for k in ('engine','backend','worker')}
dbhost=meta['connection']['primary_db_endpoint']['fqdn']
assert dbhost!=meta['source_db_host'] and dbhost.endswith('.postgresql.us-phoenix-1.oci.oraclecloud.com')
ca=HERE/'db-ca.pem'
dbuser='w02_'+nonce[:12]
checkpoint=HERE/'checkpoint'


def utc():return datetime.now(timezone.utc).isoformat()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):
    path=Path(path)
    if not path.is_absolute():path=HERE/path
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');path.chmod(0o600)
def read(path):return json.loads((HERE/path).read_text())
def credentials():return read('credentials.local.json')
def bound():
    assert datetime.now(timezone.utc)<datetime.fromisoformat(meta['stop_before_utc'])
    assert not (HERE/'cancel').exists()
def cmd(args,env=None,timeout=180):
    r=subprocess.run(args,env=env,capture_output=True,timeout=timeout)
    if r.returncode:
        p=HERE/('command-'+str(time.time_ns())+'.stderr');p.write_bytes(r.stderr);p.chmod(0o600)
        raise RuntimeError('private_command_failed:'+args[0])
    return r.stdout
def docker(*args,**kwargs):return cmd(['docker',*args],**kwargs)
def inspect(name):
    value=json.loads(docker('inspect',name))[0]
    assert value['Config']['Labels']['w02_id']==nonce
    return value
def envopts(values):
    env=os.environ.copy();env.update({k:str(v) for k,v in values.items()})
    return env,[item for key in values for item in ('--env',key)]
def dbconnect(admin=False):
    password=credentials()['database'];user=dbuser
    if admin:
        import oci
        client=oci.secrets.SecretsClient({'region':'us-phoenix-1'},signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner(),timeout=(10,30))
        password=base64.b64decode(client.get_secret_bundle(meta['admin_secret_id'],version_number=int(meta['admin_secret_version'])).data.secret_bundle_content.content).decode()
        user=meta['admin_username']
    return psycopg.connect(host=dbhost,port=5432,dbname='postgres',user=user,password=password,
             sslmode='verify-full',sslrootcert=str(ca),connect_timeout=10,autocommit=True)
def addresses():
    return {k:inspect(v)['NetworkSettings']['Networks'][network]['IPAddress'] for k,v in names.items() if k!='worker'}
def clients():
    address=addresses();cred=credentials()
    return (FluxnovaClient('http://'+address['engine']+':8080/engine-rest','w02bench',cred['admin']),
            Client('http://'+address['backend']+':28181','Bearer '+cred['worker']),
            Client('http://'+address['backend']+':28181','Bearer '+cred['registrar']))
def queues():
    engine=clients()[0]
    return {k:engine.request('GET',v)['count'] for k,v in {'active_instances':'/process-instance/count',
                  'external_tasks':'/external-task/count','incidents':'/incident/count'}.items()}
def ready():
    deadline=time.monotonic()+300
    while True:
        bound()
        try:
            engine,support,_=clients();version=engine.readiness();support.request('GET','/health')
            assert version['version']=='3.0.1';return version
        except (OSError,RuntimeError):
            if time.monotonic()>deadline:raise
            time.sleep(2)
def source_guard():
    expected=read('source-manifest.json')
    assert all(sha(HERE/'source'/name)==digest for name,digest in expected['files'].items())
    assert sha(HERE/'runtime.py')==expected['runtime_sha256']
    if meta.get('study_id')=='w02_m2':
        assert sha(HERE/'mixed-corpus.json')==expected['mixed_corpus_sha256']==meta['mixed_corpus_sha256']
        assert read('mixed-corpus.json')==resolve_cloud_corpus(read('source/workloads/w02/sensitivity-protocol.json'),read('source/workloads/w02/cases.json'))
    return expected

def mixed_corpus():
    return read('mixed-corpus.json') if meta.get('study_id')=='w02_m2' else None


def setup():
    bound();source_guard();assert not (HERE/'setup.json').exists()
    assert not (HERE/'credentials.local.json').exists()
    save('credentials.local.json',{k:secrets.token_urlsafe(32) for k in ('admin','database','worker','registrar')})
    ca.write_text(meta['connection']['ca_certificate']);ca.chmod(0o644)
    for image in meta['images'].values():
        assert re.search(r'@sha256:[0-9a-f]{64}$',image)
        docker('image','inspect',image)
    with dbconnect(True) as db:
        assert db.execute("SELECT count(*) FROM pg_namespace WHERE nspname='fluxnova'").fetchone()[0]==0
        db.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Identifier(dbuser),sql.Literal(credentials()['database'])))
        db.execute('CREATE SCHEMA fluxnova')
        db.execute(sql.SQL('GRANT USAGE,CREATE ON SCHEMA fluxnova TO {}').format(sql.Identifier(dbuser)))
        db.execute(sql.SQL('ALTER ROLE {} SET search_path TO fluxnova,public').format(sql.Identifier(dbuser)))
    docker('network','create','--internal','--label',label,network)
    n=json.loads(docker('network','inspect',network))[0];assert n['Internal'] and n['Labels']['w02_id']==nonce
    gateway=n['IPAM']['Config'][0]['Gateway']
    rule=['-i','br-'+n['Id'][:12],'-s',n['IPAM']['Config'][0]['Subnet'],'-d',gateway,'-p','tcp','--dport','5432','-m','comment','--comment',network,'-j','ACCEPT']
    assert subprocess.run(['iptables','-C','INPUT',*rule],capture_output=True).returncode==1
    before=cmd(['iptables','-S','INPUT']).decode()
    save('owned-input-rule.json',{'rule':rule,'before':before,'network_id':n['Id']})
    cmd(['iptables','-I','INPUT','1',*rule])
    save('fixed-forward-binding.json',{'run_id':nonce,'target_id':meta['target_id'],'host':dbhost,'gateway':gateway})
    argv=[sys.executable,str(HERE/'fixed_db_forward.py')]
    with (HERE/'fixed-forward.log').open('xb') as log:
        p=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    save('forward.json',{'pid':p.pid,'command':argv,'source_sha256':sha(HERE/'fixed_db_forward.py')})
    deadline=time.monotonic()+20
    while True:
        assert p.poll() is None
        try:
            with socket.create_connection((gateway,5432),timeout=1):break
        except OSError:
            assert time.monotonic()<deadline;time.sleep(.5)
    data=HERE/'backend';data.mkdir(mode=0o700);os.chown(data,65532,65532)
    cred=credentials()
    common=['--label',label,'--network',network,'--cap-drop','ALL','--security-opt','no-new-privileges']
    mount=['--mount','type=bind,src='+str(HERE/'source')+',dst=/source,readonly','--workdir','/source']
    env,options=envopts({'W02_WORKER_TOKEN':cred['worker'],'W02_REGISTRATION_TOKEN':cred['registrar'],'PYTHONDONTWRITEBYTECODE':'1'})
    docker('run','-d','--name',names['backend'],*common,'--network-alias','backend','--user','65532:65532','--read-only',
           '--memory','256m','--cpus','.25','--pids-limit','64',*mount,'--mount','type=bind,src='+str(data)+',dst=/data',*options,
           meta['images']['services'],'python','-m','services.workflow_checks','--host','0.0.0.0','--database','/data/study.sqlite',env=env)
    env,options=envopts({'SPRING_DATASOURCE_URL':'jdbc:postgresql://'+dbhost+':5432/postgres?currentSchema=fluxnova&sslmode=verify-full&sslrootcert=/run/db-ca.pem',
        'SPRING_DATASOURCE_USERNAME':dbuser,'SPRING_DATASOURCE_PASSWORD':cred['database'],'SPRING_DATASOURCE_DRIVER_CLASS_NAME':'org.postgresql.Driver',
        'SPRING_DATASOURCE_HIKARI_MAXIMUM_POOL_SIZE':'10','FLUXNOVA_BPM_RUN_AUTH_ENABLED':'true','FLUXNOVA_BPM_AUTHORIZATION_ENABLED':'true',
        'FLUXNOVA_BPM_HISTORY_LEVEL':'full','FLUXNOVA_BPM_ADMIN_USER_ID':'w02bench','FLUXNOVA_BPM_ADMIN_USER_PASSWORD':cred['admin'],
        'JAVA_OPTS':'-Xms512m -Xmx2g','FLUXNOVA_BPM_RUN_EXAMPLE_ENABLED':'false'})
    docker('run','-d','--name',names['engine'],*common,'--network-alias','engine','--add-host',dbhost+':'+gateway,
           '--memory','4g','--cpus','2','--pids-limit','256','--mount','type=bind,src='+str(ca)+',dst=/run/db-ca.pem,readonly',*options,meta['images']['engine'],env=env)
    version=ready()
    engine=clients()[0];save('deployment.json',engine.deploy([HERE/'source/workloads/w02'/ (arm+'.bpmn') for arm in ('sequential','parallel')]))
    env,options=envopts({'BENCH_USERNAME':'w02bench','BENCH_PASSWORD':cred['admin'],'W02_WORKER_TOKEN':cred['worker'],'PYTHONDONTWRITEBYTECODE':'1'})
    docker('create','--name',names['worker'],*common,'--user','65532:65532','--read-only','--memory','256m','--cpus','.5',
           '--pids-limit','64',*mount,*options,meta['images']['services'],'python','-m','services.workflow_checks_worker',
           '--engine-url','http://engine:8080/engine-rest','--support-url','http://backend:28181','--slots','2',env=env)
    return finish_setup()


def finish_setup():
    bound();source_guard();assert not (HERE/'setup.json').exists()
    version=ready()
    probe="import socket,json;r=[]\nfor h,p in TARGETS:\n s=socket.socket();s.settimeout(1);v=s.connect_ex((h,p));s.close();assert v!=0;r.append({'port':p,'blocked':True})\nprint(json.dumps(r))"
    # Resolve outside the isolated namespace: a DNS failure is not an egress test.
    targets=[(socket.gethostbyname(host),port) for host,port in meta['forbidden_endpoints']]
    save('resolved-egress-probes.json',targets)
    blocked=json.loads(docker('exec',names['backend'],'python','-c',probe.replace('TARGETS',repr(targets))))
    with dbconnect() as db:
        sessions=db.execute('SELECT s.ssl,s.version FROM pg_stat_activity a JOIN pg_stat_ssl s USING(pid) WHERE a.usename=%s',(dbuser,)).fetchall()
        assert len(sessions)>1 and all(x[0] for x in sessions)
        server_version=db.execute('SELECT version()').fetchone()[0]
    assert not any(queues().values())
    result={'ready':True,'version':version,'database_version':server_version,'jdbc_tls_verify_full':True,'database_tls':sessions,
            'blocked_original_endpoints':blocked,'internal_network':True,'source_revision':meta['source_commit'],
            'instrumentation':'SQL and Docker telemetry; Java agent disabled in both W02 arms',
            'topology':'Colocated existing controller and fresh one-node OCI PostgreSQL; different from four-host P01'}
    save('setup.json',result);capture();return result


def stop_workers():
    x=inspect(names['worker'])
    if x['State']['Running']:docker('stop','--time','35',names['worker'],timeout=45)
    assert not inspect(names['worker'])['State']['Running']
def start_workers():
    assert not inspect(names['worker'])['State']['Running'];docker('start',names['worker'])
def pause():
    assert not any(queues().values());stop_workers()
    for role in ('engine','backend'):
        inspect(names[role]);docker('stop','--time','30',names[role],timeout=45)
def resume():
    for path in (HERE/'backend').glob('study.sqlite*'):
        assert path.is_file() and not path.is_symlink();os.chown(path,65532,65532)
    for role in ('backend','engine'):inspect(names[role]);docker('start',names[role])
    ready()
def pgtool(args,folder):
    env,options=envopts({'PGHOST':dbhost,'PGPORT':'5432','PGUSER':dbuser,'PGPASSWORD':credentials()['database'],
                         'PGDATABASE':'postgres','PGSSLMODE':'verify-full','PGSSLROOTCERT':'/run/db-ca.pem'})
    return docker('run','--rm','--label',label,'--network','host','--mount','type=bind,src='+str(ca)+',dst=/run/db-ca.pem,readonly',
         '--mount','type=bind,src='+str(folder)+',dst=/checkpoint',*options,meta['images']['postgres'],*args,env=env,timeout=600)
def logical():
    with dbconnect() as db:p=postgres_fingerprint(db,'fluxnova')
    return {'engine':p,'backend':sqlite_fingerprint(HERE/'backend/study.sqlite')}
def capture():
    pause();checkpoint.mkdir(mode=0o700)
    value=logical()
    pgtool(['pg_dump','--schema=fluxnova','--strict-names','--format=custom','--no-owner','--no-acl','--file=/checkpoint/engine.dump'],checkpoint)
    sqlite_copy(HERE/'backend/study.sqlite',checkpoint/'study.sqlite')
    assert logical()==value
    save(checkpoint/'manifest.json',{'logical':value,'files':{name:sha(checkpoint/name) for name in ('engine.dump','study.sqlite')}})
    resume()
def reset(folder):
    pause();frozen=read('checkpoint/manifest.json')
    assert all(sha(checkpoint/name)==value for name,value in frozen['files'].items())
    # Preserve the previous state before replacing this exact owned schema.
    rollback=folder/'previous-state';rollback.mkdir(mode=0o700)
    pgtool(['pg_dump','--schema=fluxnova','--strict-names','--format=custom','--no-owner','--no-acl','--file=/checkpoint/engine.dump'],rollback)
    sqlite_copy(HERE/'backend/study.sqlite',rollback/'study.sqlite')
    save(rollback/'logical.json',logical())
    listing=pgtool(['pg_restore','--list','/checkpoint/engine.dump'],checkpoint).decode()
    (checkpoint/'restore.list').write_text(restore_listing(listing,'fluxnova'))
    pgtool(['pg_restore','--dbname=postgres','--clean','--if-exists','--single-transaction','--no-owner','--no-acl',
            '--exit-on-error','--use-list=/checkpoint/restore.list','/checkpoint/engine.dump'],checkpoint)
    sqlite_copy(checkpoint/'study.sqlite',HERE/'backend/study.sqlite',replace=True)
    actual=logical();assert actual==frozen['logical']
    digest=lambda v:hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest()
    receipt={'verified':True,'expected_fingerprint':digest(frozen['logical']),'restored_fingerprint':digest(actual),
             'target_id':meta['target_id'],'schema':'fluxnova','verified_before_resuming_writers':True,'at':utc()}
    save(folder/'reset.json',receipt);resume();assert not any(queues().values());return receipt


def functional():
    folder=HERE/'functional';folder.mkdir(mode=0o700);reset(folder)
    engine,support,registrar=clients();rows=[];approved={};replays=[]
    cases=json.loads((HERE/'source/workloads/w02/cases.json').read_text())['cases']
    try:
        for case in cases:
            for arm in ('sequential','parallel'):
                bound();key=network+'-'+arm+'-'+case['case_id']
                pid=engine.start('bench_w02_'+arm,key,{'studyCaseKey':{'value':key,'type':'String'}})['id']
                registrar.request('POST','/cases/register',{'case_key':key,'process_id':pid,'case':case})
                try:
                    support.request('POST','/tools/execute',{'case_key':key,'process_id':pid,'operation':'settle'})
                    raise AssertionError('premature_settlement_accepted')
                except urllib.error.HTTPError as error:assert error.code==409
                start_workers();observed=engine.observe(pid,timeout_s=30,poll_s=.1);stop_workers()
                state=registrar.request('GET','/state/'+urllib.parse.quote(key,safe=''))
                activities=engine.request('GET','/history/activity-instance?processInstanceId='+pid)
                save(folder/(arm+'-'+case['case_id']+'.json'),{'original':case,'observed':observed,'activities':activities,'backend':state})
                require_outcome(case,state,observed,activities)
                kyc,risk=state['receipts']['kyc'],state['receipts']['risk']
                overlap=min(kyc['finished_ns'],risk['finished_ns'])-max(kyc['started_ns'],risk['started_ns'])
                if arm=='sequential':assert risk['started_ns']>=kyc['finished_ns']
                if arm=='parallel' and case['case_id'] in ('approve','concurrent_completion'):assert overlap>0
                for op,schedule in case['schedule'].items():
                    event={'before_once':'failure_before_receipt','after_once':'response_lost_after_receipt'}.get(schedule['fault'])
                    if event:assert any(a['operation']==op and a['event']==event for a in state['audit'])
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                    repeated=list(executor.map(lambda op:support.request('POST','/tools/execute',{'case_key':key,'process_id':pid,'operation':op}),['kyc','risk','settle']*2))
                assert all(r['replayed'] for r in repeated)
                after=registrar.request('GET','/state/'+key)
                assert after['effects']==state['effects'] and after['receipts']==state['receipts']
                save(folder/(arm+'-'+case['case_id']+'-replay.json'),after)
                rows.append({'case_id':case['case_id'],'arm':arm,'status':'passed','effects':len(state['effects']),
                             'checks_overlap':overlap>0,'replay_calls':len(repeated),'premature_effect_refused':True,'input_sha256':fingerprint(case)})
                save(folder/'progress.json',rows)
                if case['case_id']=='approve':approved[arm]=(key,state)
        for arm,(key,original) in approved.items():
            pid=engine.start('bench_w02_'+arm,key,{'studyCaseKey':{'value':key,'type':'String'}})['id'];start_workers()
            deadline=time.monotonic()+20
            while True:
                tasks=engine.request('GET','/external-task?processInstanceId='+pid)
                if tasks and all(t.get('retries')==0 for t in tasks):break
                assert time.monotonic()<deadline;time.sleep(.1)
            stop_workers();after=registrar.request('GET','/state/'+key)
            assert after['effects']==original['effects'] and after['receipts']==original['receipts']
            incidents=engine.request('GET','/incident?processInstanceId='+pid);assert len(incidents)==len(tasks)
            save(folder/(arm+'-cross-process-replay.json'),{'tasks':tasks,'incidents':incidents,'after':after})
            engine.request('DELETE','/process-instance/'+pid+'?skipCustomListeners=true&skipIoMappings=true')
            replays.append({'arm':arm,'status':'blocked_as_expected','incidents':len(incidents),'original_effects_unchanged':True})
        assert not any(queues().values())
        result={'status':'passed','cases':rows,'functional_cases_passed':len(rows),'expected_effects':sum(r['effects'] for r in rows),
                'replay_probes':replays,'cloud_resources_accessed':True,'native_model_calls':0,'performance_claim':None}
        save(folder/'summary.json',result);return result
    finally:stop_workers()


def sample():
    row={'monotonic_s':time.monotonic(),'at':utc(),'host':linux_host_sample()}
    stats=[json.loads(line) for line in docker('stats','--no-stream','--format','{{json .}}',*names.values(),timeout=8).decode().splitlines()]
    by_name={s['Name']:s for s in stats};containers={}
    for role,name in names.items():
        x=inspect(name);s=by_name[name]
        containers[role]={'running':x['State']['Running'],'oom_killed':x['State']['OOMKilled'],'restart_count':x['RestartCount'],
                          'cpu_percent':float(s['CPUPerc'].rstrip('%')),'memory_usage':s['MemUsage'],'pids':s['PIDs']}
    row['containers']=containers
    with dbconnect() as db:
        db.execute('SET statement_timeout=5000')
        values=db.execute('SELECT xact_commit,deadlocks FROM pg_stat_database WHERE datname=current_database()').fetchone()
        sessions=db.execute("SELECT count(*),count(*) FILTER (WHERE state='active'),count(*) FILTER (WHERE wait_event_type='Lock') FROM pg_stat_activity WHERE datname=current_database() AND usename=%s",(dbuser,)).fetchone()
        tls=db.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()[0]
        row['database']=dict(zip(('commits','deadlocks','connections','active','lock_waiters'),[*values,*sessions]))
        row['database'].update(tls=tls,server_version=db.execute('SHOW server_version').fetchone()[0])
        row['engine']={k:db.execute('SELECT count(*) FROM fluxnova.'+table).fetchone()[0] for k,table in
                         [('external_tasks','act_ru_ext_task'),('incidents','act_ru_incident'),('jobs','act_ru_job')]}
        row['engine']['active_instances']=db.execute('SELECT count(*) FROM fluxnova.act_ru_execution WHERE id_=proc_inst_id_').fetchone()[0]
    return row


def controls():
    actual={}
    for role,name in names.items():
        x=inspect(name)
        actual[role]={'image_id':x['Image'],'arguments':x['Config']['Cmd'],
                      'environment_sha256':hashlib.sha256(json.dumps(sorted(x['Config']['Env'])).encode()).hexdigest(),
                      'memory_bytes':x['HostConfig']['Memory'],'nano_cpus':x['HostConfig']['NanoCpus'],
                      'pids_limit':x['HostConfig']['PidsLimit'],'network_mode':x['HostConfig']['NetworkMode'],
                      'read_only_root':x['HostConfig']['ReadonlyRootfs'],
                      'mounts':[{k:m[k] for k in ['Type','Source','Destination','RW']} for m in x['Mounts']]}
    return {'source_manifest_sha256':sha(HERE/'source-manifest.json'),'containers':actual,
            'engine_cpus':2,'engine_memory_gib':4,'heap_gib':2,'pool_size':10,'worker_slots':2,'worker_cpus':.5,'backend_cpus':.25,
            'worker_memory_mib':256,'backend_memory_mib':256,'db_target_id':meta['target_id'],'db_nodes':1,'db_ocpu':2,'db_memory_gib':32,
            'history':'full','poll_ms':100,'instrumentation':'SQL/Docker/Linux telemetry; no Java agent','telemetry_interval_s':5,
            'k6_image':meta['images']['k6'],'generator':'unlimited container on shared controller; 16 fixed VUs',
            'case_mode':'mixed_sensitivity' if mixed_corpus() is not None else 'approved',
            'corpus_sha256':meta.get('mixed_corpus_sha256'),
            'checkpoint_sha256':sha(checkpoint/'manifest.json')}


def lane(folder,arm,rate,warmup,duration,calibration):
    bound();source_guard();folder.mkdir(mode=0o700);reset_receipt=reset(folder);start_workers()
    rows=[];stop=threading.Event()
    def collect():
        try:value=sample()
        except Exception as e:value={'monotonic_s':time.monotonic(),'at':utc(),'error':type(e).__name__}
        rows.append(value)
        with (folder/'telemetry.ndjson').open('a') as f:f.write(json.dumps(value)+'\n')
    def sampling():
        while not stop.wait(5):collect()
    collect();thread=threading.Thread(target=sampling,daemon=True);thread.start()
    # Retried preparation attempts keep unique business keys even after a reset.
    run_id=network+'-'+secrets.token_hex(6)+'-'+folder.name
    cred=credentials();address=addresses()
    values={'W02_ENGINE_URL':'http://'+address['engine']+':8080/engine-rest','W02_SUPPORT_URL':'http://'+address['backend']+':28181',
            'W02_USERNAME':'w02bench','W02_PASSWORD':cred['admin'],'W02_REGISTRATION_TOKEN':cred['registrar'],'W02_RUN_ID':run_id,
            'W02_ARM':arm,'W02_RATE':rate,'W02_DURATION_S':duration,'W02_WARMUP_S':warmup,'W02_DRAIN_S':30,'W02_VUS':16,'W02_OUTPUT_DIR':folder}
    corpus=mixed_corpus();extra_mount=[]
    if corpus is not None:
        values.update(W02_CORPUS_FILE='/run/w02-corpus.json',W02_CORPUS_SHA256=meta['mixed_corpus_sha256'])
        extra_mount=['--mount','type=bind,src='+str(HERE/'mixed-corpus.json')+',dst=/run/w02-corpus.json,readonly']
    env,options=envopts(values)
    generator=network+'-generator';argv=['docker','run','--rm','--name',generator,'--label',label,'--network','host',
            '--user','0:0','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','1g','--pids-limit','256',
            '--mount','type=bind,src='+str(HERE/'source')+',dst='+str(HERE/'source')+',readonly',
            '--mount','type=bind,src='+str(folder)+',dst='+str(folder),*extra_mount,*options,meta['images']['k6'],
            'run','--log-format','json','--no-color',str(HERE/'source/load/workflow_checks.js')]
    execution={'started_at':utc(),'started_monotonic_s':time.monotonic(),'argv_without_credentials':argv}
    baseline_controls=controls();save(folder/'controls.json',baseline_controls)
    try:
        with (folder/'k6.log').open('wb') as log:
            proc=subprocess.Popen(argv,env=env,stdout=log,stderr=subprocess.STDOUT)
            try:code=proc.wait(timeout=warmup+duration+90)
            except BaseException:
                x=inspect(generator);docker('stop','--time','10',generator,timeout=20);proc.wait(timeout=15);raise
        execution.update(returncode=code,finished_at=utc(),finished_monotonic_s=time.monotonic())
    finally:
        stop.set();thread.join(timeout=30);assert not thread.is_alive();collect();stop_workers()
    save(folder/'execution.json',execution)
    events=[];malformed=0
    with (folder/'events.ndjson').open('w') as f:
        for line in (folder/'k6.log').read_text(errors='replace').splitlines():
            try:
                event=extract_event(line)
                if event:event=normalize_event(event);events.append(event);f.write(json.dumps(event,allow_nan=False)+'\n')
            except (ValueError,TypeError):malformed+=1
    assert malformed==0
    summary=json.loads((folder/'k6-summary.json').read_text())
    if corpus is not None:assert summary['w02']['corpus_sha256']==meta['mixed_corpus_sha256']
    for key,value in {'run_id':run_id,'arm':arm,'rate':rate,'duration_s':duration,'warmup_s':warmup,'drain_s':30,'fixed_vus':16,'deadline_s':2,'poll_interval_ms':100}.items():
        assert summary['w02'][key]==value
    engine=clients()[0]
    final=queues();final['completed_instances']=engine.request('GET','/history/process-instance/count?finished=true')['count']
    with sqlite3.connect('file:'+str(HERE/'backend/study.sqlite')+'?mode=ro',uri=True) as db:
        for key,table in [('effects','effects'),('registrations','cases'),('receipts','receipts')]:final[key]=db.execute('SELECT count(*) FROM '+table).fetchone()[0]
    with dbconnect() as db:
        sql_count=db.execute("SELECT count(*) FROM fluxnova.act_hi_procinst WHERE state_='COMPLETED'").fetchone()[0]
        assert sql_count==final['completed_instances']
    save(folder/'final-state.json',final)
    result=assess_cloud(events,summary,execution,calibration,final,rows,reset_receipt,corpus=corpus)
    result['run_id']=run_id;result['folder']=folder.name
    save(folder/'assessment.json',result)
    # History and exact effect data are retained independently of k6's verdict.
    archive_state(folder)
    source_guard();require_matched_controls(baseline_controls,controls());return result


def archive_state(folder):
    target=folder/'stores';target.mkdir(mode=0o700)
    stop_workers()
    pgtool(['pg_dump','--schema=fluxnova','--strict-names','--format=custom','--no-owner','--no-acl','--file=/checkpoint/engine.dump'],target)
    sqlite_copy(HERE/'backend/study.sqlite',target/'study.sqlite')
    save(target/'files.json',{p.name:sha(p) for p in target.iterdir() if p.is_file()})


def calibrate():
    folder=HERE/'calibration';folder.mkdir(mode=0o700)
    config={'workload':{'rate':1},'execution':{'k6_binary':str(HERE/'k6')},'artifact':{'k6_image':meta['images']['k6']},
            'evidence':{'estimated_http_calls_per_workflow':40,'calibration_duration_s':15}}
    value=calibrate_generator(config,network,folder);assert value['valid'];return value


def qualify():
    assert not (HERE/'qualification.json').exists()
    if mixed_corpus() is not None:
        assert not (HERE/'mixed-design.json').exists()
        save('mixed-design.json',{'candidate_design':mixed_measurement_plan(nonce,1,mixed_corpus()),
                                 'frozen_at':utc(),'source_commit':meta['source_commit'],
                                 'corpus_sha256':meta['mixed_corpus_sha256']})
    calibration=calibrate();functional_result=functional();assert functional_result['functional_cases_passed']==20
    results=[];corpus=mixed_corpus()
    for index,settings in enumerate(QUALIFICATION_LANES,1):
        rate,arm=settings['rate'],settings['arm']
        result=lane(HERE/f'q{index}-{arm}',arm,rate,100 if corpus is not None else 30,100 if corpus is not None else 120,calibration);results.append(result)
        save('qualification-progress.json',results)
        if not result['measurement_valid']:raise RuntimeError('W02_cloud_qualification_invalid')
    eligible=[rate for rate in (.5,1) if all(r['status']=='passed' for r in results if r['offered_rate']==rate)]
    assert eligible,'Neither offered rate passed both arms'
    rate=max(eligible);plan=mixed_measurement_plan(nonce,rate,corpus) if corpus is not None else measurement_plan(nonce,rate)
    if corpus is not None:plan['corpus_sha256']=meta['mixed_corpus_sha256']
    plan.update(frozen_at=utc(),source_commit=meta['source_commit'],controls=controls(),qualification_sha256=sha(HERE/'qualification-progress.json'))
    save('measurement-plan.json',plan)
    result={'status':'passed','selected_rate':rate,'lanes':results,'measurement_plan_sha256':sha(HERE/'measurement-plan.json'),
            'performance_claim':None,'qualification_not_ranked':True}
    save('qualification.json',result);return result


def campaign():
    assert read('qualification.json')['status']=='passed' and not (HERE/'campaign-status.json').exists()
    plan=read('measurement-plan.json');assert sha(HERE/'measurement-plan.json')==read('qualification.json')['measurement_plan_sha256']
    calibration=read('calibration/generator-calibration.json');results=[];status={'status':'running','started_at':utc(),'completed_arms':0,'planned_arms':10}
    save('campaign-status.json',status)
    try:
        for pair,arms in enumerate(plan['pairs'],1):
            for order,arm in enumerate(arms,1):
                bound();require_matched_controls(plan['controls'],controls())
                name=f'p{pair}-{order}-{arm}';status['current_arm']=name;save('campaign-status.json',status)
                result=lane(HERE/name,arm,plan['rate_per_s'],plan['warmup_s'],plan['measurement_s'],calibration)
                results.append(result);save('campaign-results.json',results)
                status['completed_arms']=len(results);save('campaign-status.json',status)
                if not result['measurement_valid']:raise RuntimeError('W02_measurement_invalid')
        status['status']='passed'
    except Exception as error:
        status.update(status='stopped',error_type=type(error).__name__)
        save('campaign-failure.json',{'error_type':type(error).__name__,'traceback':traceback.format_exc()})
        raise
    finally:
        status['finished_at']=utc()
        try:status['retirement']=retire()
        except Exception as error:status['retirement_error']=type(error).__name__
        save('campaign-status.json',status)
    return status


def retire():
    # Exact labels protect both original services and unrelated containers.
    for role in ('generator','worker','engine','backend'):
        name=names.get(role,network+'-'+role)
        probe=subprocess.run(['docker','inspect',name],capture_output=True)
        if probe.returncode:continue
        inspect(name)
        if role!='generator':
            logs=docker('logs',name);p=HERE/(role+'-final.log');p.write_bytes(logs);p.chmod(0o600)
        docker('stop','--time','35',name,timeout=45)
        if subprocess.run(['docker','inspect',name],capture_output=True).returncode==0:inspect(name);docker('rm',name)
    final=HERE/'final-stores'
    if not final.exists() and (HERE/'backend/study.sqlite').exists():
        final.mkdir(mode=0o700)
        sqlite_copy(HERE/'backend/study.sqlite',final/'study.sqlite')
        try:
            pgtool(['pg_dump','--schema=fluxnova','--strict-names','--format=custom','--no-owner','--no-acl','--file=/checkpoint/engine.dump'],final)
            save(final/'files.json',{p.name:sha(p) for p in final.iterdir() if p.is_file()})
        except Exception as error:
            save(final/'export-error.json',{'error_type':type(error).__name__,'database_export_complete':False})
    if (HERE/'forward.json').exists():
        f=read('forward.json');path=Path('/proc')/str(f['pid'])/'cmdline'
        if path.exists() and path.read_bytes():
            assert [s.decode() for s in path.read_bytes().rstrip(b'\0').split(b'\0')]==f['command']
            os.kill(f['pid'],signal.SIGTERM)
    if (HERE/'owned-input-rule.json').exists():
        record=read('owned-input-rule.json');assert network in record['rule']
        present=subprocess.run(['iptables','-C','INPUT',*record['rule']],capture_output=True).returncode;assert present in (0,1)
        if present==0:cmd(['iptables','-D','INPUT',*record['rule']])
        assert subprocess.run(['iptables','-C','INPUT',*record['rule']],capture_output=True).returncode==1
        save('input-rule-removed.json',{'owned_rule_removed':True,'original_input_rules_restored':cmd(['iptables','-S','INPUT']).decode()==record['before']})
    probe=subprocess.run(['docker','network','inspect',network],capture_output=True)
    if probe.returncode==0:
        n=json.loads(probe.stdout)[0];assert n['Labels']['w02_id']==nonce and not n['Containers'];docker('network','rm',network)
    result={'owned_containers_removed':True,'owned_network_removed':True,'evidence_retained':True,'at':utc()}
    save('retirement.json',result);return result


if __name__=='__main__':
    try:
        action=sys.argv[1];result={'setup':setup,'finish_setup':finish_setup,'qualify':qualify,'campaign':campaign,'retire':retire}[action]()
        print(json.dumps({'action':action,'status':result.get('status','completed'),'at':utc()}))
    except BaseException as error:
        save('failure-'+str(time.time_ns())+'.json',{'error_type':type(error).__name__,'traceback':traceback.format_exc()})
        print(json.dumps({'error_type':type(error).__name__}));sys.exit(1)
