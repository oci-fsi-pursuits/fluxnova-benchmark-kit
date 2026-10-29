"""Campaign control tests use fake runners; they are not measured results."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchlib.campaign import execute_campaign, planned_runs


class CapacityCampaignTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "campaign": {"discovery_rates": [5, 10, 15, 20, 25, 30, 40, 50]},
            "workload": {"scenario": "async", "rate": 5, "duration_s": 300,
                         "warmup_s": 120, "drain_s": 60, "deadline_s": 2,
                         "poll_interval_ms": 100},
            "estimated_cost": {"duration_hours": 3},
        }

    def test_explicit_ladder_keeps_observation_policy_and_original_config(self):
        original = copy.deepcopy(self.config)
        runs = list(planned_runs(self.config, "discovery"))
        self.assertEqual([r["workload"]["rate"] for r in runs], [5, 10, 15, 20, 25, 30, 40, 50])
        self.assertTrue(all(r["workload"]["poll_interval_ms"] == 100 for r in runs))
        runs[0]["workload"]["poll_interval_ms"] = 999
        self.assertEqual(runs[1]["workload"]["poll_interval_ms"], 100)
        self.assertEqual(self.config, original)

    def test_malformed_late_rate_blocks_all_load(self):
        for bad in ([5, 10, True], [5, 10, 9], [5, 10, 10], [5, 10, 1001],
                    [5, 10, 20.0], [], "5,10", list(range(1, 18)), [0, 5]):
            with self.subTest(rates=bad), tempfile.TemporaryDirectory() as folder:
                self.config["campaign"]["discovery_rates"] = bad
                with patch("benchlib.campaign.run_k6") as run:
                    with self.assertRaises(ValueError):
                        execute_campaign(self.config, "discovery", folder)
                    run.assert_not_called()
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_unknown_profile_cannot_become_a_day_long_acceptance_run(self):
        with tempfile.TemporaryDirectory() as folder, patch("benchlib.campaign.run_k6") as run:
            with self.assertRaisesRegex(ValueError, "Unknown campaign"):
                execute_campaign(self.config, "soka", folder)
            run.assert_not_called()
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_duration_budget_covers_every_planned_step_before_start(self):
        self.config["estimated_cost"]["duration_hours"] = .01
        with tempfile.TemporaryDirectory() as folder, patch("benchlib.campaign.run_k6") as run:
            with self.assertRaisesRegex(ValueError, "full campaign"):
                execute_campaign(self.config, "discovery", folder)
            run.assert_not_called()

    def fake_runner(self, config, run_id, output_root):
        # The plan and current attempt must be saved before the external runner.
        records = list(Path(output_root).glob("campaign-*.json"))
        self.assertEqual(len(records), 1)
        saved = json.loads(records[0].read_text())
        self.assertEqual(saved["current_attempt"]["run_id"], run_id)
        self.assertEqual(saved["current_attempt"]["rate"], config["workload"]["rate"])
        return Path(output_root) / run_id

    def test_nonfinite_or_boolean_budget_duration_cannot_authorize_load(self):
        for duration in (float("nan"), float("inf"), True):
            with self.subTest(duration=duration), tempfile.TemporaryDirectory() as folder:
                self.config["estimated_cost"]["duration_hours"] = duration
                with patch("benchlib.campaign.run_k6") as run:
                    with self.assertRaisesRegex(ValueError, "full campaign"):
                        execute_campaign(self.config, "discovery", folder)
                    run.assert_not_called()

    def test_each_nonpassing_status_stops_escalation_and_keeps_the_bad_rate(self):
        for status in ("failed", "invalid", "inconclusive"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder:
                with patch("benchlib.campaign.run_k6", side_effect=self.fake_runner) as run, \
                     patch("benchlib.campaign.write_reports"), \
                     patch("benchlib.campaign.analyze_run", side_effect=[{"status": "pass"}, {"status": status}]):
                    result = execute_campaign(self.config, "discovery", folder)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(result["last_valid_rate"], 5)
                self.assertEqual(result["results"][1]["status"], status)
                self.assertEqual(result["unattempted_rates"], [15, 20, 25, 30, 40, 50])
                self.assertIn("not established", result["capacity_claim"])

    def test_interruption_retains_current_identity_without_retry_or_false_success(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("benchlib.campaign.run_k6", side_effect=[Path(folder)/"first", KeyboardInterrupt()]) as run, \
                 patch("benchlib.campaign.write_reports"), \
                 patch("benchlib.campaign.analyze_run", return_value={"status": "pass"}):
                with self.assertRaises(KeyboardInterrupt):
                    execute_campaign(self.config, "discovery", folder)
            saved = json.loads(next(Path(folder).glob("campaign-*.json")).read_text())
            self.assertEqual(run.call_count, 2)
            self.assertEqual(saved["status"], "interrupted")
            self.assertEqual(saved["current_attempt"]["rate"], 10)
            self.assertEqual(saved["last_valid_rate"], 5)
            self.assertEqual(saved["unattempted_rates"], [15, 20, 25, 30, 40, 50])

    def test_report_failure_preserves_attempt_and_does_not_leak_exception_text(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("benchlib.campaign.run_k6", side_effect=self.fake_runner) as run, \
                 patch("benchlib.campaign.write_reports", side_effect=RuntimeError("private diagnostic")):
                with self.assertRaises(RuntimeError):
                    execute_campaign(self.config, "discovery", folder)
            saved_text = next(Path(folder).glob("campaign-*.json")).read_text()
            saved = json.loads(saved_text)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(saved["status"], "error")
            self.assertEqual(saved["current_attempt"]["rate"], 5)
            self.assertEqual(saved["error_type"], "RuntimeError")
            self.assertNotIn("private diagnostic", saved_text)
            self.assertIsNone(saved["last_valid_rate"])

    def test_passing_local_trials_never_become_a_cloud_capacity_claim(self):
        self.config["campaign"]["discovery_rates"] = [5, 10]
        with tempfile.TemporaryDirectory() as folder:
            with patch("benchlib.campaign.run_k6", side_effect=self.fake_runner), \
                 patch("benchlib.campaign.write_reports"), \
                 patch("benchlib.campaign.analyze_run", return_value={"status": "pass"}):
                result = execute_campaign(self.config, "discovery", folder, local=True)
            self.assertEqual(result["status"], "completed")
            self.assertIsNone(result["last_valid_rate"])
            self.assertEqual(result["unattempted_rates"], [])
            self.assertEqual(result["scope"], "local-functional")


if __name__ == "__main__":
    unittest.main()
