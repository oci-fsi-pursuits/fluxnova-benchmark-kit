import json
from pathlib import Path
import tempfile
import unittest
from benchlib.runner import create_run, demo_run, extract_event, verify_archive
from benchlib.preflight import check
from benchlib.util import ROOT, read_json
from benchlib.campaign import planned_runs

class RunnerTests(unittest.TestCase):
    def test_extract_k6_json_preserves_evidence(self):
        event = {"event": "completed", "logical_request_id": "abc", "status": "unknown"}
        self.assertEqual(extract_event(json.dumps({"msg": "BENCH_EVENT " + json.dumps(event)})), event)
        self.assertIsNone(extract_event('{"msg":"not a benchmark event"}'))

    def test_missing_cloud_inputs_block(self):
        config = read_json(ROOT / "configs/oci.example.json")
        errors = check(config)
        self.assertTrue(any("region" in e for e in errors))
        self.assertTrue(any("budget" in e for e in errors))
        self.assertTrue(any("compartment" in e for e in errors))

    def test_manifest_cannot_overwrite_and_tamper_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            run = demo_run(folder)
            self.assertEqual(verify_archive(run), [])
            with self.assertRaises(FileExistsError):
                create_run({}, run.name, folder)
            (run / "events.ndjson").write_text("", encoding="utf-8")
            self.assertIn("events.ndjson", verify_archive(run))

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                create_run({}, "../outside", folder)

    def test_campaign_profiles_bounded(self):
        config = read_json(ROOT / "configs/oci.example.json")
        self.assertEqual([c['workload']['rate'] for c in planned_runs(config, 'discovery')], [1,2,5,10,20,50,100])
        self.assertEqual(len(list(planned_runs(config, 'baseline'))), 5)
        self.assertEqual(next(planned_runs(config, 'acceptance'))['workload']['duration_s'], 86400)

if __name__ == '__main__':
    unittest.main()
