"""Recipient, original-input and batch accounting failures must remain failures."""
import copy
import unittest

from benchlib.fnv06_functional import render_model, resolve
from scripts.audit_fnv06_functional import check_snapshot, intended_request, check_batch, INVENTORIES


def fixture():
    expected={}
    data={k:[] for k in ('instances','history','activities','variables','subscriptions','incidents','jobs','batches','historic_batches')}
    for alias,complete in (('a',True),('b',False)):
        pid='pid-'+alias;prefix='fnv06-fixture-M01'
        expected[pid]={'case_id':'M01','alias':alias,'prefix':prefix,'definition_id':'definition',
            'business_key':'key-'+alias,'tenant':None,'kind':'message','event':'event',
            'completed':complete,'delivered':complete,'suspended':False}
        data['history'].append({'id':pid,'processDefinitionId':'definition','businessKey':'key-'+alias,'tenantId':None,
            'startTime':'2026-11-01T00:00:00+00:00','endTime':'2026-11-01T00:00:01+00:00' if complete else None,
            'state':'COMPLETED' if complete else 'ACTIVE','deleteReason':None})
        if not complete:
            data['instances'].append({'id':pid,'definitionId':'definition','businessKey':'key-'+alias,'tenantId':None,'suspended':False})
            data['subscriptions'].append({'id':'subscription-'+alias,'processInstanceId':pid,'eventType':'message',
                'eventName':'event','activityId':'waiting','executionId':'execution-'+alias,'tenantId':None})
        for activity in ('start','waiting')+(('received',) if complete else ()):
            data['activities'].append({'id':pid+'-'+activity,'processInstanceId':pid,'processDefinitionId':'definition',
                'activityId':activity,'endTime':'2026-11-01T00:00:01+00:00' if complete or activity=='start' else None})
        values={'caseId':'M01','alias':alias,'sentinel':prefix+'-'+alias}
        if complete:values['delivery']=prefix+'-delivered'
        for name,value in values.items():
            data['variables'].append({'id':pid+'-'+name,'processInstanceId':pid,'name':name,'value':value,'type':'String','tenantId':None})
    return expected,data


def bind(data):
    snapshot={'inventories':{}};requests=[]
    for name,rows in data.items():
        index=len(requests)
        requests.extend([{'index':index,'method':'GET','path':INVENTORIES[name]+'?maxResults=1000','status':200,'response':copy.deepcopy(rows)},
            {'index':index+1,'method':'GET','path':INVENTORIES[name]+'/count','status':200,'response':{'count':len(rows)}}])
        snapshot['inventories'][name]={'rows':copy.deepcopy(rows),'count':len(rows),'list_request':index,'count_request':index+1}
    return snapshot,requests


def batch_fixture():
    created={'id':'batch','type':'correlate-message','batchJobDefinitionId':'work','seedJobDefinitionId':'seed',
             'monitorJobDefinitionId':'monitor','invocationsPerBatchJob':1,'totalJobs':2}
    history=dict(created,startTime='2026-11-01T00:00:00+00:00',endTime='2026-11-01T00:00:01+00:00')
    requests=[]
    for path,body in [('/batch?batchId=batch',[]),('/history/batch?batchId=batch',[history]),('/incident',[])]:
        requests.append({'index':len(requests),'method':'GET','path':path,'status':200,'response':body})
    logs=[]
    for definition in ('work','seed','monitor'):
        rows=[{'jobId':definition+'-'+str(i),'jobDefinitionId':definition,'successLog':True,'failureLog':False}
              for i in range(2 if definition=='work' else 1)]
        row={'index':len(requests),'method':'GET','path':'/history/job-log?jobDefinitionId='+definition+'&maxResults=1000','status':200,'response':rows}
        requests.append(row);logs.append(row)
    entry={'request':{'response':created},'batch':{'observations':[{'runtime_request':0,'history_request':1,'incident_request':2}],
            'final_history':history,'job_logs':logs}}
    return entry,requests,['one','two']


