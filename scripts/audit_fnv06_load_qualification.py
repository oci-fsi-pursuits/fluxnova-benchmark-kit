#!/usr/bin/env python3
"""Audit saved FNV-06 load-driver qualification without calling any service."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.audit_fnv06_functional import inventory, check_snapshot, check_model, check_batch


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def epoch(value):
    time=datetime.fromisoformat(value);assert time.tzinfo is not None
    return time.timestamp()*1000


def expected_requests(spec, processes, prefix):
    variables={'delivery':{'value':prefix+'-delivered','type':'String'}}
    members=['m'+str(i).zfill(4) for i in range(spec['population'])]
    if spec['kind']=='message':
        return [{'path':'/message','body':{'messageName':prefix+'-event-match','businessKey':prefix+'-key-'+a,
            'withoutTenantId':True,'resultEnabled':True,'processVariables':variables}} for a in members]
    if spec['kind']=='signal':return [{'path':'/signal','body':{'name':prefix+'-event-match','variables':variables}}]
    assert spec['kind']=='batch'
    return [{'path':'/process-instance/message-async','body':{'messageName':prefix+'-event-match',
        'processInstanceIds':[processes[a]['id'] for a in members],'variables':variables}}]


def check_driver(fixture, actions, summary, *, expected, engine_url, max_first_lag_ms=1000):
    assert fixture['requests']==expected and fixture['study_id'] in ('FNV-06-LQ','FNV-06-LQ-SCALE','FNV-06-REPEAT-QUAL')
    assert fixture['population'] in ([1,4,16] if fixture['study_id']=='FNV-06-LQ' else [10,100,1000])
    assert fixture['vus']==(min(4,fixture['population']) if fixture['kind']=='message' else 1)
    assert summary['engine_url']==engine_url and summary['run_id']==fixture['run_id']
    assert summary['observation_id']==fixture['observation_id'] and summary['fixed_vus']==fixture['vus']
    assert summary['planned_actions']==len(expected)==len(actions)
    assert summary['metrics']['iterations']['values']['count']==len(expected)
    assert summary['metrics']['http_reqs']['values']['count']==len(expected)
    assert all(type(a['index']) is int for a in actions) and {a['index'] for a in actions}==set(range(len(expected)))
    release=fixture['release_epoch_ms'];status=204 if fixture['kind']=='signal' else 200
    for row in actions:
        assert row['study_id']==fixture['study_id'] and row['run_id']==fixture['run_id']
        assert row['observation_id']==fixture['observation_id'] and row['request']==expected[row['index']]
        assert row['status']==status and row['error_code']==0 and row['scheduled_epoch_ms']==release
        assert type(row['vu']) is int and 1<=row['vu']<=fixture['vus']
        assert all(isinstance(row[k],(int,float)) and math.isfinite(row[k]) for k in ('started_epoch_ms','finished_epoch_ms','elapsed_ms'))
        assert release<=row['started_epoch_ms']<=row['finished_epoch_ms'] and row['elapsed_ms']>=0
        assert abs(row['finished_epoch_ms']-row['started_epoch_ms']-row['elapsed_ms'])<=50,'Clock disagreement'
    first=min(r['started_epoch_ms'] for r in actions)
    assert 0<=first-release<=max_first_lag_ms,'Generator missed the declared release'
    return {'http_ack_median_ms':statistics.median(r['elapsed_ms'] for r in actions),
            'http_ack_max_ms':max(r['elapsed_ms'] for r in actions),'first_dispatch_lag_ms':first-release}


def check_observer(observer, requests, *, population, definition_id, release_epoch_ms, first_action_ms, limit_s=30):
    assert observer['exit_code']==0 and observer['owned_generator_stopped'] is True
    assert observer['release_epoch_ms']==release_epoch_ms
    path='/history/process-instance/count?processDefinitionId='+definition_id+'&finished=true'
    assert observer['poll_path']==path and len(observer['polls'])>=2
    previous=-1;indices=[]
    for item in observer['polls']:
        row=requests[item['request_index']];indices.append(item['request_index'])
        assert row['method']=='GET' and row['path']==path and row['status']==200
        assert row['response']=={'count':item['count']} and type(item['count']) is int
        assert 0<=item['count']<=population and (previous==-1 or previous<=item['count'])
        previous=item['count']
        assert item['start_elapsed_s']<=item['finish_elapsed_s']
        assert abs((epoch(row['started_at'])-release_epoch_ms)/1000-item['start_elapsed_s'])<=.05
        assert abs((epoch(row['finished_at'])-release_epoch_ms)/1000-item['finish_elapsed_s'])<=.05
    assert indices==sorted(set(indices))
    assert observer['polls'][0]['count']==0 and epoch(requests[indices[0]]['finished_at'])<first_action_ms
    assert observer['polls'][-1]['count']==population and all(p['count']<population for p in observer['polls'][:-1])
    lower=observer['polls'][-2]['start_elapsed_s'];upper=observer['polls'][-1]['finish_elapsed_s']
    assert lower<=upper and 0<=upper<=limit_s
    return {'all_recipients_completion_interval_s':[max(0,lower),upper],
            'completion_observed_s':upper,'completion_poll_requests':len(indices)}


def audit(folder):
    folder=folder.resolve();seal=read(folder/'checksums.json')
    actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() and p.name!='checksums.json'}
    assert actual==set(seal['files'])
    for name,digest in seal['files'].items():
        p=folder/name;assert p.resolve().is_relative_to(folder) and not p.is_symlink() and sha(p)==digest
    manifest=read(folder/'manifest.json');summary=read(folder/'summary.json')
    study=manifest['study_id'];assert study in ('FNV-06-LQ','FNV-06-LQ-SCALE','FNV-06-REPEAT-QUAL')
    repeat=study=='FNV-06-REPEAT-QUAL';scaled=study!='FNV-06-LQ'
    protocol=read(folder/'inputs/workloads/fnv06'/('repeat-qualification.json' if repeat else 'scale-qualification.json' if scaled else 'load-qualification.json'))
    assert study==summary['study_id']==protocol['study_id']
    assert manifest['run_id']==summary['run_id'] and summary['status']=='collected_pending_independent_audit'
    assert summary['owned_engine_stopped'] and not summary['cleanup_errors']
    assert type(manifest['cloud_resources_accessed']) is bool
    cloud=manifest['cloud_resources_accessed']
    assert summary['cloud_resources_accessed'] is cloud
    assert not manifest['model_calls'] and not summary['model_calls'] and not manifest['performance_comparison_complete'] and not summary['performance_comparison_complete']
    if cloud:
        from scripts.audit_fnv06_cloud import check_runtime,check_database,instant
        identity=read(folder/'identity.json');runtime=read(folder/'runtime.json')
        assert identity.get('qualification_protocol','small')==('repeat' if repeat else 'scale' if scaled else 'small')
        if repeat: assert identity['qualification_cell']==manifest['qualification_cell']
        window=check_runtime(manifest,summary,identity,runtime,read(folder/'isolation.json'),
            read(folder/'engine-closeout.json'),read(folder/'source-manifest.json'),runtime_script='scripts/run_fnv06_load_cloud.py')
        assert read(folder/'engine-version.json')==runtime['engine']
        assert manifest['engine_url']=='http://engine:8080/engine-rest'
        assert manifest['generator']==runtime['generator']==read(folder/'generator-check.json')
        assert manifest['generator']['archive']=='k6-v2.3.0-linux-amd64.tar.gz'
        assert manifest['generator']['archive_sha256']=='39c3117b6af817592dcd0ce4242105c0a7af10948c2a425306f0be8f7a8a8ab1'
    else:
        assert read(folder/'engine-version.json')['version']==manifest['runtime']['release']=='3.0.1'
        assert manifest['runtime']['distribution_sha256']=='ced4311c541faf1a7248d88e37ba53bd8c659fefe8387d2f78c21c30a117d7cf'
    assert 'v2.3.0' in manifest['generator']['version']
    assert manifest['generator']['archive_sha256'] in ('112276d495e5741c968e2bc09ea6196099c1275bd6db9ee0875d173c7148ce43','39c3117b6af817592dcd0ce4242105c0a7af10948c2a425306f0be8f7a8a8ab1')
    for name,digest in manifest['source_sha256'].items():assert sha(folder/'inputs'/name)==digest
    populations=(10,100,1000) if scaled else (1,4,16)
    fixed=[{'id':prefix+str(n).zfill(3),'kind':kind,'population':n} for n in populations for prefix,kind in [('M','message'),('S','signal'),('B','batch')]]
    if repeat:
        cells=[{'id':'C'+str(i+1).zfill(2),'kind':kind,'population':n} for i,(kind,n) in
               enumerate((kind,n) for kind in ('message','signal','batch') for n in (10,1000))]
        assert protocol['cells']==cells and protocol['warmup_recipients']==100
        cell=manifest['qualification_cell'];assert cell in cells
        fixed=[{'id':cell['id']+suffix,'kind':cell['kind'],'population':n} for suffix,n in [('W',100),('M',cell['population'])]]
    else: assert protocol['observations']==fixed
    assert summary['cases_collected']==[r['id'] for r in fixed]
    assert protocol['population_sizes']==list(populations) and protocol['message_client_ceiling']==4 and protocol['controls_per_observation']==2
    assert protocol['release_lead_ms']==3000 and protocol['maximum_first_dispatch_lag_ms']==1000 and protocol['completion_poll_ms']==100
    assert protocol['completion_timeout_s']==(120 if scaled else 30) and protocol['batch_terminal_timeout_s']==(240 if scaled else 120)
    assert manifest['functional_batch_wait_timeout_s']==protocol['batch_terminal_timeout_s'] and manifest['batch_poll_interval_s']==.5
    requests=[json.loads(l) for l in (folder/'requests.ndjson').read_text().splitlines()]
    assert all(r['index']==i and r['status'] is not None and r['elapsed_s']>=0 for i,r in enumerate(requests))
    baseline=inventory(read(folder/'baseline.json'),requests)
    assert all(not rows for name,rows in baseline.items() if name!='jobs')
    jobs={j['id'] for j in baseline['jobs']};expected={};batches=[];diagnostics=[];warmup=None
    last_after=max(v['count_request'] for v in read(folder/'baseline.json')['inventories'].values())
    for spec in fixed:
        case_id=spec['id'];directory=folder/'observations'/case_id;record=read(directory/'observation.json')
        prefix='fnv06-'+manifest['run_id'][:12]+'-'+case_id;assert record['spec']==spec and record['prefix']==prefix
        aliases=['m'+str(i).zfill(4) for i in range(spec['population'])]+['excluded','other']
        assert set(record['processes'])==set(aliases) and len(record['models'])==2
        models={m['definition']['id']:m for m in record['models']};kind='signal' if spec['kind']=='signal' else 'message'
        for alias,p in record['processes'].items():
            pid=p['id'];assert pid not in expected
            event='other' if alias=='other' or alias=='excluded' and spec['kind']=='signal' else 'match'
            assert p['event']==event and p['definition']==models[p['definition']['id']]['definition']
            check_model(folder,models[p['definition']['id']],{'id':case_id,'kind':kind},{'event':event},prefix,None,requests)
            body={'businessKey':prefix+'-key-'+alias,'variables':{k:{'value':v,'type':'String'} for k,v in
                {'caseId':case_id,'alias':alias,'sentinel':prefix+'-'+alias}.items()}}
            start=requests[p['start_request']]
            assert p['start_request']>last_after,'New cohort started before the previous state was recorded'
            assert p['input']==body and start['body']==body and start['method']=='POST' and start['status']==200
            assert start['path']=='/process-definition/'+p['definition']['id']+'/start' and start['response']['id']==pid
            expected[pid]={'case_id':case_id,'alias':alias,'prefix':prefix,'definition_id':p['definition']['id'],
                'tenant':None,'kind':kind,'event':prefix+'-event-'+event,'business_key':body['businessKey'],
                'completed':False,'delivered':False,'suspended':False}
        check_snapshot(record['before'],expected,requests,jobs,batches)
        fixture=read(directory/'driver-input.json');actions=read(directory/'actions.json');k6=read(directory/'k6-summary.json')
        assert fixture['study_id']==study and fixture['run_id']==manifest['run_id'] and fixture['observation_id']==case_id and fixture['kind']==spec['kind'] and fixture['population']==spec['population']
        assert k6['fixture_sha256']==sha(directory/'driver-input.json')
        logged=[]
        for line in (directory/'k6.log').read_text().splitlines():
            try: outer=json.loads(line)
            except ValueError:continue
            if outer.get('msg','').startswith('FNV06_ACTION '):logged.append(json.loads(outer['msg'][13:]))
        assert actions==logged,'Action inventory differs from native k6 log'
        intended=expected_requests(spec,record['processes'],prefix)
        timing=check_driver(fixture,actions,k6,expected=intended,engine_url=manifest['engine_url'])
        first=min(r['started_epoch_ms'] for r in actions)
        before_end=max(v['count_request'] for v in record['before']['inventories'].values())
        assert epoch(requests[before_end]['finished_at'])<first
        if spec['kind']=='message':
            for action in actions:
                result=action['response'];alias=aliases[action['index']]
                assert len(result)==1 and result[0]['resultType']=='Execution'
                assert result[0]['execution']['processInstanceId']==record['processes'][alias]['id']
        elif spec['kind']=='signal':assert actions[0]['response'] is None
        else:
            batch_id=check_batch({'request':actions[0],'batch':record['batch']},requests,intended[0]['body']['processInstanceIds'])
            assert batch_id not in batches;batches.append(batch_id)
        if spec['kind']!='batch':assert record['batch'] is None
        definition=record['processes'][aliases[0]]['definition']['id']
        observer=read(directory/'observer.json')
        observed=check_observer(observer,requests,population=spec['population'],definition_id=definition,
                                release_epoch_ms=fixture['release_epoch_ms'],first_action_ms=first,limit_s=120 if scaled else 30)
        assert min(p['request_index'] for p in observer['polls'])>before_end
        for alias in aliases[:-2]:
            pid=record['processes'][alias]['id'];expected[pid].update(completed=True,delivered=True)
        state_after=check_snapshot(record['after'],expected,requests,jobs,batches)
        last_after=max(v['count_request'] for v in record['after']['inventories'].values())
        if repeat and case_id==cell['id']+'W':
            assert read(folder/'warmup-state.json')=={'case_id':case_id,'state':record['after']}
            shapes=Counter((v['name'],v['type'],len(v['value'])) for v in state_after['variables'])
            signature={'kind':cell['kind'],'completed_histories':sum(p['completed'] for p in expected.values()),
                'waiting_controls':len(state_after['instances']),'subscriptions':len(state_after['subscriptions']),
                'historic_batches':len(state_after['historic_batches']),
                'variables':[[*key,count] for key,count in sorted(shapes.items())],
                'model_template_sha256':sha(folder/'inputs/workloads/fnv06'/(kind+'.bpmn'))}
            assert signature['completed_histories']==100 and signature['waiting_controls']==signature['subscriptions']==2
            assert signature['historic_batches']==(1 if cell['kind']=='batch' else 0)
            warmup={'status':'passed','signature':signature,
                'signature_sha256':hashlib.sha256(json.dumps(signature,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                'scope':'Equivalent declared workflow state and variable lengths; not identical physical database/cache/JIT state.'}
        after_start=min(v['list_request'] for v in record['after']['inventories'].values())
        assert after_start>max(p['request_index'] for p in observer['polls'])
        assert epoch(requests[after_start]['started_at'])>=max(r['finished_epoch_ms'] for r in actions)
        timing.update(observed)
        if spec['kind']=='batch':
            last=record['batch']['observations'][-1]['history_request']
            timing['batch_terminal_observed_s']=(epoch(requests[last]['finished_at'])-fixture['release_epoch_ms'])/1000
            assert timing['batch_terminal_observed_s']>=timing['completion_observed_s']
        diagnostics.append({'id':case_id,'kind':spec['kind'],'population':spec['population'],
                            'planned_actions':len(intended),'diagnostic_only':True,**timing})
    before_cleanup=check_snapshot(read(folder/'before-cleanup.json'),expected,requests,jobs,batches)
    total=sum(s['population']+2 for s in fixed);batch_count=sum(s['kind']=='batch' for s in fixed)
    assert set(summary['owned_process_ids'])==set(expected) and len(expected)==total and len(summary['owned_process_ids'])==total
    assert summary['owned_batch_ids']==batches and len(batches)==batch_count
    waiting={pid for pid,e in expected.items() if not e['completed']}
    cleanup_requests=requests;database_audit=None
    if cloud:
        cleanup_requests=[json.loads(line) for line in (folder/'cleanup/requests.ndjson').read_text().splitlines()]
        assert all(r['index']==i and r['status'] is not None and r['elapsed_s']>=0 for i,r in enumerate(cleanup_requests))
        sql=read(folder/'database-before-cleanup.json')
        assert all(window[0]<=instant(r['started_at'])<=instant(r['finished_at'])<window[1] for r in requests)
        assert max(instant(r['finished_at']) for r in requests)<=instant(sql['started_at'])
        assert instant(sql['finished_at'])<window[1]
        assert instant(sql['finished_at'])<=min(instant(r['started_at']) for r in cleanup_requests)
        database_audit=check_database(sql,before_cleanup,run_id=manifest['run_id'],target_id=identity['target_id'],
                                     database_user=runtime['database_role'])
        assert not any(r['method']=='DELETE' for r in requests)
    deletions=[r for r in cleanup_requests if r['method']=='DELETE' and r['path'].startswith('/process-instance/')]
    assert len(deletions)==len(waiting)==2*len(fixed) and all(r['status']==204 for r in deletions)
    assert {r['path'].split('?')[0].rsplit('/',1)[1] for r in deletions}==waiting
    counts=read(folder/'final-engine-counts.json')
    assert set(counts)=={'/process-instance','/event-subscription','/incident','/external-task','/batch'} and all(v==0 for v in counts.values())
    for path in counts:
        last=[r for r in cleanup_requests if r['method']=='GET' and r['path']==path+'/count'][-1]
        assert last['index']>max(r['index'] for r in deletions) and last['status']==200 and last['response']=={'count':0}
    assert all(sha(folder/name)==digest for name,digest in seal['files'].items())
    result={'study_id':study,'status':'passed','scope':manifest['scope'],
        'source_checksums_sha256':sha(folder/'checksums.json'),'auditor_sha256':sha(Path(__file__)),
        'shared_state_auditor_sha256':sha(Path(__file__).with_name('audit_fnv06_functional.py')),
        'sealed_files_verified':len(actual),'observations':len(fixed),'native_k6_actions':sum(r['planned_actions'] for r in diagnostics),
        'workflow_instances':total,'expected_completions':sum(s['population'] for s in fixed),'unchanged_waiting_controls':2*len(fixed),'asynchronous_batches':batch_count,
        'unintended_transitions':0,'owned_engine_stopped':True,'cloud_resources_accessed':cloud,
        'model_calls':0,'performance_comparison_complete':False,'diagnostics':diagnostics,
        'database_audit':database_audit,
        'cloud_auditor_sha256':sha(Path(__file__).with_name('audit_fnv06_cloud.py')) if cloud else None,
        'evidence_boundary':('Fixed warmup and cell qualification with complete original-input/state audit; not repeated performance results. Cloud runs additionally require independent final SQL reconciliation.' if repeat else
            'Complete paginated REST inventories and batch job logs, real k6 and recipient observations; independent final SQL reconciliation is required for cloud runs. Fixed-population qualification, not a repeated performance or capacity result.' if scaled else
            'Real k6, original inputs, REST inventories, completion observations and independent final SQL reconciliation. Small-population driver qualification; no capacity or speedup claim.' if cloud else
            'Real k6, original inputs, REST inventories and completion observations. Local small-population driver qualification; no independent SQL audit, capacity or speedup claim.')}
    if repeat:
        assert warmup is not None
        result.update(qualification_cell=cell,warmup=warmup)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists() or a.output.resolve().is_relative_to(a.source.resolve()):p.error('Use a fresh audit output outside the sealed evidence.')
    result=audit(a.source);a.output.mkdir(parents=True);(a.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='diagnostics'}))


if __name__=='__main__':main()
