"""Collect a small real-k6 FNV-06 qualification; no performance claim is made."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

from benchlib.fnv06_functional import deploy, render_model, await_batch, save, sha


def parse_actions(log):
    result = []
    for line in log.splitlines():
        try: item = json.loads(line)
        except (json.JSONDecodeError, ValueError): continue
        message = item.get('msg', '')
        if message.startswith('FNV06_ACTION '): result.append(json.loads(message[len('FNV06_ACTION '):]))
    return result


def retain_failure_state(api, path, record, summary, error, phase):
    """Keep a failed observation failed, but attempt its final state inventory."""
    failure={'id':record['spec']['id'],'phase':phase,'error_type':type(error).__name__,
             'snapshot_available':False}
    try:
        record['after']=api.snapshot()
        failure['snapshot_available']=True
    except Exception as capture_error:
        failure['state_capture_error_type']=type(capture_error).__name__
    record['load_failure']=failure;summary['failed_observation']=failure
    save(path,record);save(api.output/'progress.json',summary)


def run_driver(api, folder, fixture, k6, script, protocol, definition_id, *, bound=lambda: None):
    """Observe completion concurrently with k6; retain the acknowledgment separately."""
    clock_start = time.monotonic()
    clock_epoch = time.time() * 1000
    clock_finish = time.monotonic()
    release = int(clock_epoch) + protocol['release_lead_ms']
    release_clock = (clock_start + clock_finish) / 2 + (release - clock_epoch) / 1000
    fixture['release_epoch_ms'] = release
    save(folder/'driver-input.json', fixture)
    env = dict(os.environ, FNV06_CASE_FILE=str((folder/'driver-input.json').resolve()),
        FNV06_CASE_SHA256=sha(folder/'driver-input.json'), FNV06_ENGINE_URL=api.engine.base_url,
        FNV06_AUTH=api.engine.auth, FNV06_SUMMARY=str((folder/'k6-summary.json').resolve()),
        K6_NO_USAGE_REPORT='true')
    poll_path = '/history/process-instance/count?processDefinitionId=' + definition_id + '&finished=true'
    record = {'poll_path': poll_path, 'polls': [], 'release_epoch_ms': release, 'exit_code': None,
              'clock_origin': {'started_monotonic_s': clock_start, 'epoch_ms': clock_epoch,
                               'finished_monotonic_s': clock_finish, 'release_monotonic_s': release_clock}}
    process = None; first_complete = None
    try:
        with (folder/'k6.log').open('xb') as log:
            process = subprocess.Popen([str(k6), 'run', '--log-format', 'json', '--no-color', str(script)],
                env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            while True:
                bound()
                if first_complete is None:
                    start = time.monotonic() - release_clock
                    row = api.get(poll_path)
                    observation = {'request_index': row['index'], 'count': row['response']['count'],
                        'start_elapsed_s': start, 'finish_elapsed_s': time.monotonic() - release_clock}
                    record['polls'].append(observation); save(folder/'observer.json', record)
                    if observation['count'] > fixture['population']: raise RuntimeError('Unexpected extra completion')
                    if observation['count'] == fixture['population']: first_complete = observation
                code = process.poll()
                if code is not None:
                    record['exit_code'] = code
                    if code != 0: raise RuntimeError('k6 failed; retain all evidence')
                    if first_complete is not None: break
                if time.monotonic() - release_clock > protocol['completion_timeout_s']:
                    raise TimeoutError('Recipients did not complete within the qualification bound')
                time.sleep(protocol['completion_poll_ms']/1000)
    finally:
        if process and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
        if process: record['exit_code'] = process.returncode
        record['owned_generator_stopped'] = bool(process and process.poll() is not None)
        record['finished_clock'] = {'epoch_ms': time.time() * 1000, 'monotonic_s': time.monotonic()}
        save(folder/'observer.json', record)
        if (folder/'k6.log').exists():
            actions = parse_actions((folder/'k6.log').read_text(errors='replace'))
            save(folder/'actions.json', actions)
    return actions


def exercise(api, protocol, run_id, username, summary, k6, *, bound=lambda: None):
    output = api.output
    populations = {'FNV-06-LQ': [1,4,16], 'FNV-06-LQ-SCALE': [10,100,1000],
                   'FNV-06-REPEAT-QUAL': [10,100,1000], 'FNV-06-SCALING-1': [10,100,1000]}
    if protocol['study_id'] not in populations or protocol['population_sizes'] != populations[protocol['study_id']]:
        raise ValueError('Only the fixed qualification protocols are supported')
    if protocol['study_id'] in ('FNV-06-REPEAT-QUAL','FNV-06-SCALING-1'):
        if protocol['study_id']=='FNV-06-SCALING-1':
            from benchlib.fnv06_campaign import cell_protocol
        else:
            from benchlib.fnv06_repeat import cell_protocol
        if cell_protocol(protocol, protocol['qualification_cell']['id']) != protocol:
            raise ValueError('Warmup qualification cell differs from the fixed protocol')
    (output/'models').mkdir(); (output/'observations').mkdir()
    save(output/'baseline.json', api.snapshot())
    for spec in protocol['observations']:
        bound(); case_id = spec['id']; kind = 'signal' if spec['kind']=='signal' else 'message'
        prefix = 'fnv06-' + run_id[:12] + '-' + case_id
        folder = output/'observations'/case_id; folder.mkdir()
        record = {'spec': spec, 'prefix': prefix, 'models': [], 'processes': {}, 'batch': None}
        path = folder/'observation.json'; save(path, record)
        models = {}
        for event in ('match','other'):
            model = output/'models'/(case_id+'-'+event+'.bpmn')
            template = (output/'inputs/workloads/fnv06'/(kind+'.bpmn')).read_bytes()
            model.write_bytes(render_model(template, 'bench_fnv06_'+case_id+'_'+event, prefix+'-event-'+event))
            models[event] = deploy(api, model, None); record['models'].append(models[event])
        aliases = ['m'+str(i).zfill(4) for i in range(spec['population'])] + ['excluded','other']
        for alias in aliases:
            event = 'other' if alias=='other' or (alias=='excluded' and spec['kind']=='signal') else 'match'
            definition = models[event]['definition']
            body = {'businessKey': prefix+'-key-'+alias, 'variables': {k:{'value':v,'type':'String'} for k,v in
                    {'caseId':case_id,'alias':alias,'sentinel':prefix+'-'+alias}.items()}}
            row = api.call('POST', '/process-definition/'+definition['id']+'/start', body)
            if row['status'] != 200: raise RuntimeError('Instance start failed')
            record['processes'][alias] = {'id':row['response']['id'], 'definition':definition,
                'start_request':row['index'], 'input':body, 'event':event}
            summary['owned_process_ids'].append(row['response']['id']); save(output/'progress.json', summary)
        record['before'] = api.snapshot(); save(path, record)
        variables = {'delivery':{'value':prefix+'-delivered','type':'String'}}
        members = [record['processes'][a]['id'] for a in aliases[:-2]]
        if spec['kind']=='message':
            requests = [{'path':'/message','body':{'messageName':prefix+'-event-match','businessKey':prefix+'-key-'+a,
                'withoutTenantId':True,'resultEnabled':True,'processVariables':variables}} for a in aliases[:-2]]
        elif spec['kind']=='signal': requests = [{'path':'/signal','body':{'name':prefix+'-event-match','variables':variables}}]
        else: requests = [{'path':'/process-instance/message-async','body':{'messageName':prefix+'-event-match',
                'processInstanceIds':members,'variables':variables}}]
        fixture = {'study_id':protocol['study_id'],'run_id':run_id,'observation_id':case_id,'kind':spec['kind'],
            'population':spec['population'],'vus':min(4,spec['population']) if spec['kind']=='message' else 1,'requests':requests}
        driver_error=None
        try:
            actions = run_driver(api,folder,fixture,k6,output/'inputs/load/fnv06_events.js',protocol,
                                 models['match']['definition']['id'],bound=bound)
        except Exception as error:
            driver_error=error
            actions=json.loads((folder/'actions.json').read_text()) if (folder/'actions.json').exists() else []
        finally:
            if spec['kind']=='batch' and (folder/'actions.json').exists():
                for action in json.loads((folder/'actions.json').read_text()):
                    if action['request']==requests[0] and action['status']==200 and isinstance(action['response'],dict) and action['response'].get('id'):
                        batch_id=action['response']['id']
                        if batch_id not in summary['owned_batch_ids']:summary['owned_batch_ids'].append(batch_id)
                save(output/'progress.json',summary)
        expected_status = 204 if spec['kind']=='signal' else 200
        if driver_error is not None:
            retain_failure_state(api,path,record,summary,driver_error,'dispatch_or_drain')
            raise driver_error
        if len(actions)!=len(requests) or any(a['status']!=expected_status for a in actions):
            error=RuntimeError('Missing or unsuccessful load action')
            retain_failure_state(api,path,record,summary,error,'request_result')
            raise error
        if spec['kind']=='batch':
            batch = actions[0]['response']
            record['batch'] = await_batch(api,batch,protocol['batch_terminal_timeout_s'])
        record['after'] = api.snapshot(); save(path, record)
        if protocol['study_id'] in ('FNV-06-REPEAT-QUAL','FNV-06-SCALING-1') and case_id.endswith('W'):
            # This is a retained observation, not a collector-declared pass.
            # The separate auditor checks every history, variable and control.
            save(output/'warmup-state.json', {'case_id': case_id, 'state': record['after']})
        summary['cases_collected'].append(case_id); save(output/'progress.json', summary)
    save(output/'before-cleanup.json',api.snapshot())
