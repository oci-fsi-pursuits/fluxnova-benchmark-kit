import json
import hashlib
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from benchlib.analysis import analyze_run, distribution, merge_histograms, paired_effect_interval, pass_power_k, percentile
from benchlib.report import write_reports, write_parquet


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.write("manifest.json", {"config": {"schema_version": "1.0", "run": {"id": "test", "mode": "fixture"}, "workload": {"scenario": "minimal", "duration_s": 10, "drain_s": 5, "deadline_s": 1}}})
        self.write("k6-summary.json", {"measurement_duration_s": 10, "generator_validated": True, "telemetry_complete": True})
        self.write("costs.json", {"currency": "USD", "complete": False, "records": []})

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding="utf-8")

    def events(self, values):
        (self.root / "events.ndjson").write_text("\n".join(json.dumps(v) for v in values), encoding="utf-8")

    def event(self, kind, ident="r1", elapsed=0, **extra):
        return {"event": kind, "run_id": "test", "logical_request_id": ident, "attempt_id": ident + "-a1", "scenario": "minimal", "elapsed_s": elapsed, **extra}

    def complete(self, ident="r1", latency=0.1, status="correct"):
        return [self.event("scheduled", ident), self.event("started", ident), self.event("accepted", ident, 0.01), self.event("completed", ident, latency, status=status, latency_s=latency)]

    def test_acceptance_is_not_completion_and_unknown_is_in_denominator(self):
        self.events(self.complete() + [self.event("scheduled", "r2"), self.event("started", "r2"), self.event("accepted", "r2", 0.1)])
        result = analyze_run(self.root)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["correctness_fraction"], 0.5)
        self.assertEqual(result["counts"]["right_censored"], 1)
        self.assertEqual(result["outcomes"][1]["censor_lower_bound_s"], 15)
        self.assertEqual(result["latency"]["correct_only"]["n"], 1)

    def test_completed_without_explicit_status_does_not_pass(self):
        self.events([self.event("scheduled"), self.event("started"), self.event("completed", elapsed=0.1)])
        result = analyze_run(self.root)
        self.assertEqual(result["counts"]["correct"], 0)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["counts"]["right_censored"], 0)

    def test_outcome_verification_does_not_add_observer_delay_to_completion(self):
        self.events([self.event("scheduled"), self.event("started"), self.event("completed", elapsed=0.1), self.event("outcome", elapsed=5, status="correct")])
        result = analyze_run(self.root)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["outcomes"][0]["latency_s"], 0.1)
        self.assertEqual(result["outcomes"][0]["verification_delay_s"], 4.9)

    def test_poll_completion_boundary_keeps_oracle_verification_separate(self):
        records = self.complete(latency=.2)
        records[-1]["details"] = {"validation_elapsed_s": 3, "verified_at": "2026-01-01T00:00:03.200Z"}
        self.events(records)
        result = analyze_run(self.root)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["latency"]["correct_only"]["p99"], .2)
        self.assertEqual(result["latency"]["verification_delay"]["p99"], 3)
        self.assertEqual(result["outcomes"][0]["verification_delay_s"], 3)
        paths = write_reports(self.root)
        self.assertIn("verification_delay_s", Path(paths["outcomes_csv"]).read_text())

    def test_safety_failure_is_sticky_across_successful_retry(self):
        records = self.complete()
        records.insert(2, self.event("error", elapsed=0.02, status="incorrect", details={"safety_violation": True}))
        self.events(records)
        result = analyze_run(self.root)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["counts"]["correct"], 0)
        self.assertEqual(result["counts"]["unsafe_outcomes"], 1)

    def test_duplicate_attempts_are_not_duplicate_goodput(self):
        records = self.complete()
        records.insert(2, self.event("started", elapsed=0.001, attempt_id="r1-a2"))
        records.append(self.event("completed", elapsed=0.2, status="correct", attempt_id="r1-a2"))
        self.events(records)
        result = analyze_run(self.root)
        self.assertEqual(result["counts"]["offered"], 1)
        self.assertEqual(result["counts"]["correct"], 1)
        self.assertEqual(result["counts"]["duplicate_attempts"], 1)
        self.assertEqual(result["rates"]["deadline_goodput_per_s"], 0.1)

    def test_warmup_is_excluded_and_drain_completion_included(self):
        records = [dict(e, phase="warmup") for e in self.complete("warm")]
        records += [self.event("scheduled", elapsed=9), self.event("started", elapsed=9), self.event("completed", elapsed=10.1, phase="drain", status="correct", latency_s=1.1)]
        self.events(records)
        result = analyze_run(self.root)
        self.assertEqual(result["counts"]["offered"], 1)
        self.assertEqual(result["counts"]["correct"], 1)
        self.assertEqual(result["counts"]["deadline_correct"], 0)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["backlog"]["measurement_end_outstanding_started"], 1)
        self.assertEqual(result["backlog"]["oldest_outstanding_started_age_s"], 1)

    def test_summary_drops_never_disappear_from_denominator(self):
        self.events(self.complete())
        self.write("k6-summary.json", {"scheduled": 3, "started": 1, "dropped": 2, "measurement_duration_s": 10, "generator_validated": True, "telemetry_complete": True})
        result = analyze_run(self.root)
        self.assertEqual(result["correctness_fraction"], 1 / 3)
        self.assertEqual(result["counts"]["dropped"], 2)
        self.assertEqual(result["status"], "failed")

    def test_extra_boundary_iteration_is_invalid_and_never_erased(self):
        records = []
        for index in range(11):
            records.extend(self.complete(str(index)))
        self.events(records)
        self.write("k6-summary.json", {"scheduled": 10, "started": 11, "dropped": 0, "measurement_duration_s": 10,
                                       "generator_validated": True, "telemetry_complete": True})
        result = analyze_run(self.root)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["counts"]["correct"], 11)
        self.assertEqual(result["counts"]["offered"], 11)
        self.assertTrue(any("smaller than observed" in error for error in result["errors"]))

    def test_missing_telemetry_cannot_pass(self):
        self.events(self.complete())
        self.write("k6-summary.json", {"generator_validated": True})
        self.assertEqual(analyze_run(self.root)["status"], "inconclusive")

    def test_corrupt_or_cross_run_evidence_is_invalid(self):
        self.events([dict(self.event("scheduled"), run_id="other")])
        self.assertEqual(analyze_run(self.root)["status"], "invalid")

    def test_cost_retains_failure_spend_and_zero_denominator_is_undefined(self):
        self.events(self.complete(status="incorrect"))
        self.write("costs.json", {"currency": "USD", "complete": True, "records": [{"category": "compute", "amount": 8, "basis": "effective_actual", "allocation": "measurement"}, {"category": "database", "amount": 2, "basis": "effective_actual", "allocation": "drain"}, {"category": "compute", "amount": 3, "basis": "effective_actual", "allocation": "deployment"}]})
        cost = analyze_run(self.root)["cost"]
        self.assertEqual(cost["by_basis"]["effective_actual"]["total_spend"], 13)
        self.assertIsNone(cost["by_basis"]["effective_actual"]["per_1000_correct"])
        self.events(self.complete())
        cost = analyze_run(self.root)["cost"]
        self.assertEqual(cost["by_basis"]["effective_actual"]["per_1000_correct"], 10_000)

    def test_estimate_and_incomplete_actual_cost_are_not_combined(self):
        self.events(self.complete())
        self.write("costs.json", {"currency": "USD", "complete": False, "records": [{"amount": 2, "basis": "list_estimate", "allocation": "measurement"}, {"amount": 1, "basis": "effective_actual", "allocation": "measurement"}]})
        cost = analyze_run(self.root)["cost"]["by_basis"]
        self.assertEqual(cost["list_estimate"]["per_1000_correct"], 2000)
        self.assertIsNone(cost["effective_actual"]["per_1000_correct"])

    def test_reports_are_deterministic_and_always_label_fixtures(self):
        self.events(self.complete())
        paths = write_reports(self.root)
        before = {name: Path(path).read_bytes() for name, path in paths.items()}
        write_reports(self.root)
        for name, path in paths.items():
            data = Path(path).read_bytes()
            self.assertEqual(data, before[name])
            self.assertIn(b"SYNTHETIC HARNESS VALIDATION", data)
        self.assertNotIn("parquet", " ".join(paths))

    def test_tampered_manifest_or_lifecycle_invalidates_report(self):
        self.events(self.complete())
        self.write("checksums.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir() if p.is_file()})
        self.assertTrue(analyze_run(self.root)["evidence_integrity"]["verified"])
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["config"]["workload"]["deadline_s"] = 999
        self.write("manifest.json", manifest)
        result = analyze_run(self.root)
        self.assertEqual(result["status"], "invalid")
        self.assertIn("changed file: manifest.json", result["evidence_integrity"]["mismatches"])

    def test_local_functional_is_never_labeled_cloud_performance(self):
        self.events(self.complete())
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["config"]["run"]["mode"] = "local-functional"
        self.write("manifest.json", manifest)
        result = analyze_run(self.root)
        self.assertEqual(result["mode"], "local-functional")
        self.assertEqual(result["evidence_label"], "LOCAL FUNCTIONAL VALIDATION — NOT OCI PERFORMANCE")
        self.assertEqual(result["status"], "inconclusive")

    def test_parquet_missing_dependency_cannot_claim_saved_file(self):
        self.events(self.complete())
        target = self.root / "outcomes.parquet"
        with patch.dict("sys.modules", {"pyarrow": None}):
            with self.assertRaisesRegex(RuntimeError, "no Parquet file was written"):
                write_parquet(self.root, target)
        self.assertFalse(target.exists())

    def test_live_run_needs_golden_gate_even_when_requests_and_telemetry_pass(self):
        self.events(self.complete())
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["config"]["run"]["mode"] = "live"
        self.write("manifest.json", manifest)
        self.write("checksums.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir() if p.is_file()})
        result = analyze_run(self.root)
        self.assertEqual(result["status"], "inconclusive")
        self.assertTrue(any("golden correctness" in message for message in result["missing_evidence"]))


class StatisticsTests(unittest.TestCase):
    def test_quantiles_use_observations_not_average_percentiles(self):
        self.assertEqual(percentile([1] * 99 + [100], 0.95), 1)
        merged = merge_histograms([{"bounds": [1, 10], "counts": [99, 0, 0]}, {"bounds": [1, 10], "counts": [0, 0, 1]}])
        self.assertEqual(merged["counts"], [99, 0, 1])
        with self.assertRaises(ValueError):
            merge_histograms([{"bounds": [1], "counts": [1, 0]}, {"bounds": [2], "counts": [1, 0]}])

    def test_p99_9_needs_declared_tail_support(self):
        self.assertIsNone(distribution([1] * 100)["p99_9"])
        self.assertEqual(distribution([1] * 100_000)["p99_9"], 1)

    def test_pass_power_is_all_success_not_any_success(self):
        result = pass_power_k({"one": [True] * 5, "two": [True, False, True, True, True], "short": [True]})
        self.assertEqual(result["value"], 0.5)
        self.assertEqual(result["insufficient_trial_tasks"], 1)

    def test_paired_effects_are_seeded_and_require_independent_pairs(self):
        first = paired_effect_interval([1, 2, 3, 4, 5], [2, 4, 6, 8, 10], resamples=100)
        self.assertEqual(first["relative_effect"], 1)
        self.assertEqual(first["ci95_low"], 1)
        self.assertEqual(first, paired_effect_interval([1, 2, 3, 4, 5], [2, 4, 6, 8, 10], resamples=100))
        with self.assertRaises(ValueError):
            paired_effect_interval([1], [2])


if __name__ == "__main__":
    unittest.main()
