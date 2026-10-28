#!/usr/bin/env python3
"""Reversibly swap the reference app's Java agent mode on its Linux host.

Preserves both containers and all environment values except JAVA_TOOL_OPTIONS.
Call only between workloads; the controller must also verify no k6 is active.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchlib.deployment import run_container
from benchlib.fluxnova import FluxnovaClient
from benchlib.util import ROOT,utc_now,write_json

ACTIVE='fluxnova-benchmark'
AGENT='-javaagent:/opt/opentelemetry-javaagent.jar'


def agent_environment(environment,enabled):
    result=dict(environment)
    if result.get('JAVA_TOOL_OPTIONS') not in (None,AGENT):
        raise ValueError('Unexpected JVM options; refusing to discard them')
    if enabled:result['JAVA_TOOL_OPTIONS']=AGENT
    else:result.pop('JAVA_TOOL_OPTIONS',None)
    return result


def docker(*argv,optional=False):
    p=subprocess.run(['docker',*argv],capture_output=True,text=True,timeout=120)
    if p.returncode:
        if optional:return None
        raise RuntimeError('Docker operation failed: '+argv[0])
    return p.stdout


def inspect(name):
    value=docker('inspect',name,optional=True)
    return json.loads(value)[0] if value else None


def environment(value):return dict(x.split('=',1) for x in value['Config']['Env'])


def controls(value):
    return {'image':value['Config']['Image'],'image_id':value['Image'],
            'config':{k:value['Config'][k] for k in ('Entrypoint','Cmd','User','WorkingDir','ExposedPorts')},
            'host':{k:value['HostConfig'][k] for k in ('NanoCpus','Memory','Binds','PortBindings','NetworkMode')},
            'environment':agent_environment(environment(value),False)}


def ready(client):
    for _ in range(90):
        try:client.readiness();return
        except Exception:time.sleep(2)
    raise RuntimeError('Application readiness failed after instrumentation switch')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--mode',choices=('on','off'),required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if os.name!='posix':raise ValueError('Run on the benchmark application Linux host')
    os.umask(0o077)
    output=args.output.resolve()
    if not output.is_relative_to((ROOT/'dist').resolve()) or output.exists():raise ValueError('Use a new receipt under dist')
    before=inspect(ACTIVE)
    if not before or not before['State']['Running']:raise ValueError('Reference application must initially be running')
    env=environment(before);agent_environment(env,False)
    initial='on' if env.get('JAVA_TOOL_OPTIONS')==AGENT else 'off'
    client=FluxnovaClient('http://127.0.0.1:8080/engine-rest',env['FLUXNOVA_BPM_ADMIN_USER_ID'],env['FLUXNOVA_BPM_ADMIN_USER_PASSWORD'])
    if any(client.request('GET',route)['count'] for route in ('/process-instance/count','/external-task/count','/incident/count')):
        raise ValueError('Benchmark queues must drain before switching instrumentation')
    result={'started_at':utc_now(),'initial_mode':initial,'requested_mode':args.mode,'success':False,'changed':initial!=args.mode}
    if initial==args.mode:
        result.update(success=True,completed_at=utc_now(),controls_unchanged=True)
        write_json(output,result);print(json.dumps(result));return
    parked=ACTIVE+'-agent-'+initial;desired=ACTIVE+'-agent-'+args.mode
    if inspect(parked):raise ValueError('Reserved parking container already exists')
    candidate=inspect(desired)
    if candidate:
        if candidate['State']['Running'] or controls(candidate)!=controls(before):raise ValueError('Stored alternate container differs from current controls')
        if environment(candidate)!=agent_environment(env,args.mode=='on'):raise ValueError('Stored alternate environment differs')
    host=before['HostConfig']
    if host['NetworkMode']!='bridge' or before['Config']['Cmd'] is not None or before['Config']['Entrypoint']!=['/opt/fluxnova/benchmark-start.sh']:
        raise ValueError('Unsupported reference container layout')
    options=['--cpus',str(host['NanoCpus']/1e9),'--memory',str(host['Memory']),'--network','bridge']
    for bind in host['Binds']:options+=['-v',bind]
    for target,bindings in host['PortBindings'].items():
        for binding in bindings:
            options+=['-p',(binding['HostIp']+':' if binding['HostIp'] else '')+binding['HostPort']+':'+target]
    try:
        docker('update','--restart=no',ACTIVE);docker('stop','--time','30',ACTIVE);docker('rename',ACTIVE,parked)
        if candidate:
            docker('rename',desired,ACTIVE);docker('update','--restart=unless-stopped',ACTIVE);docker('start',ACTIVE)
        else:run_container(ACTIVE,before['Config']['Image'],agent_environment(env,args.mode=='on'),options)
        after=inspect(ACTIVE)
        if controls(after)!=controls(before) or environment(after)!=agent_environment(env,args.mode=='on'):
            raise RuntimeError('Instrumentation switch changed another recorded control')
        ready(client)
        result.update(success=True,controls_unchanged=True,active_image=after['Config']['Image'],
                      agent_attached=args.mode=='on',parked_container=parked)
    except BaseException:
        # Preserve any failed replacement, then bring the original instance back.
        current=inspect(ACTIVE)
        if current and current['Id']!=before['Id']:
            docker('update','--restart=no',ACTIVE);docker('stop','--time','30',ACTIVE)
            docker('rename',ACTIVE,ACTIVE+'-failed-'+uuid.uuid4().hex[:10])
        if inspect(parked):docker('rename',parked,ACTIVE)
        docker('update','--restart=unless-stopped',ACTIVE);docker('start',ACTIVE);ready(client)
        result['original_restored']=True
        raise
    finally:
        result['completed_at']=utc_now();write_json(output,result)
    print(json.dumps(result))


if __name__=='__main__':main()
