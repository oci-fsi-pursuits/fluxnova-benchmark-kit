from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from benchlib.cloud import online_preflight, provision, terraform_variables
from benchlib.util import read_json, write_json, ROOT

class Models:
    def __getattr__(self, _):
        return lambda **kw: SimpleNamespace(**kw)

class CloudTests(unittest.TestCase):
    def test_postgresql_discovery_uses_service_shape_id(self):
        for selected, valid in (("PostgreSQL.VM.Standard.E5.Flex", True), ("VM.Standard.E5.Flex", False)):
            with self.subTest(selected=selected):
                results = [SimpleNamespace(data=[SimpleNamespace(shape="VM.Standard.E5.Flex")]),
                           SimpleNamespace(data=SimpleNamespace(items=[SimpleNamespace(
                               id="PostgreSQL.VM.Standard.E5.Flex", shape="VM.Standard.E5.Flex")]))]
                sdk = SimpleNamespace(pagination=SimpleNamespace(list_call_get_all_results=Mock(side_effect=results)),
                                      util=SimpleNamespace(to_dict=lambda value: {}))
                config = {"campaign": {"compartment_id": "test"},
                          "oci": {"availability_domain": "test-ad", "db_shape": selected}}
                with patch("benchlib.cloud.sdk_client", return_value=(sdk, Mock())):
                    if valid:
                        self.assertEqual(online_preflight(config)["compute_shape"], "VM.Standard.E5.Flex")
                    else:
                        with self.assertRaisesRegex(RuntimeError, "PostgreSQL shape"):
                            online_preflight(config)

    def test_saved_plan_is_applied_without_duplicate_stack(self):
        cfg = {"campaign": {"region": "us-ashburn-1", "compartment_id": "ocid1.compartment.oc1..test"}}
        state = {"stack_id": "stack", "plan_job_id": "plan", "plan_status": "SUCCEEDED", "region": cfg['campaign']['region'], "compartment_id": cfg['campaign']['compartment_id']}
        sdk = SimpleNamespace(resource_manager=SimpleNamespace(models=Models()))
        client = Mock()
        client.create_job.return_value.data.id = "apply"
        client.get_job_tf_state.return_value.data.content = b'{"outputs":{"environment_contract":{"value":{"cloud":"oci"}}}}'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'state.json'
            write_json(path, state)
            with patch('benchlib.cloud.sdk_client', return_value=(sdk, client)), patch('benchlib.cloud.wait_job'):
                result = provision(cfg, path, apply=True)
            self.assertEqual(result['apply_status'], 'SUCCEEDED')
            client.create_stack.assert_not_called()
            self.assertEqual(client.create_job.call_args.args[0].apply_job_plan_resolution.plan_job_id, 'plan')
            self.assertEqual(read_json(Path(temp)/'environment.local.json'), {'cloud':'oci'})

    def test_cross_compartment_resume_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'state.json'
            write_json(path, {'region':'a','compartment_id':'b'})
            sdk = SimpleNamespace(resource_manager=SimpleNamespace(models=Models()))
            with patch('benchlib.cloud.sdk_client', return_value=(sdk, Mock())):
                with self.assertRaises(ValueError):
                    provision({'campaign':{'region':'a','compartment_id':'different'}}, path, apply=True)

    def test_template_variable_override_cannot_bypass_scope(self):
        cfg = read_json(ROOT/'configs/oci.example.json')
        cfg['oci']['terraform_variables'] = {'compartment_id':'other'}
        with self.assertRaises(ValueError):
            terraform_variables(cfg)

if __name__ == '__main__': unittest.main()
