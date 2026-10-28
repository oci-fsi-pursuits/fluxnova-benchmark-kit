from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from benchlib.fluxnova import FluxnovaClient, workload_resources


class WorkloadResourceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def definition(self, form):
        path = self.root / 'review.bpmn'
        path.write_text('<definitions xmlns:c="http://camunda.org/schema/1.0/bpmn">'
                        '<process><userTask c:formKey="embedded:deployment:' + form + '"/></process></definitions>')
        return path

    def test_embedded_form_is_included_without_unreferenced_files(self):
        definition = self.definition('forms/review.html')
        form = self.root / 'forms/review.html'
        form.parent.mkdir()
        form.write_text('<form>Payment review</form>')
        (form.parent / 'unrelated.html').write_text('not part of deployment')
        self.assertEqual(set(workload_resources(self.root)), {definition.resolve(), form.resolve()})

    def test_missing_or_parent_form_is_rejected_before_deployment(self):
        for name in ('forms/missing.html', '../outside.html', 'C:/outside.html'):
            with self.subTest(form=name):
                self.definition(name)
                with self.assertRaises(ValueError):
                    workload_resources(self.root)

    def test_multipart_retains_form_path_and_rejects_files_outside_root(self):
        definition = self.definition('forms/review.html')
        form = self.root / 'forms/review.html'
        form.parent.mkdir()
        form.write_bytes(b'<form>Payment review</form>')
        client = FluxnovaClient('http://localhost/engine-rest')
        client.request = Mock(return_value={'id': 'deployment'})
        client.deploy(workload_resources(self.root), resource_root=self.root)
        method, endpoint, body, content_type = client.request.call_args.args
        self.assertEqual((method, endpoint), ('POST', '/deployment/create'))
        self.assertIn(b'filename="forms/review.html"\r\nContent-Type: text/html', body)
        self.assertIn(form.read_bytes(), body)
        self.assertIn(b'filename="review.bpmn"\r\nContent-Type: application/xml', body)
        self.assertTrue(content_type.startswith('multipart/form-data; boundary='))
        with self.assertRaises(ValueError):
            client.deploy([definition], resource_root=form.parent)
