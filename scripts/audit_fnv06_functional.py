#!/usr/bin/env python3
"""Audit sealed FNV-06 REST evidence without starting workflows or importing its collector."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BPMN='http://www.omg.org/spec/BPMN/20100524/MODEL'
INVENTORIES={'instances':'/process-instance','history':'/history/process-instance','activities':'/history/activity-instance',
             'variables':'/history/variable-instance','subscriptions':'/event-subscription','incidents':'/incident',
             'jobs':'/job','batches':'/batch','historic_batches':'/history/batch'}


def read(path): return json.loads(path.read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def response(requests, index, method='GET'):
    row=requests[index]
    assert row['index']==index and row['method']==method and row['status']==200
    return row['response']


def paged_inventory(item, requests, path, parameters=None):
    parameters=parameters or {}
    assert item['format']=='counted-pages-v1' and item['path']==path and item['parameters']==parameters
    assert item['page_size']==500 and type(item['count']) is int and 0<=item['count']<=100000
    total=item['count'];rows=[];indices=[item['count_before_request']]
    def query(base,extra=None):
        values={**parameters,**(extra or {})}
        return base+('?' + urlencode(values) if values else '')
    for index in (item['count_before_request'],item['count_request']):
        assert response(requests,index)=={'count':total} and requests[index]['path']==query(path+'/count')
    assert len(item['list_requests'])==math.ceil(total/500)
    for offset,index in zip(range(0,total,500),item['list_requests'],strict=True):
        page=response(requests,index)
        assert type(page) is list and len(page)==min(500,total-offset)
        assert requests[index]['path']==query(path,{'firstResult':offset,'maxResults':500})
        rows.extend(page);indices.append(index)
    index=item['terminal_request'];indices.extend([index,item['count_request']])
    assert response(requests,index)==[] and requests[index]['path']==query(path,{'firstResult':total,'maxResults':500})
    assert indices==list(range(indices[0],indices[0]+len(indices)))
    assert item['list_request']==(item['list_requests'][0] if total else item['terminal_request'])
    assert rows==item['rows'] and len(rows)==len({r['id'] for r in rows})==total
    return rows


def inventory(snapshot, requests):
    assert set(snapshot['inventories'])==set(INVENTORIES)
    result={}
    for name,item in snapshot['inventories'].items():
        rows=item['rows'];assert type(rows) is list and len(rows)==item['count']
        if item.get('format')=='counted-pages-v1':
            result[name]=paged_inventory(item,requests,INVENTORIES[name]);continue
        assert response(requests,item['list_request'])==rows
        assert response(requests,item['count_request'])=={'count':len(rows)}
        assert requests[item['list_request']]['path']==INVENTORIES[name]+'?maxResults=1000'
        assert requests[item['count_request']]['path']==INVENTORIES[name]+'/count'
        assert item['count_request']==item['list_request']+1
        assert len({r['id'] for r in rows})==len(rows),'Duplicate inventory identity'
        result[name]=rows
    return result


def check_snapshot(snapshot, expected, requests, baseline_jobs, batch_ids):
    """Check the whole engine population, including earlier-case controls."""
    data=inventory(snapshot,requests)
    assert {p['id'] for p in data['history']}==set(expected),'Unknown or missing process history'
    active={pid for pid,e in expected.items() if not e['completed']}
    assert {p['id'] for p in data['instances']}==active,'Wrong active population'
    assert Counter(s['processInstanceId'] for s in data['subscriptions'])==Counter({pid:1 for pid in active})
    assert not data['incidents'] and not data['batches']
    assert {b['id'] for b in data['historic_batches']}==set(batch_ids)
    assert all(b.get('endTime') for b in data['historic_batches'])
    assert {j['id'] for j in data['jobs']}==baseline_jobs,'Unfinished or unexpected jobs'
    assert all(r['processInstanceId'] in expected for kind in ('activities','variables') for r in data[kind])
    histories={r['id']:r for r in data['history']};runtime={r['id']:r for r in data['instances']}
    for pid,e in expected.items():
        h=histories[pid]
        assert h['processDefinitionId']==e['definition_id'] and h['businessKey']==e['business_key']
        assert h['tenantId']==e['tenant'] and bool(h['endTime'])==e['completed'] and not h.get('deleteReason')
        if e['completed']:
            assert h['state']=='COMPLETED' and datetime.fromisoformat(h['endTime'])>=datetime.fromisoformat(h['startTime'])
        else:
            p=runtime[pid]
            assert p['definitionId']==e['definition_id'] and p['businessKey']==e['business_key']
            assert p['tenantId']==e['tenant'] and p['suspended'] is e['suspended']
            sub=next(s for s in data['subscriptions'] if s['processInstanceId']==pid)
            assert sub['eventType']==e['kind'] and sub['eventName']==e['event'] and sub['activityId']=='waiting'
            assert sub['tenantId']==e['tenant']
        activities=[a for a in data['activities'] if a['processInstanceId']==pid]
        assert Counter(a['activityId'] for a in activities)==Counter({'start':1,'waiting':1,**({'received':1} if e['completed'] else {})})
        assert all(a['processDefinitionId']==e['definition_id'] for a in activities)
        assert bool(next(a for a in activities if a['activityId']=='waiting')['endTime'])==e['completed']
        assert all(a['endTime'] for a in activities if a['activityId'] in ('start','received'))
        rows=[v for v in data['variables'] if v['processInstanceId']==pid]
        values={'caseId':e['case_id'],'alias':e['alias'],'sentinel':e['prefix']+'-'+e['alias']}
        if e['delivered']: values['delivery']=e['prefix']+'-delivered'
        assert len(rows)==len(values) and {v['name']:v['value'] for v in rows}==values,'Wrong or unexpected variable delivery'
        assert all(v['type']=='String' and v['tenantId']==e['tenant'] for v in rows)
    return data


def intended_request(value, record, before):
    """Resolve frozen input tokens independently of the collector."""
    if isinstance(value,dict):return {k:intended_request(v,record,before) for k,v in value.items()}
    if isinstance(value,list):return [intended_request(v,record,before) for v in value]
    if not isinstance(value,str) or not value.startswith('@'):return value
    if value=='@delivery':return record['prefix']+'-delivered'
    if value=='@missing_execution':return record['prefix']+'-missing-execution'
    kind,token=value[1:].split(':',1)
    if kind in ('key','event'):return record['prefix']+'-'+kind+'-'+token
    if kind=='tenant':return record['tenants'][token]
    if kind=='pid':return record['processes'][token]['id']
    assert kind=='execution'
    matches=[s for s in before['subscriptions'] if s['processInstanceId']==record['processes'][token]['id']]
    assert len(matches)==1
    return matches[0]['executionId']


def check_model(folder, item, case, fixture, prefix, tenant, requests):
    definition=item['definition'];key='bench_fnv06_'+case['id']+'_'+fixture['event']
    assert definition['key']==key and definition['tenantId']==tenant
    path=folder/item['model_file'];assert path.resolve().is_relative_to(folder.resolve()) and sha(path)==item['model_sha256']
    tree=ET.fromstring(path.read_bytes());process=tree.find('{'+BPMN+'}process')
    assert process.attrib['id']==key and process.attrib['isExecutable']=='true'
    named=tree.find('{'+BPMN+'}'+case['kind'])
    assert named.attrib=={'id':'event_definition','name':prefix+'-event-'+fixture['event']}
    assert len([e for e in tree if e.tag=='{'+BPMN+'}process'])==1
    nodes={e.attrib['id']:e for e in process}
    assert set(nodes)=={'start','waiting','received','start_wait','wait_end'}
    assert nodes['start'].tag=='{'+BPMN+'}startEvent' and len(nodes['start'])==0
    assert nodes['received'].tag=='{'+BPMN+'}endEvent' and len(nodes['received'])==0
    assert nodes['waiting'].tag=='{'+BPMN+'}intermediateCatchEvent' and len(nodes['waiting'])==1
    event=list(nodes['waiting'])[0]
    assert event.tag=='{'+BPMN+'}'+case['kind']+'EventDefinition' and list(event.attrib.values())==['event_definition']
    assert {(nodes[k].attrib['sourceRef'],nodes[k].attrib['targetRef']) for k in ('start_wait','wait_end')}=={('start','waiting'),('waiting','received')}
    deployment=response(requests,item['deployment_request'],'POST')
    assert list(deployment['deployedProcessDefinitions'].values())==[definition]
    assert deployment['id']==definition['deploymentId']
    xml=response(requests,item['xml_request']);assert xml['id']==definition['id'] and xml['bpmn20Xml'].encode()==path.read_bytes()


def check_batch(entry, requests, members):
    created=entry['request']['response'];batch=entry['batch'];assert batch and batch['observations']
    definitions={created[k] for k in ('batchJobDefinitionId','seedJobDefinitionId','monitorJobDefinitionId')}
    assert len(definitions)==3
    assert type(created['invocationsPerBatchJob']) is int and created['invocationsPerBatchJob']>0
    assert created['totalJobs']==math.ceil(len(members)/created['invocationsPerBatchJob'])
    for observation in batch['observations']:
        runtime=response(requests,observation['runtime_request']);history=response(requests,observation['history_request'])
        assert all(r['id']==created['id'] for r in runtime+history)
        assert requests[observation['runtime_request']]['path']=='/batch?batchId='+created['id']
        assert requests[observation['history_request']]['path']=='/history/batch?batchId='+created['id']
        assert not response(requests,observation['incident_request'])
    last=batch['observations'][-1];assert not response(requests,last['runtime_request'])
    assert response(requests,last['history_request'])==[batch['final_history']]
    history=batch['final_history'];assert history['id']==created['id'] and history['endTime']
    assert datetime.fromisoformat(history['endTime'])>=datetime.fromisoformat(history['startTime'])
    assert all(history[k]==created[k] for k in ('type','totalJobs','invocationsPerBatchJob','batchJobDefinitionId','seedJobDefinitionId','monitorJobDefinitionId'))
    assert len(batch['job_logs'])==3
    logs=[];queried=set()
    for row in batch['job_logs']:
        if row.get('format')=='counted-pages-v1':
            definition=row['parameters'].get('jobDefinitionId')
            assert definition in definitions and definition not in queried;queried.add(definition)
            entries=paged_inventory(row,requests,'/history/job-log',{'jobDefinitionId':definition})
            assert all(r['jobDefinitionId']==definition for r in entries)
            logs.extend(entries);continue
        assert row==requests[row['index']] and row['method']=='GET' and row['status']==200
        matching=[d for d in definitions if row['path']=='/history/job-log?jobDefinitionId='+d+'&maxResults=1000']
        assert len(matching)==1 and matching[0] not in queried;queried.add(matching[0])
        assert all(r['jobDefinitionId']==matching[0] for r in row['response'])
        logs.extend(row['response'])
    assert logs and all(r['jobDefinitionId'] in definitions and not r['failureLog'] for r in logs)
    succeeded=[r for r in logs if r['jobDefinitionId']==created['batchJobDefinitionId'] and r['successLog']]
    assert len(succeeded)==len({r['jobId'] for r in succeeded})==created['totalJobs'],'Missing or duplicate batch-job completion'
    return created['id']


def audit(folder):
    folder=folder.resolve();seal=read(folder/'checksums.json')
    actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() and p.name!='checksums.json'}
    assert actual==set(seal['files'])
    for name,digest in seal['files'].items():
        path=folder/name;assert path.resolve().is_relative_to(folder) and not path.is_symlink() and sha(path)==digest
    manifest=read(folder/'manifest.json');summary=read(folder/'summary.json');corpus=read(folder/'inputs/workloads/fnv06/cases.json')
    assert summary['status']=='collected_pending_independent_audit' and summary['owned_engine_stopped'] and not summary['cleanup_errors']
    assert manifest['study_id']==summary['study_id']==corpus['study_id']=='FNV-06'
    assert manifest['run_id']==summary['run_id'] and not manifest['model_calls'] and not summary['model_calls']
    assert not summary['performance_comparison_complete'] and not manifest['performance_comparison_complete']
    assert type(manifest['cloud_resources_accessed']) is bool
    cloud = manifest['cloud_resources_accessed']
    if cloud:
        from scripts.audit_fnv06_cloud import check_runtime, check_database, instant
        identity=read(folder/'identity.json'); runtime=read(folder/'runtime.json')
        window=check_runtime(manifest,summary,identity,runtime,read(folder/'isolation.json'),
                             read(folder/'engine-closeout.json'),read(folder/'source-manifest.json'))
        assert read(folder/'engine-version.json')==runtime['engine']
        assert sha(folder/'inputs/workloads/fnv06/cases.json')=='f85df6c9b7521dfc67b7ef062d8a5e37202932c8dfd87bfd1da2b65707f7c5fd'
    else:
        assert summary['cloud_resources_accessed'] is False
        assert read(folder/'engine-version.json')['version']==manifest['runtime']['release']=='3.0.1'
        assert manifest['runtime']['distribution_sha256']=='ced4311c541faf1a7248d88e37ba53bd8c659fefe8387d2f78c21c30a117d7cf'
    for name,digest in manifest['source_sha256'].items():assert sha(folder/'inputs'/name)==digest
    requests=[json.loads(line) for line in (folder/'requests.ndjson').read_text().splitlines()]
    assert all(r['index']==i and r['status'] is not None and r['elapsed_s']>=0 for i,r in enumerate(requests))
    baseline=inventory(read(folder/'baseline.json'),requests)
    assert all(not rows for name,rows in baseline.items() if name!='jobs')
    baseline_jobs={j['id'] for j in baseline['jobs']};expected={};batch_ids=[];rows=[];operations=0
    assert [c['id'] for c in corpus['cases']]==summary['cases_collected']
    assert len(corpus['cases'])==12 and len(set(summary['cases_collected']))==12
    for case in corpus['cases']:
        record=read(folder/'cases'/(case['id']+'.json'));assert record['case']==case
        prefix='fnv06-'+manifest['run_id'][:12]+'-'+case['id'];assert record['prefix']==prefix
        assert record['tenants']=={role:'fnv06'+manifest['run_id'][:12]+role for role in ('alpha','beta')}
        assert set(record['processes'])==set(case['instances']) and len(record['steps'])==len(case['steps'])
        models={m['definition']['id']:m for m in record['models']};assert len(models)==len(record['models'])
        assert set(models)=={p['definition']['id'] for p in record['processes'].values()}
        for alias,fixture in case['instances'].items():
            p=record['processes'][alias];pid=p['id'];assert pid not in expected
            tenant=record['tenants'].get(fixture['tenant']);definition=p['definition']
            assert definition==models[definition['id']]['definition']
            check_model(folder,models[definition['id']],case,fixture,prefix,tenant,requests)
            body={'businessKey':prefix+'-key-'+fixture['key'],'variables':{k:{'value':v,'type':'String'} for k,v in
                {'caseId':case['id'],'alias':alias,'sentinel':prefix+'-'+alias}.items()}}
            assert p['input']==body
            start=requests[p['start_request']]
            assert start['method']=='POST' and start['path']=='/process-definition/'+definition['id']+'/start' and start['body']==body
            assert start['status']==200 and start['response']['id']==pid and start['response']['definitionId']==definition['id']
            expected[pid]={'case_id':case['id'],'alias':alias,'prefix':prefix,'definition_id':definition['id'],
                'tenant':tenant,'kind':case['kind'],'event':prefix+'-event-'+fixture['event'],'business_key':body['businessKey'],
                'completed':False,'delivered':False,'suspended':False}
        before=check_snapshot(record['before'],expected,requests,baseline_jobs,batch_ids)
        before_indices=[v[k] for v in record['before']['inventories'].values() for k in ('list_request','count_request')]
        assert min(before_indices)>max(p['start_request'] for p in record['processes'].values())
        previous_snapshot_end=max(before_indices)
        for spec,entry in zip(case['steps'],record['steps'],strict=True):
            request=entry['request'];assert request==requests[request['index']]
            assert request['index']>previous_snapshot_end
            assert request['method']=='POST' and request['path']==spec['path'] and request['status']==spec['http_status']
            assert request['body']==intended_request(spec['body'],record,before),'Request differs from frozen original input'
            previous={pid for pid,e in expected.items() if e['completed']}
            for alias,p in record['processes'].items():
                for key in ('completed','delivered','suspended'):
                    assert len(spec[key])==len(set(spec[key])) and set(spec[key])<=set(case['instances'])
                    expected[p['id']][key]=alias in spec[key]
            if spec.get('batch'):
                batch_id=check_batch(entry,requests,request['body']['processInstanceIds']);assert batch_id not in batch_ids;batch_ids.append(batch_id)
            else:assert entry['batch'] is None
            if spec['path']=='/message' and spec['http_status']==200:
                completed={pid for pid,e in expected.items() if e['completed']}-previous
                results=request['response'];assert len(results)==len(completed)
                assert all(r['resultType']=='Execution' for r in results)
                assert {r['execution']['processInstanceId'] for r in results}==completed
            check_snapshot(entry['after'],expected,requests,baseline_jobs,batch_ids)
            after_indices=[v[k] for v in entry['after']['inventories'].values() for k in ('list_request','count_request')]
            assert min(after_indices)>request['index'];previous_snapshot_end=max(after_indices);operations+=1
        rows.append({'id':case['id'],'status':'passed','instances':len(case['instances']),
                     'completed':len(case['steps'][-1]['completed']),'operations':len(case['steps'])})
    before_cleanup=check_snapshot(read(folder/'before-cleanup.json'),expected,requests,baseline_jobs,batch_ids)
    assert set(summary['owned_process_ids'])==set(expected) and len(summary['owned_process_ids'])==len(expected)==42
    assert summary['owned_batch_ids']==batch_ids and len(batch_ids)==3
    waiting={pid for pid,e in expected.items() if not e['completed']}
    cleanup_requests=requests
    database_audit=None
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
    assert len(deletions)==len(waiting) and all(r['status']==204 for r in deletions)
    assert {r['path'].split('?')[0].rsplit('/',1)[1] for r in deletions}==waiting
    counts=read(folder/'final-engine-counts.json')
    assert set(counts)=={'/process-instance','/event-subscription','/incident','/external-task','/batch'} and all(v==0 for v in counts.values())
    for path in counts:
        count_rows=[r for r in cleanup_requests if r['method']=='GET' and r['path']==path+'/count']
        assert count_rows and count_rows[-1]['status']==200 and count_rows[-1]['response']=={'count':0}
        assert count_rows[-1]['index']>max(r['index'] for r in deletions)
    assert all(sha(folder/name)==digest for name,digest in seal['files'].items())
    return {'status':'passed','study_id':'FNV-06','scope':manifest['scope'] if cloud else corpus['scope'],'source_checksums_sha256':sha(folder/'checksums.json'),
        'auditor_sha256':sha(Path(__file__)),'sealed_files_verified':len(actual),'case_families':len(rows),'cases':rows,
        'cloud_auditor_sha256':sha(Path(__file__).with_name('audit_fnv06_cloud.py')) if cloud else None,
        'workflow_instances':len(expected),'functional_operations':operations,'completed_event_transitions':sum(e['completed'] for e in expected.values()),
        'waiting_controls_before_cleanup':len(waiting),'asynchronous_batches':len(batch_ids),
        'unintended_transitions':0,'duplicate_end_events':0,'owned_engine_stopped':True,
        'cloud_resources_accessed':cloud,'model_calls':0,'performance_comparison_complete':False,
        'database_audit':database_audit,
        'evidence_boundary':('Retained REST evidence and independent read-only PostgreSQL final-state reconciliation; intermediate states remain REST-observed. No workload-performance result.' if cloud else
                            'Retained REST request journal, complete counted inventories and deployed XML; not an independent SQL/H2 store audit or a workload-performance result.')}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(args.source.resolve()):p.error('Use a fresh output outside sealed evidence.')
    result=audit(args.source);args.output.mkdir(parents=True);(args.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
