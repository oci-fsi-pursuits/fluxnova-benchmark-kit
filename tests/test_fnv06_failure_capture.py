"""A timeout remains a failure even when later state can be retained."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from benchlib.fnv06_load_qualification import exercise
from scripts.run_fnv06_cloud import Cell,save,read

ROOT=Path(__file__).resolve().parents[1]


class FailureCaptureTests(unittest.TestCase):
    def exercise_failure(self,driver,*,capture_fails=False):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory);inputs=output/'inputs/workloads/fnv06';inputs.mkdir(parents=True)
            (inputs/'message.bpmn').write_bytes((ROOT/'workloads/fnv06/message.bpmn').read_bytes())
            sequence=[];starts=[]
            def snapshot():
                sequence.append('snapshot')
                if capture_fails and len(sequence)==3:raise TimeoutError('state unavailable')
                return {'retained':len(sequence)}
            def call(method,path,body):
                starts.append(body);return {'status':200,'response':{'id':'pid'+str(len(starts))},'index':len(starts)}
            api=SimpleNamespace(output=output,snapshot=snapshot,call=call)
            protocol=json.loads((ROOT/'workloads/fnv06/load-qualification.json').read_text())
            protocol['observations']=protocol['observations'][:1]
            case=protocol['observations'][0]['id'];summary={'owned_process_ids':[],'owned_batch_ids':[],'cases_collected':[]}
            with patch('benchlib.fnv06_load_qualification.deploy',return_value={'definition':{'id':'fixture'}}),patch('benchlib.fnv06_load_qualification.run_driver',side_effect=driver):
                with self.assertRaises((RuntimeError,TimeoutError)) as caught:
                    exercise(api,protocol,'a'*32,'user',summary,Path('unused'))
            record=read(output/'observations'/case/'observation.json')
            self.assertEqual(summary['cases_collected'],[])
            self.assertEqual(len(summary['owned_process_ids']),3)
            self.assertEqual(record['load_failure'],summary['failed_observation'])
            self.assertEqual(sequence,['snapshot']*3)
            return record,type(caught.exception)

    def test_timed_out_request_retains_later_state_and_still_fails(self):
        record,error=self.exercise_failure(lambda *a,**k:[{'status':0,'error_code':1050}])
        self.assertEqual(error,RuntimeError)
        self.assertEqual(record['after'],{'retained':3})
        self.assertTrue(record['load_failure']['snapshot_available'])
        self.assertEqual(record['load_failure']['phase'],'request_result')

    def test_drain_timeout_retains_state_and_original_failure_type(self):
        record,error=self.exercise_failure(TimeoutError('recipient drain expired'))
        self.assertEqual(error,TimeoutError)
        self.assertTrue(record['load_failure']['snapshot_available'])
        self.assertEqual(record['load_failure']['phase'],'dispatch_or_drain')

    def test_missing_final_state_is_explicit_and_cannot_become_a_pass(self):
        record,error=self.exercise_failure(lambda *a,**k:[],capture_fails=True)
        self.assertEqual(error,RuntimeError)
        self.assertNotIn('after',record)
        self.assertFalse(record['load_failure']['snapshot_available'])
        self.assertEqual(record['load_failure']['state_capture_error_type'],'TimeoutError')

    def cloud_failure(self,snapshot_fails):
        with tempfile.TemporaryDirectory() as directory:
            steps=[]
            class FaultCell(Cell):
                def __init__(self):
                    self.stage=Path(directory);self.output=self.stage/'evidence';self.output.mkdir()
                    self.names={'job':'owned-job'};self.run_id='a'*32
                    save(self.output/'summary.json',{'status':'failed','owned_process_ids':['owned'],'owned_batch_ids':[],'cases_collected':[]})
                def bound(self):pass
                def setup(self):pass
                def docker(self,*a):pass
                def inspect(self,*a):return {'State':{'Running':False,'ExitCode':1,'OOMKilled':False}}
                def snapshot_database(self):
                    steps.append('sql')
                    if snapshot_fails:raise TimeoutError('unavailable')
                    save(self.output/'database-before-cleanup.json',{'retained':'before cleanup'})
                def cleanup_workflows(self):steps.append('workflow cleanup')
                def cleanup(self):
                    steps.append('container cleanup')
                    return {'owned_engine_stopped':True,'cleanup_errors':[]}
            cell=FaultCell()
            with self.assertRaisesRegex(AssertionError,'qualification_job_failed'):cell.run()
            self.assertEqual(steps,['sql','workflow cleanup','container cleanup'])
            self.assertEqual(read(cell.output/'summary.json')['status'],'failed')
            self.assertEqual(read(cell.stage/'failure.json')['reason'],'qualification_job_failed')
            self.assertIn('failed-job-capture.json',read(cell.output/'checksums.json')['files'])
            return read(cell.output/'failed-job-capture.json')

    def test_failed_cloud_job_captures_sql_before_cleanup_and_remains_failed(self):
        result=self.cloud_failure(False)
        self.assertTrue(result['job_failed'] and result['database_snapshot_available'] and result['owned_workflow_cleanup_complete'])
        self.assertEqual(result['errors'],[])

    def test_unavailable_sql_is_reported_and_owned_cleanup_still_runs(self):
        result=self.cloud_failure(True)
        self.assertFalse(result['database_snapshot_available'])
        self.assertTrue(result['owned_workflow_cleanup_complete'])
        self.assertEqual(result['errors'],[{'phase':'database_snapshot','error_type':'TimeoutError'}])


if __name__=='__main__':unittest.main()
