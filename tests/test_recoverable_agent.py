import json
from pathlib import Path
import tempfile
import time
import unittest
import urllib.parse

from services.payment_agent import AgentStopped
from services.recoverable_agent import Journal, RecoverableTools, ReplayModel, digest, owner


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.folder=Path(temp.name)
        self.context={'wall_s':30,'payment':{'authorized':True}}
        self.journal=Journal(self.folder,self.context)

    def test_atomic_checkpoint_retains_identity_and_detects_changed_context(self):
        self.journal.data['model_requests']=2;self.journal.save()
        restored=Journal(self.folder,self.context,resume=True)
        self.assertEqual(restored.data['request_id'],self.journal.data['request_id'])
        self.assertEqual(restored.data['model_requests'],2)
        with self.assertRaisesRegex(ValueError,'context_mismatch'):
            Journal(self.folder,{**self.context,'payment':{'authorized':False}},resume=True)
        self.assertEqual(list(self.folder.glob('checkpoint.json.*')),[])

    def test_corrupt_checkpoint_cannot_be_used_as_fresh_state(self):
        self.journal.path.write_text('{torn')
        with self.assertRaises(json.JSONDecodeError):
            Journal(self.folder,self.context,resume=True)

    def test_only_one_process_owner(self):
        with owner(self.folder):
            with self.assertRaises(OSError):
                with owner(self.folder):self.fail('overlapping owner')
        with owner(self.folder):pass

    def test_completed_model_response_replays_without_request_or_new_budget(self):
        calls=[]
        def call(body,deadline):calls.append(body);return {'final':True,'tool_calls':[],'output':'ignored'}
        body={'turn':1,'tool_results':[]}
        first=ReplayModel(self.journal,call)(body,time.monotonic()+10)
        self.journal.data['action_deadline_utc_s']=0;self.journal.save()
        restored=Journal(self.folder,self.context,resume=True)
        second=ReplayModel(restored,call)(body,time.monotonic()+10)
        self.assertEqual(first,second);self.assertEqual(len(calls),1)
        self.assertEqual(restored.data['model_requests'],1)
        with self.assertRaisesRegex(ValueError,'replay_context_mismatch'):
            ReplayModel(restored,call)({'turn':2},time.monotonic()+10)

    def test_lost_model_responses_cannot_reset_request_budget(self):
        calls=[]
        def failed(body,deadline):calls.append(body);raise ConnectionError()
        for _ in range(6):
            restored=Journal(self.folder,self.context,resume=True)
            with self.assertRaises(ConnectionError):
                ReplayModel(restored,failed)({'turn':0},time.monotonic()+10)
        with self.assertRaisesRegex(AgentStopped,'durable_model_request_budget'):
            ReplayModel(Journal(self.folder,self.context,resume=True),failed)({'turn':0},time.monotonic()+10)
        self.assertEqual(len(calls),6)

    def test_original_deadline_blocks_new_model_request(self):
        self.journal.data['action_deadline_utc_s']=0;self.journal.save()
        with self.assertRaisesRegex(AgentStopped,'original_action_deadline'):
            ReplayModel(self.journal,lambda *args:self.fail('new action'))({'turn':0},time.monotonic()+10)

    def start_body(self):
        return {'businessKey':self.journal.data['request_id']+':settlement','variables':{}}

    def pending_tools(self,matches):
        body=self.start_body()
        self.journal.data['workflows']['settlement']={'intent':True,'body_sha256':digest(body)}
        self.journal.save()
        calls=[]
        class Engine:
            def request(self,method,path,value=None):
                query=urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
                if query.get('processInstanceBusinessKey') != [body['businessKey']]:
                    raise AssertionError('historic API requires processInstanceBusinessKey')
                calls.append((method,path));return matches
        engine=Engine()
        return RecoverableTools(engine,None,self.journal,lambda event:None),engine,calls

    def test_lost_start_response_resolves_exact_instance_without_post(self):
        key=self.start_body()['businessKey']
        tools,engine,calls=self.pending_tools([{'id':'existing','businessKey':key,'processDefinitionKey':'bench_settlement'}])
        self.journal.data['action_deadline_utc_s']=0
        instance=tools.request(engine,'POST','/process-definition/key/bench_settlement/start',self.start_body(),time.monotonic()+10)
        self.assertEqual(instance,{'id':'existing'})
        self.assertEqual([method for method,path in calls],['GET'])
        self.assertEqual(Journal(self.folder,self.context,resume=True).data['workflows']['settlement']['process_instance_id'],'existing')

    def test_absent_or_ambiguous_start_is_never_repeated(self):
        key=self.start_body()['businessKey']
        row={'id':'existing','businessKey':key,'processDefinitionKey':'bench_settlement'}
        for matches in ([],[row,row],[{**row,'processDefinitionKey':'other'}]):
            tools,engine,calls=self.pending_tools(matches)
            with self.assertRaisesRegex(AgentStopped,'unresolved_workflow_start'):
                tools.request(engine,'POST','/process-definition/key/bench_settlement/start',self.start_body(),time.monotonic()+10)
            self.assertEqual([method for method,path in calls],['GET'])

    def test_changed_workflow_input_is_not_reused(self):
        tools,engine,calls=self.pending_tools([])
        with self.assertRaisesRegex(ValueError,'workflow_replay_context_mismatch'):
            tools.request(engine,'POST','/process-definition/key/bench_settlement/start',{**self.start_body(),'variables':{'changed':True}},time.monotonic()+10)
        self.assertEqual(calls,[])