class Fnv06AuditTests(unittest.TestCase):
    def check(self,expected,data):
        snapshot,requests=bind(data)
        return check_snapshot(snapshot,expected,requests,set(),[])

    def test_expected_transition_and_untouched_control_pass(self):
        expected,data=fixture();self.check(expected,data)

    def test_delivery_to_wrong_recipient_rejected_even_if_http_succeeded(self):
        expected,data=fixture()
        data['variables'].append({'id':'extra','processInstanceId':'pid-b','name':'delivery','value':'fnv06-fixture-M01-delivered','type':'String','tenantId':None})
        with self.assertRaises(AssertionError):self.check(expected,data)

    def test_unintended_completion_cannot_be_hidden_by_correct_total(self):
        expected,data=fixture()
        for row in data['history']:row['id']='pid-b' if row['id']=='pid-a' else 'pid-a'
        with self.assertRaises(AssertionError):self.check(expected,data)

    def test_duplicate_end_event_rejected_even_with_resealed_inventory(self):
        expected,data=fixture();row=copy.deepcopy(data['activities'][2]);row['id']='duplicate-end';data['activities'].append(row)
        with self.assertRaises(AssertionError):self.check(expected,data)

    def test_extra_process_missing_subscription_and_changed_control_rejected(self):
        for changed in ('process','subscription','variable','suspension','tenant'):
            expected,data=fixture()
            if changed=='process':row=copy.deepcopy(data['history'][0]);row['id']='unexpected';data['history'].append(row)
            if changed=='subscription':data['subscriptions']=[]
            if changed=='variable':data['variables'][-1]['value']='tampered'
            if changed=='suspension':data['instances'][0]['suspended']=True
            if changed=='tenant':data['subscriptions'][0]['tenantId']='another-tenant'
            with self.subTest(changed=changed),self.assertRaises(AssertionError):self.check(expected,data)

    def test_incomplete_or_unbound_inventory_rejected(self):
        expected,data=fixture();snapshot,requests=bind(data)
        snapshot['inventories']['history']['count']-=1
        with self.assertRaises(AssertionError):check_snapshot(snapshot,expected,requests,set(),[])
        snapshot,requests=bind(data);requests[0]['response']=[]
        with self.assertRaises(AssertionError):check_snapshot(snapshot,expected,requests,set(),[])

    def test_active_batch_incident_or_job_prevents_pass(self):
        for kind in ('incidents','batches','jobs'):
            expected,data=fixture();data[kind]=[{'id':'unfinished'}]
            with self.subTest(kind=kind),self.assertRaises(AssertionError):self.check(expected,data)

    def test_equal_empty_lists_cannot_substitute_another_endpoint(self):
        expected,data=fixture();snapshot,requests=bind(data)
        index=snapshot['inventories']['incidents']['list_request']
        requests[index]['path']='/batch?maxResults=1000'
        with self.assertRaises(AssertionError):check_snapshot(snapshot,expected,requests,set(),[])

    def test_target_execution_is_derived_from_original_subscription(self):
        record={'prefix':'case','tenants':{'alpha':'tenant-a'},'processes':{'a':{'id':'pid-a'}}}
        before={'subscriptions':[{'processInstanceId':'pid-a','executionId':'expected-execution'}]}
        self.assertEqual(intended_request({'executionId':'@execution:a','tenantId':'@tenant:alpha'},record,before),
                         {'executionId':'expected-execution','tenantId':'tenant-a'})
        before['subscriptions'].append(dict(before['subscriptions'][0]))
        with self.assertRaises(AssertionError):intended_request('@execution:a',record,before)

    def test_runtime_resolver_refuses_unknown_or_ambiguous_targets(self):
        context={'prefix':'case','processes':{'a':{'id':'pid'}},'before':{'inventories':{'subscriptions':{'rows':[]}}}}
        for value in ('@unknown:a','@execution:a'):
            with self.subTest(value=value),self.assertRaises(ValueError):resolve(value,context)

    def test_batch_creation_receipt_is_not_completion(self):
        created={'id':'batch','batchJobDefinitionId':'work','seedJobDefinitionId':'seed','monitorJobDefinitionId':'monitor',
                 'invocationsPerBatchJob':1,'totalJobs':2}
        with self.assertRaises(AssertionError):check_batch({'request':{'response':created},'batch':{'observations':[]}},[],['one','two'])

    def test_batch_requires_terminal_history_and_distinct_successful_jobs(self):
        self.assertEqual(check_batch(*batch_fixture()),'batch')

    def test_missing_duplicate_failed_or_misbound_batch_job_cannot_pass(self):
        for changed in ('missing','duplicate','failure','history','endpoint','still_active'):
            entry,requests,members=batch_fixture()
            if changed=='missing':requests[3]['response'].pop()
            if changed=='duplicate':requests[3]['response'][1]['jobId']=requests[3]['response'][0]['jobId']
            if changed=='failure':requests[3]['response'][0]['failureLog']=True
            if changed=='history':entry['batch']['final_history']['type']='another-operation'
            if changed=='endpoint':requests[4]['path']=requests[3]['path']
            if changed=='still_active':requests[0]['response']=[{'id':'batch'}]
            with self.subTest(changed=changed),self.assertRaises(AssertionError):check_batch(entry,requests,members)

    def test_model_names_cannot_inject_xml(self):
        for key,event in [('bad<key','event'),('key','event"'),('key','../event')]:
            with self.subTest(key=key,event=event),self.assertRaises(ValueError):render_model(b'',key,event)


if __name__=='__main__':unittest.main()
