import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchlib.compare import compare_runs, _gate
from benchlib.cli import parser, main


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def seal(self, path):
        self.write(path / "checksums.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir() if p.is_file() and p.name != "checksums.json"})

    def run_record(self, lane, pair, latency=1, mode="live", cpu=4, factors=None, design=True):
        # Synthetic unit-test data only, disposed with TemporaryDirectory. The
        # mode parameter exercises provenance classification; not a saved result.
        path = self.root / f"{lane}-{pair}"
        path.mkdir()
        config = {"run": {"id": path.name, "mode": mode}, "campaign": {"region": "us-ashburn-1"},
                  "artifact": {"release": "test", "image": "example.invalid/runtime@sha256:" + "a" * 64},
                  "runtime": {"cpu": cpu, "memory_gib": 8, "heap_gib": 4},
                  "workload": {"scenario": "minimal", "rate": 0.1, "seed": 42, "duration_s": 10, "drain_s": 15, "deadline_s": 10, "payload_bytes": 1, "warmup_s": 0}}
        if design:
            config["comparison"] = {"pair_id": f"pair-{pair}", "pair_order": "AB" if pair % 2 else "BA", "randomized_order": True,
                                    "dataset_sha256": "b" * 64, "state_fingerprint": "c" * 64,
                                    "experimental_factors": factors or [], "reset_evidence_file": "reset.json",
                                    "environment": {"cpu_arch": "amd64", "cpu_model": "test", "topology": "vm", "database_product": "postgresql", "database_version": "17", "durability": "synchronous", "network_path": "same-vcn"}}
            self.write(path / "reset.json", {"run_id": path.name, "success": True, "dataset_sha256": "b" * 64, "state_fingerprint": "c" * 64})
        self.write(path / "manifest.json", {"config": config, "source_hashes": {"load/benchmark.js": "d" * 64}})
        common = {"run_id": path.name, "logical_request_id": "request", "attempt_id": "attempt", "scenario": "minimal", "phase": "measurement"}
        records = [{**common, "event": "scheduled", "elapsed_s": 0}, {**common, "event": "started", "elapsed_s": 0},
                   {**common, "event": "completed", "elapsed_s": latency, "latency_s": latency, "status": "correct"}]
        (path / "events.ndjson").write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        self.write(path / "k6-summary.json", {"generator_validated": True, "telemetry_complete": True, "golden_validated": True, "scheduled": 1, "started": 1, "dropped": 0, "measurement_duration_s": 10})
        self.write(path / "costs.json", {"currency": "USD", "complete": False, "records": []})
        self.seal(path)
        return path

    def lanes(self, latency=1.05, **kwargs):
        a = [self.run_record("a", i, **kwargs) for i in range(5)]
        b = [self.run_record("b", i, latency=latency if isinstance(latency, (int, float)) else latency[i], **kwargs) for i in range(5)]
        return a, b

    def test_actual_analysis_fields_produce_gate_pass(self):
        a, b = self.lanes()
        result = compare_runs(a, b, self.root / "comparison")
        self.assertEqual(result["status"], "pass", result)
        self.assertAlmostEqual(result["metrics"]["p95_s"]["interval"]["relative_effect"], 0.05)
        self.assertEqual(result["metrics"]["deadline_goodput_per_s"]["verdict"], "pass")
        self.assertIn("not sustainable-throughput", " ".join(result["notes"]))

    def test_latency_regression_really_fails(self):
        a, b = self.lanes(latency=1.2)
        result = compare_runs(a, b, self.root / "comparison")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["metrics"]["p95_s"]["verdict"], "failed")
        self.assertEqual(result["metrics"]["p99_s"]["verdict"], "failed")

    def test_ci_straddling_margin_is_inconclusive(self):
        a, b = self.lanes(latency=[1, 1, 1.3, 1.3, 1.3])
        result = compare_runs(a, b, self.root / "comparison")
        self.assertEqual(result["status"], "inconclusive")

    def test_goodput_gate_direction_and_boundary(self):
        self.assertEqual(_gate({"ci95_low": -0.10, "ci95_high": -0.06}, .05, "decrease_is_worse"), "failed")
        self.assertEqual(_gate({"ci95_low": -0.05, "ci95_high": 0}, .05, "decrease_is_worse"), "pass")
        self.assertEqual(_gate({"ci95_low": -0.10, "ci95_high": 0}, .05, "decrease_is_worse"), "inconclusive")
        self.assertEqual(_gate({"ci95_low": .10, "ci95_high": .10000000000000009}, .10, "increase_is_worse"), "pass")

    def test_reusing_runs_cannot_manufacture_repetitions(self):
        a, b = self.lanes()
        with self.assertRaises(ValueError):
            compare_runs([a[0]] * 5, b, self.root / "comparison")
        with self.assertRaises(ValueError):
            compare_runs(a, a, self.root / "comparison")

    def test_missing_design_does_not_pass(self):
        a, b = self.lanes(design=False)
        result = compare_runs(a, b, self.root / "comparison")
        self.assertEqual(result["status"], "inconclusive")
        self.assertTrue(any("state_fingerprint" in x for x in result["missing_evidence"]))

    def test_state_mismatch_is_invalid_even_when_performance_is_good(self):
        a, b = self.lanes()
        manifest = json.loads((b[0] / "manifest.json").read_text())
        manifest["config"]["comparison"]["state_fingerprint"] = "e" * 64
        self.write(b[0] / "manifest.json", manifest)
        self.seal(b[0])
        result = compare_runs(a, b, self.root / "comparison")
        self.assertEqual(result["status"], "invalid")

    def test_declared_environment_factor_allowed_but_undeclared_change_invalid(self):
        a = [self.run_record("a", i, cpu=4, factors=["runtime.cpu"]) for i in range(5)]
        b = [self.run_record("b", i, cpu=8, factors=["runtime.cpu"]) for i in range(5)]
        self.assertEqual(compare_runs(a, b, self.root / "comparison")["status"], "pass")
        for path in a + b:
            manifest = json.loads((path / "manifest.json").read_text())
            manifest["config"]["comparison"]["experimental_factors"] = []
            self.write(path / "manifest.json", manifest)
            self.seal(path)
        self.assertEqual(compare_runs(a, b, self.root / "comparison")["status"], "invalid")

    def test_fixture_comparison_never_claims_live_performance(self):
        a, b = self.lanes(mode="fixture")
        result = compare_runs(a, b, self.root / "comparison")
        self.assertEqual(result["status"], "inconclusive")
        self.assertIn("SYNTHETIC HARNESS VALIDATION", result["evidence_label"])

    def test_reset_evidence_tampering_is_invalid(self):
        a, b = self.lanes()
        (b[0] / "reset.json").write_text("{}")
        self.assertEqual(compare_runs(a, b, self.root / "comparison")["status"], "invalid")

    def test_cli_parquet_flag_and_unavailable_dependency(self):
        args = parser().parse_args(["report", "run-dir", "--parquet"])
        self.assertTrue(args.parquet)
        run = self.run_record("a", 0, mode="fixture")
        with patch.dict("sys.modules", {"pyarrow": None}):
            self.assertEqual(main(["report", str(run), "--parquet"]), 2)
        self.assertTrue((run / "reports" / "analysis.json").is_file())
        self.assertFalse((run / "reports" / "outcomes.parquet").exists())


if __name__ == "__main__":
    unittest.main()
