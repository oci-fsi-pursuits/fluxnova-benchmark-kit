#!/usr/bin/env python3
"""Qualify W01 burst accounting on fresh owned local Fluxnova/H2 stacks.

This is a short driver/control gate, not the matched OCI performance campaign.
Each arm starts with an empty engine and ledger. No OCI resource is accessed.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from benchlib.fluxnova import FluxnovaClient
from benchlib.w01_pacing import audit
from scripts.run_w02_functional import distribution_inventory, require, save, sha
from scripts.qualify_w02_load import verify_k6
from services.mock_service import Ledger, make_server

SOURCES=['scripts/qualify_w01_pacing.py','scripts/run_w02_functional.py',
         'scripts/qualify_w02_load.py','benchlib/w01_pacing.py','benchlib/analysis.py',
         'benchlib/fluxnova.py','benchlib/__init__.py','services/worker.py',
         'services/mock_service.py','services/http_support.py','services/__init__.py',
         'load/worker_pacing.js','workloads/settlement.bpmn']


def parse_log(path):
    events=[];http=[]
    for line in path.read_text(encoding='utf-8',errors='replace').splitlines():
        try:
            outer=json.loads(line)
        except json.JSONDecodeError:
            continue
        msg=outer.get('msg','')
        if msg.startswith('W01_EVENT '):events.append(json.loads(msg[len('W01_EVENT '):]))
        elif msg.startswith('W01_HTTP '):http.append(json.loads(msg[len('W01_HTTP '):]))
    return events,http


def lane(folder,args,inputs,arm):
    folder.mkdir()
    with socket.socket() as probe:probe.bind(('127.0.0.1',args.port))
    user,password='w01local',secrets.token_urlsafe(24)
    config=folder/'engine.local.yml'
    config.write_text('''server:
  address: 127.0.0.1
  port: '''+str(args.port)+'''
fluxnova.bpm:
  admin-user:
    id: '''+user+'''
    password: '''+json.dumps(password)+'''
  history-level: full
  authorization:
    enabled: true
  run:
    auth:
      enabled: true
    cors:
      enabled: false
    example:
      enabled: false
spring.datasource:
  url: '''+json.dumps('jdbc:h2:'+str(folder/'engine-db').replace('\\','/')+';DB_CLOSE_ON_EXIT=FALSE')+'''
  driver-class-name: org.h2.Driver
  username: sa
  password: sa
spring.web.resources:
  static-locations: NULL
''',encoding='utf-8')
    empty=folder/'empty-deployments';empty.mkdir()
    command=[str(args.java),'-Xms256m','-Xmx1024m',
        '-Dloader.path='+','.join(str(args.runtime/p) for p in ('internal/rest','internal/webapps','configuration/userlib')),
        '-Dcamunda.deploymentDir='+str(empty),'-jar',str(args.runtime/'internal/fluxnova-bpm-run-core.jar'),
        '--spring.config.location='+config.as_uri()]
    engine_process=worker_process=load_process=server=thread=None
    logs=[];cleanup_errors=[]
    def stop(process):
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
    def cleanup(label,fn):
        try:fn()
        except Exception as e:cleanup_errors.append({'component':label,'error_type':type(e).__name__})
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    try:
        elog=(folder/'engine.log').open('wb');logs.append(elog)
        engine_process=subprocess.Popen(command,cwd=folder,stdout=elog,stderr=subprocess.STDOUT,creationflags=flags)
        base=f'http://127.0.0.1:{args.port}/engine-rest'
        engine=FluxnovaClient(base,user,password)
        until=time.monotonic()+180
        while True:
            require(engine_process.poll() is None,'engine_exited_before_ready')
            try:save(folder/'engine-version.json',engine.readiness());break
            except (OSError,RuntimeError):
                require(time.monotonic()<until,'engine_start_timeout');time.sleep(.5)
        save(folder/'deployment.json',engine.deploy([inputs/'workloads/settlement.bpmn']))
        initial={k:engine.request('GET',p)['count'] for k,p in {
            'active_instances':'/process-instance/count','external_tasks':'/external-task/count',
            'incidents':'/incident/count','history':'/history/process-instance/count'}.items()}
        require(all(v==0 for v in initial.values()),'nonempty_initial_engine')
        save(folder/'initial-state.json',initial)
        ledger=Ledger(folder/'ledger.sqlite')
        token=secrets.token_urlsafe(24)
        server=make_server('127.0.0.1',0,ledger,token)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        support=f'http://127.0.0.1:{server.server_port}'
        delay=.1 if arm=='success_delay_100ms' else 0
        env={k:v for k,v in os.environ.items() if not k.startswith(('BENCH_','W01_','W02_'))}
        env.update(PYTHONPATH=str(inputs),PYTHONDONTWRITEBYTECODE='1',
            BENCH_USERNAME=user,BENCH_PASSWORD=password,BENCH_SUPPORT_TOKEN=token)
        wlog=(folder/'worker.log').open('wb');logs.append(wlog)
        worker_process=subprocess.Popen([sys.executable,'-m','services.worker',
            '--engine-url',base,'--support-url',support,'--poll-s','0.1',
            '--success-delay-s',str(delay)],cwd=inputs,env=env,stdout=wlog,stderr=subprocess.STDOUT,creationflags=flags)
        run_id='w01-'+uuid.uuid4().hex[:12]
        save(folder/'run.json',{'run_id':run_id,'arm':arm,'worker_slots':1,
            'success_delay_s':delay,'idle_and_error_delay_s':.1,'worker_timing':False,
            'warmup_bursts':args.warmup_bursts,'measurement_bursts':args.measured_bursts,
            'burst_starts':20,'burst_duration_s':1,'burst_period_s':10,
            'deadline_s':2,'poll_interval_ms':100,'fresh_local_stack':True,
            'source_worker_sha256':sha(inputs/'services/worker.py')})
        load_env={k:v for k,v in env.items() if not k.startswith('BENCH_')}
        load_env.update({k:str(v) for k,v in {
            'W01_ENGINE_URL':base,'W01_SUPPORT_URL':support,'W01_USERNAME':user,
            'W01_PASSWORD':password,'W01_SUPPORT_TOKEN':token,'W01_RUN_ID':run_id,
            'W01_ARM':arm,'W01_WARMUP_BURSTS':args.warmup_bursts,
            'W01_MEASURED_BURSTS':args.measured_bursts,'W01_OUTPUT_DIR':folder}.items()})
        klog=(folder/'k6.log').open('wb');logs.append(klog)
        load_process=subprocess.Popen([str(args.k6),'run','--log-format','json','--no-color',
            str(inputs/'load/worker_pacing.js')],cwd=inputs,env=load_env,
            stdout=klog,stderr=subprocess.STDOUT,creationflags=flags)
        returncode=load_process.wait(timeout=(args.warmup_bursts+args.measured_bursts)*10+60)
        klog.flush()
        require(worker_process.poll() is None,'worker_exited_during_load')
        # The driver allows bounded drain after every scheduled arrival.
        state={k:engine.request('GET',p)['count'] for k,p in {
            'active_instances':'/process-instance/count','external_tasks':'/external-task/count',
            'incidents':'/incident/count','completed_instances':'/history/process-instance/count?finished=true'}.items()}
        with ledger.connect() as db:
            state.update({name:db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in ('effects','receipts')})
            require(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','ledger_integrity_failed')
            effects=db.execute('SELECT key,amount,beneficiary FROM effects ORDER BY key').fetchall()
        save(folder/'final-state.json',state)
        events,http=parse_log(folder/'k6.log')
        with (folder/'events.ndjson').open('w',encoding='utf-8') as stream:
            for event in events:stream.write(json.dumps(event,allow_nan=False)+'\n')
        summary=json.loads((folder/'k6-summary.json').read_text())
        require(summary['w01']['run_id']==run_id and summary['w01']['arm']==arm,'load_identity_mismatch')
        result=audit(events,summary,state,returncode)
        expected_keys={r['logical_request_id'] for r in events if r['event']=='completed' and r['status']=='correct'}
        require(effects==[(key,125,'synthetic-beneficiary') for key in sorted(expected_keys)],'persisted_effects_mismatch')
        buckets={}
        for row in http:
            key=int(row['elapsed_s']//1);buckets[key]=buckets.get(key,0)+1
        result.update(arm=arm,persisted_effects_checked=len(effects),
            observed_peak_http_requests_per_1s=max(buckets.values(),default=0),
            generator_loopback_headroom_validated=False,cloud_telemetry_validated=False)
        save(folder/'assessment.json',result)
        return result
    finally:
        cleanup('load',lambda:stop(load_process))
        cleanup('worker',lambda:stop(worker_process))
        if server:
            cleanup('server',server.shutdown);cleanup('server_close',server.server_close)
        if thread:cleanup('server_thread',lambda:thread.join(timeout=5))
        cleanup('engine',lambda:stop(engine_process))
        for log in logs:cleanup('log',log.close)
        cleanup('credential_redaction',lambda:config.write_text(
            config.read_text(encoding='utf-8').replace(json.dumps(password),'"REDACTED_AFTER_SHUTDOWN"'),encoding='utf-8'))
        save(folder/'cleanup.json',{'owned_engine_stopped':bool(engine_process and engine_process.poll() is not None),
            'owned_worker_stopped':bool(worker_process and worker_process.poll() is not None),'errors':cleanup_errors})
        require(not cleanup_errors,'owned_local_cleanup_incomplete')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--runtime',type=Path,default=ROOT/'.tools/fluxnova-3.0.1')
    p.add_argument('--java',type=Path,default=ROOT/'.tools/java21/jdk-21.0.12.1+1-jre/bin/java.exe')
    p.add_argument('--k6',type=Path,default=ROOT/'.tools/k6/k6-v2.3.0-windows-amd64/k6.exe')
    p.add_argument('--port',type=int,default=28380)
    p.add_argument('--warmup-bursts',type=int,default=1)
    p.add_argument('--measured-bursts',type=int,default=2)
    args=p.parse_args()
    if args.output.exists():p.error('Use a new output directory; retain earlier attempts.')
    if not 1<=args.warmup_bursts<=6 or not 1<=args.measured_bursts<=60:p.error('Invalid burst counts')
    for field in ('output','runtime','java','k6'):setattr(args,field,getattr(args,field).resolve())
    args.output.mkdir(parents=True)
    inputs=args.output/'inputs';inputs.mkdir()
    inventory={}
    for name in SOURCES:
        target=inputs/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/name,target);inventory[name]=sha(target)
    save(args.output/'source-sha256.json',inventory)
    save(args.output/'runtime.json',{'scope':'Local W01 driver/control qualification; not OCI performance',
        'distribution':distribution_inventory(args.runtime),'k6':verify_k6(args.k6),
        'java_version':subprocess.run([str(args.java),'-version'],capture_output=True,text=True,check=True).stderr.strip(),
        'worker_slots':1,'java_heap_mib':1024,'database':'fresh embedded H2 per arm',
        'order':['success_delay_100ms','idle_only'],'cloud_resources_accessed':False})
    results=[]
    try:
        for arm in ('success_delay_100ms','idle_only'):
            print('Qualifying '+arm,flush=True)
            result=lane(args.output/arm,args,inputs,arm);results.append(result)
            require(result['status']=='passed','local_driver_qualification_failed')
        require(all(sha(ROOT/name)==value for name,value in inventory.items()),'source_changed_during_qualification')
        save(args.output/'summary.json',{'status':'passed','scope':'Local accounting/outcome qualification only',
            'arms':results,'matched_oci_study_completed':False,
            'limits':['Fresh H2 stacks, not a restored cloud checkpoint','No cloud headroom/telemetry qualification',
                      'Two qualification arms are not five randomized matched pairs']})
    except BaseException as e:
        save(args.output/'failure.json',{'status':'failed','error_type':type(e).__name__,
            'error':str(e),'completed_arms':results})
        raise
    print('Local W01 driver/control qualification passed.',flush=True)


if __name__=='__main__':
    main()
