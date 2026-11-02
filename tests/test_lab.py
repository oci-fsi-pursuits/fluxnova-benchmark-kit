import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from benchlib.lab import Engine, EXAMPLES, Lab, make_server
from benchlib.lab_model import parse_model, parameters, variables
from scripts import build_public_candidate


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.live_tasks = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == '/version':
            return {'version': 'test'}
        if method == 'GET' and path.startswith('/task?'):
            return self.live_tasks

    def deploy(self, model):
        return {'id': 'own-' + model['key'], 'deployedProcessDefinitions': {'x': {'id': model['key'] + ':1', 'key': model['key']}}}


class LabModelTests(unittest.TestCase):
    def setUp(self):
        self.xml = (EXAMPLES / 'approval.bpmn').read_text(encoding='utf-8')

    def test_all_starters_are_isolated_and_in_public_release(self):
        policy = build_public_candidate.load_policy()
        for file in EXAMPLES.glob('*.bpmn'):
            model = parse_model(file.read_text(), 'isolated_key')
            self.assertIn(b'id="isolated_key"', model['xml'])
            self.assertEqual(model['key'], 'isolated_key')
            self.assertNotIn(b'topic="lab-demo"', model['xml'])
            self.assertTrue(build_public_candidate.included(file.relative_to(build_public_candidate.ROOT).as_posix(), policy))
        for file in ('providers/oci/main.tf', 'providers/oci/cloud-init.yaml', 'benchlib/lab_assets/app.js'):
            self.assertTrue(build_public_candidate.included(file, policy))

    def test_upload_rejects_code_and_expression_injection(self):
        for xml in (
            '<!DOCTYPE definitions [<!ENTITY a "x">]>' + self.xml,
            self.xml.replace('<userTask ', '<scriptTask '),
            self.xml.replace('name="Review the request"', 'camunda:class="Exploit"'),
            self.xml.replace('name="Review the request"', 'name="${runtime.exec(123)}"'),
            self.xml.replace('name="Review the request"', 'name="&#36;{runtime.exec(123)}"'),
            self.xml.replace('${approved == true}', '${runtime.exec("evil")}'),
            self.xml.replace('${approved == true}', '${execution.processEngineServices}'),
            self.xml.replace('camunda:historyTimeToLive="1"', 'camunda:executionListener="x"'),
            self.xml.replace('sourceRef="decision" targetRef="rejected"', 'sourceRef="decision" targetRef="review"'),
            self.xml + 'x',
        ):
            with self.subTest(xml=xml[:50]), self.assertRaises(ValueError):
                parse_model(xml, 'isolation')

    def test_variables_and_bounds_do_not_allow_objects_or_unbounded_load(self):
        model = parse_model(self.xml, 'x')
        for values in ({'x': {'object': 1}}, {'x': [1]}, {'bad-name': True}, {'x': float('nan')}, {'x': 2**64}):
            with self.assertRaises(ValueError):
                variables(values)
        self.assertEqual(variables({'approved': True})['approved']['type'], 'Boolean')
        for values in ({'count': 21}, {'poll_ms': 0}, {'count': True}, {'deadline_s': 601}, {'expected_end': 'missing'}):
            with self.assertRaises(ValueError):
                parameters(values, model)

    def test_remote_plaintext_and_embedded_credentials_are_rejected(self):
        for url in ('http://example.org/engine-rest', 'https://u:p@example.org/engine-rest', 'https://example.org/rest?token=abc'):
            with self.assertRaises(ValueError):
                Engine(url)
        Engine('http://127.0.0.1:8080/engine-rest')


class LabOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = FakeEngine()
        self.lab = Lab(self.engine, self.temp.name)
        self.xml = (EXAMPLES / 'approval.bpmn').read_text()

    def tearDown(self):
        self.temp.cleanup()

    def test_readonly_connection_never_deploys(self):
        self.lab.writable = False
        with self.assertRaisesRegex(ValueError, 'read-only'):
            self.lab.upload(self.xml)
        self.assertEqual(self.lab.deployments, [])

    def test_cleanup_targets_only_recorded_deployments(self):
        model = self.lab.upload(self.xml)
        self.lab.close()
        deletes = [p for m, p, _ in self.engine.calls if m == 'DELETE']
        self.assertEqual(deletes, ['/deployment/own-' + model['key'] + '?cascade=true&skipCustomListeners=true&skipIoMappings=true'])
        self.assertEqual(json.loads((Path(self.temp.name) / 'cleanup.local.json').read_text())['remaining_deployment_ids'], [])

    def test_task_cannot_escape_owned_active_instance(self):
        model = self.lab.upload(self.xml)
        self.lab.runs = [{'model': model['id'], 'status': 'running', 'instances': [{'id': 'owned', 'status': 'running'}]}]
        self.engine.live_tasks = [{'id': 'my-task'}]
        for data in ({'instance': 'unrelated', 'task': 'my-task'}, {'instance': 'owned', 'task': 'other-task'}):
            with self.assertRaises(ValueError):
                self.lab.complete(data)
        self.assertFalse(any(m == 'POST' for m, _, _ in self.engine.calls))
        self.lab.complete({'instance': 'owned', 'task': 'my-task', 'variables': {'approved': True}})
        self.assertEqual(self.engine.calls[-1][1], '/task/my-task/complete')


class LabHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.lab = Lab(FakeEngine(), self.temp.name)
        self.server = make_server(self.lab, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.token = json.load(urllib.request.urlopen(self.url + '/api/state'))['token']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def test_cross_origin_and_dns_rebinding_are_rejected(self):
        headers = {'Content-Type': 'application/json', 'X-Lab-Token': self.token, 'Origin': self.url}
        for changed in ({'Origin': 'https://attacker.example'}, {'X-Lab-Token': 'wrong'}, {'Host': 'attacker.example'}, {'Content-Type': 'text/plain'}):
            req = urllib.request.Request(self.url + '/api/stop', data=b'{}', headers=dict(headers, **changed))
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(req)
            self.assertEqual(caught.exception.code, 403)
        self.assertFalse(self.lab.stop.is_set())
        with urllib.request.urlopen(urllib.request.Request(self.url + '/api/stop', data=b'{}', headers=headers)) as response:
            self.assertEqual(response.status, 200)
        self.assertTrue(self.lab.stop.is_set())

    def test_static_routes_cannot_read_local_files(self):
        for path in ('/../lab.py', '/api/connection', '/style.css/../../lab.py'):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.url + path)
            self.assertEqual(caught.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
