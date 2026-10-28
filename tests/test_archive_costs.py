import csv
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from benchlib.archive import archive_run
from benchlib.costs import import_costs
from benchlib.runner import demo_run
from benchlib.util import read_json

class EvidenceTests(unittest.TestCase):
    def test_transient_upload_failure_can_retry_same_immutable_local_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = demo_run(root / "runs")
            destination = root / "run.zip"
            config = {"campaign": {"name": "campaign"}, "oci": {"artifact_namespace": "namespace", "artifact_bucket_name": "bucket"}}
            client = Mock()
            client.put_object.side_effect = [TimeoutError("temporary upload failure"), SimpleNamespace(headers={"etag": "uploaded"})]
            with patch("benchlib.cloud.sdk_client", return_value=(None, client)):
                with self.assertRaises(TimeoutError):
                    archive_run(run, destination, config)
                before, modified = destination.read_bytes(), destination.stat().st_mtime_ns
                receipt = archive_run(run, destination, config)
            self.assertTrue(receipt["cloud_upload"])
            self.assertEqual(destination.read_bytes(), before)
            self.assertEqual(destination.stat().st_mtime_ns, modified)
            self.assertEqual(client.put_object.call_count, 2)
            self.assertEqual(list(root.glob(".bench-archive-*")), [])

    def test_different_existing_archive_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = demo_run(root / "runs")
            destination = root / "run.zip"
            destination.write_bytes(b"unrelated original artifact")
            with self.assertRaisesRegex(ValueError, "differs"):
                archive_run(run, destination)
            self.assertEqual(destination.read_bytes(), b"unrelated original artifact")
            self.assertEqual(list(root.glob(".bench-archive-*")), [])

    def test_remote_retry_after_lost_response_verifies_existing_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = demo_run(root / "runs")
            destination = root / "run.zip"
            receipt = archive_run(run, destination)
            config = {"campaign": {"name": "campaign"}, "oci": {"artifact_namespace": "namespace", "artifact_bucket_name": "bucket"}}
            client = Mock()
            conflict = RuntimeError("already uploaded")
            conflict.status = 412
            client.put_object.side_effect = conflict
            client.head_object.return_value = SimpleNamespace(headers={"opc-meta-sha256": receipt["sha256"], "content-length": str(receipt["bytes"]), "etag": "previous-upload"})
            with patch("benchlib.cloud.sdk_client", return_value=(None, client)):
                retried = archive_run(run, destination, config)
                self.assertTrue(retried["cloud_upload"])
                client.head_object.return_value.headers["opc-meta-sha256"] = "wrong"
                with self.assertRaisesRegex(ValueError, "does not match"):
                    archive_run(run, destination, config)

    def test_archive_is_reproducible_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = demo_run(root / "runs")
            first = archive_run(run, root / "a.zip")
            second = archive_run(run, root / "b.zip")
            self.assertEqual(first["sha256"], second["sha256"])
            (run / "events.ndjson").write_text("changed")
            with self.assertRaises(ValueError):
                archive_run(run, root / "c.zip")

    def test_costs_preserve_credits_allocations_and_missingness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "billing.csv"
            source.write_text("cost,currency\n10,USD\n-2,USD\n", encoding="utf-8")
            mapping = {"basis": "effective_actual", "columns": {"EffectiveCost": "cost", "BillingCurrency": "currency"},
                       "defaults": {"x_BenchmarkCategory": "application", "x_AllocationFraction": "0.5", "x_AllocationBasis": "Half of metered instance time"}}
            report = import_costs(source, mapping, root / "output")
            self.assertIn("ListCost", report["missing_fields"])
            self.assertEqual((root / "output/source-billing.csv").read_bytes(), source.read_bytes())
            costs = read_json(root / "output/costs.json")
            self.assertEqual(sum(r["amount"] for r in costs["records"]), 4)
            self.assertFalse(costs["complete"])

    def test_mixed_currencies_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "billing.csv"
            source.write_text("cost,currency\n10,USD\n10,EUR\n")
            mapping = {"basis": "list_estimate", "columns": {"ListCost": "cost", "BillingCurrency": "currency"},
                       "defaults": {"x_BenchmarkCategory": "application", "x_AllocationFraction": "1", "x_AllocationBasis": "dedicated"}}
            with self.assertRaises(ValueError):
                import_costs(source, mapping, root / "output")

if __name__ == "__main__": unittest.main()
