import io
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import json
import tempfile
import unittest
import re
from urllib.parse import urlsplit
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from benchlib.evidence import TelemetrySampler, collect_source, linux_host_sample, _prometheus_metrics, calibrate_generator
from benchlib.runner import normalize_event, normalize_summary, run_k6, verify_archive
from benchlib.analysis import analyze_run
from benchlib.contracts import validate_event
from benchlib.campaign import planned_wall_seconds, execute_campaign


class EvidenceCollectorTests(unittest.TestCase):
    def test_calibration_boundary_margin_preserves_the_full_required_volume(self):
        # The retained cloud failure completed 38,399 of the required 38,400.
        # Extra scheduled arrivals must not reduce that minimum or inflate
        # the capacity credited by downstream validators.
        for requests, expected in [(38399,False),(38400,True),(38799,True)]:
            with self.subTest(requests=requests), tempfile.TemporaryDirectory() as temp:
                cfg={'workload':{'rate':15},'evidence':{'calibrate_on_nonlinux':True,
                    'calibration_duration_s':20,'estimated_http_calls_per_workflow':64}}
                def run(argv,**kwargs):
                    script=Path(argv[-1]).read_text()
                    self.assertIn("rate:1940,timeUnit:'1s',duration:'20s'",script)
                    (Path(temp)/'generator-calibration-summary.json').write_text(json.dumps({'metrics':{
                        'http_reqs':{'values':{'count':requests}},'http_req_failed':{'values':{'rate':0}},
                        'dropped_iterations':{'values':{'count':0}}}}))
                    return SimpleNamespace(returncode=0)
                with patch('benchlib.evidence.platform.system',return_value='Linux'), patch('benchlib.evidence.subprocess.run',side_effect=run):
                    result=calibrate_generator(cfg,'boundary',temp)
                self.assertEqual(result['valid'],expected)
                self.assertEqual(result['minimum_completed_requests'],38400)
                self.assertEqual(result['requested_http_rate'],1920)
                self.assertEqual(result['scheduled_http_rate'],1940)

    def test_calibration_stub_reuses_a_connection_with_complete_responses(self):
        with tempfile.TemporaryDirectory() as temp:
            config = {'workload':{'rate':1}, 'evidence':{'calibrate_on_nonlinux':True, 'calibration_duration_s':3}}
            def run(argv, **kwargs):
                script = Path(argv[-1]).read_text()
                url = urlsplit(json.loads(re.search(r'http.get\(("[^"]+")', script).group(1)))
                connection = http.client.HTTPConnection(url.hostname, url.port, timeout=2)
                try:
                    port = None
                    for _ in range(2):
                        connection.request('GET', url.path)
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(json.loads(response.read()), {'ok':True})
                        self.assertIsNotNone(connection.sock, 'The stub closed the connection after one request')
                        current = connection.sock.getsockname()[1]
                        if port is not None:self.assertEqual(current, port)
                        port = current
                finally:connection.close()
                (Path(temp)/'generator-calibration-summary.json').write_text(json.dumps({'metrics':{
                    'http_reqs':{'values':{'count':48}},'http_req_failed':{'values':{'rate':0}}}}))
                return SimpleNamespace(returncode=0)
            with patch('benchlib.evidence.platform.system',return_value='Linux'), patch('benchlib.evidence.subprocess.run',side_effect=run):
                self.assertTrue(calibrate_generator(config, 'keepalive', temp)['valid'])

    def test_calibration_keeps_zero_error_drop_and_offered_volume_gates(self):
        for requests, dropped, errors, code in [(47,0,0,0),(48,1,0,0),(48,0,.001,0),(48,0,0,1)]:
            with self.subTest(requests=requests,dropped=dropped,errors=errors,code=code), tempfile.TemporaryDirectory() as temp:
                def run(*args, **kwargs):
                    (Path(temp)/'generator-calibration-summary.json').write_text(json.dumps({'metrics':{
                        'http_reqs':{'values':{'count':requests}},'http_req_failed':{'values':{'rate':errors}},
                        'dropped_iterations':{'values':{'count':dropped}}}}))
                    return SimpleNamespace(returncode=code)
                cfg={'workload':{'rate':1},'evidence':{'calibrate_on_nonlinux':True,'calibration_duration_s':3}}
                with patch('benchlib.evidence.platform.system',return_value='Linux'), patch('benchlib.evidence.subprocess.run',side_effect=run):
                    self.assertFalse(calibrate_generator(cfg,'failed',temp)['valid'])

    def test_file_metrics_reject_stale_failed_and_future_snapshots(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'snapshot.json'
            source = {'kind': 'file', 'path': str(path), 'required_metrics': ['connections'], 'max_age_s': 30}
            for age, errors, expected in [(1, {}, True), (31, {}, False), (-10, {}, False), (0, {'db': 'failure'}, False), (0, None, False)]:
                observed = datetime.now(timezone.utc) - timedelta(seconds=age)
                path.write_text(json.dumps({'connections': 4, '_meta': {'observed_at': observed.isoformat(), 'errors': errors}}))
                self.assertEqual(collect_source(source)['ok'], expected)
            path.write_text('{"connections":4}')
            self.assertFalse(collect_source(source)['ok'])

    def test_calibration_passes_pinned_k6_image_to_container_wrapper(self):
        with tempfile.TemporaryDirectory() as temp:
            image = "example.invalid/k6@sha256:" + "a" * 64
            config = {"workload": {"rate": 1}, "artifact": {"k6_image": image},
                      "execution": {"k6_binary": "k6-wrapper"}, "evidence": {"calibrate_on_nonlinux": True, "calibration_duration_s": 3}}
            def calibrate(argv, **kwargs):
                self.assertEqual(argv[0], "k6-wrapper")
                self.assertEqual(kwargs["env"]["BENCH_K6_IMAGE"], image)
                (Path(temp) / "generator-calibration-summary.json").write_text(json.dumps({"metrics": {
                    "http_reqs": {"values": {"count": 48}}, "dropped_iterations": {"values": {"count": 0}}, "http_req_failed": {"values": {"rate": 0}}}}))
                return SimpleNamespace(returncode=0)
            with patch("benchlib.evidence.platform.system", return_value="Linux"), patch("benchlib.evidence.subprocess.run", side_effect=calibrate):
                self.assertTrue(calibrate_generator(config, "calibration", Path(temp))["valid"])

    def test_linux_cpu_and_memory_are_read_without_external_dependency(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "stat").write_text("cpu  10 0 5 80 5 0 0 0 0 0\ncpu0 10 0 5 80 5 0 0 0\n")
            (root / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 700 kB\n")
            sample = linux_host_sample(root)
            self.assertEqual(sample["cpu_total_ticks"], 100)
            self.assertEqual(sample["cpu_idle_ticks"], 85)
            self.assertEqual(sample["memory_available_bytes"], 700 * 1024)

    def test_telemetry_requires_actual_named_metrics(self):
        values = _prometheus_metrics('# HELP x value\njvm_memory_used_bytes{area="heap"} 1024\n', ["jvm_memory_used_bytes"])
        self.assertEqual(values, {"jvm_memory_used_bytes": 1024})
        with self.assertRaises(ValueError):
            _prometheus_metrics("up 1\n", ["db_connections"])
        with self.assertRaises(ValueError):
            _prometheus_metrics("up 1\n", [])

    def test_generator_host_samples_alone_never_complete_system_telemetry(self):
        with tempfile.TemporaryDirectory() as temp:
            sampler = TelemetrySampler({}, "run", Path(temp))
            sampler.samples = [{"elapsed_s": i, "generator_host": {"cpu_total_ticks": i * 100, "cpu_idle_ticks": i * 90, "memory_available_bytes": 800, "memory_total_bytes": 1000}, "sources": []} for i in range(3)]
            summary = sampler.summary()
            self.assertTrue(summary["generator_host_valid"])
            self.assertFalse(summary["valid"])

    def test_missing_required_source_and_sampling_gaps_fail_completeness(self):
        with tempfile.TemporaryDirectory() as temp:
            sources = [{"name": "app", "role": "application"}, {"name": "db", "role": "database"}, {"name": "engine", "role": "engine"}]
            sampler = TelemetrySampler({"evidence": {"telemetry_sources": sources}}, "run", Path(temp))
            sampler.samples = [{"elapsed_s": i, "generator_host": None, "sources": [{**s, "ok": True} for s in sources]} for i in range(3)]
            self.assertTrue(sampler.summary()["valid"])
            sampler.samples[1]["sources"][1]["ok"] = False
            self.assertFalse(sampler.summary()["valid"])
            sampler.samples[1]["sources"][1]["ok"] = True
            sampler.samples[-1]["elapsed_s"] = 100
            self.assertFalse(sampler.summary()["valid"])

    def test_readonly_command_metrics_validate_numeric_shape(self):
        source = {"kind": "command", "name": "db", "role": "database", "argv": ["probe"], "format": "json", "required_metrics": ["connections"]}
        with patch("benchlib.evidence.subprocess.run", return_value=SimpleNamespace(stdout='{"connections":4}')):
            self.assertTrue(collect_source(source)["ok"])
        with patch("benchlib.evidence.subprocess.run", return_value=SimpleNamespace(stdout='{"status":"healthy"}')):
            self.assertFalse(collect_source(source)["ok"])


class RunnerIntegrationTests(unittest.TestCase):
    def config(self):
        return {"run": {"mode": "local-functional"}, "runtime": {"base_url": "http://example.invalid"},
                "workload": {"scenario": "minimal", "duration_s": 1, "warmup_s": 0, "rate": 1, "drain_s": 1, "deadline_s": 1},
                "evidence": {"auto_calibrate": False, "auto_collect": False}, "execution": {"k6_binary": "test-k6"}}

    def test_schedule_boundary_is_preserved_and_negative_warmup_is_valid(self):
        event = {"event": "scheduled", "run_id": "test", "logical_request_id": "r1", "scenario": "minimal", "phase": "measurement", "elapsed_s": 0.25, "at": "2026-01-01T00:00:00.250Z", "details": {"scheduled_elapsed_s": 0}}
        fixed = normalize_event(event)
        self.assertEqual(fixed["elapsed_s"], 0)
        self.assertEqual(fixed["details"]["observed_emit_elapsed_s"], .25)
        self.assertTrue(fixed["at"].startswith("2026-01-01T00:00:00+"))
        warmup = {**event, "phase": "warmup", "elapsed_s": -2}
        self.assertEqual(validate_event(warmup), [])
        self.assertTrue(validate_event({**warmup, "phase": "measurement"}))

    def test_nested_phase_summary_does_not_mix_warmup_drops(self):
        raw = {"measurement": {"scheduled": 10, "started": 9, "dropped": 1}, "benchmark": {"phase_schedule": {"measurement_start_s": 10}},
               "metrics": {"dropped_iterations": {"values": {"count": 100}}}}
        flat = normalize_summary(raw, {"rate": 1, "duration_s": 10, "warmup_s": 10})
        self.assertEqual((flat["scheduled"], flat["started"], flat["dropped"]), (10, 9, 1))
        self.assertEqual(flat["phase_schedule"]["measurement_start_s"], 10)

    def test_launch_failure_still_seals_raw_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("benchlib.runner.subprocess.Popen", side_effect=FileNotFoundError("not found")):
                run = run_k6(self.config(), "launch-failed", Path(temp))
            self.assertEqual(verify_archive(run), [])
            self.assertTrue((run / "events.ndjson").exists())
            self.assertEqual(analyze_run(run)["status"], "invalid")
            self.assertIn("FileNotFoundError", json.loads((run / "execution.json").read_text())["problems"])
            window = json.loads((run / "load-window.json").read_text())
            self.assertFalse(window["process_started"])
            self.assertIsNone(window["returncode"])
            self.assertGreaterEqual(datetime.fromisoformat(window["finished_at"]), datetime.fromisoformat(window["started_at"]))

    def test_interruption_seals_end_time_and_preserves_failure(self):
        def interrupted_output():
            raise KeyboardInterrupt("bounded interruption")
            yield "unreachable"
        for already_exited in (False, True):
            with self.subTest(already_exited=already_exited), tempfile.TemporaryDirectory() as temp:
                process = Mock(stdout=interrupted_output())
                process.poll.return_value = -15 if already_exited else None
                process.wait.return_value = -15
                config = self.config()
                config["workload"].update(warmup_s=600, duration_s=1800)
                with patch("benchlib.runner.subprocess.Popen", return_value=process):
                    with self.assertRaises(KeyboardInterrupt):
                        run_k6(config, "interrupted", Path(temp))
                run = Path(temp) / "interrupted"
                window = json.loads((run / "load-window.json").read_text())
                self.assertTrue(window["process_started"])
                self.assertTrue(window["interrupted"])
                self.assertEqual(window["returncode"], -15)
                self.assertEqual(window["error_type"], "KeyboardInterrupt")
                self.assertGreaterEqual(datetime.fromisoformat(window["finished_at"]), datetime.fromisoformat(window["started_at"]))
                self.assertEqual(verify_archive(run), [])
                self.assertEqual(analyze_run(run)["status"], "invalid")
                process.wait.assert_called_once()
                self.assertEqual(process.kill.call_count, 0 if already_exited else 1)

    def test_failure_before_launch_does_not_invent_load_window(self):
        with tempfile.TemporaryDirectory() as temp:
            config = self.config()
            config["evidence"]["auto_calibrate"] = True
            with patch("benchlib.evidence.calibrate_generator", side_effect=RuntimeError("calibration failed")), \
                    patch("benchlib.runner.subprocess.Popen") as launch:
                run = run_k6(config, "before-launch", Path(temp))
            launch.assert_not_called()
            self.assertFalse((run / "load-window.json").exists())
            self.assertEqual(verify_archive(run), [])
            self.assertEqual(analyze_run(run)["status"], "invalid")

    def test_real_k6_envelope_roundtrip_preserves_lag_and_phase_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            config = self.config()
            config['workload']['abort_on_warmup_unknown'] = True
            config['workload']['late_poll_interval_ms'] = 1000
            config["artifact"] = {"k6_image": "example.invalid/k6@sha256:" + "a" * 64}
            def launch(argv, **kwargs):
                self.assertEqual(kwargs['env']['BENCH_ABORT_ON_WARMUP_UNKNOWN'], 'true')
                self.assertEqual(kwargs['env']['BENCH_LATE_POLL_INTERVAL_MS'], '1000')
                self.assertEqual(kwargs["env"]["BENCH_K6_IMAGE"], config["artifact"]["k6_image"])
                directory = Path(kwargs["env"]["BENCH_OUTPUT_DIR"])
                raw = {"measurement": {"scheduled": 1, "started": 1, "dropped": 0}, "benchmark": {"completion_observation": "polling upper bound"}, "metrics": {}}
                (directory / "k6-summary.json").write_text(json.dumps(raw))
                common = {"run_id": "roundtrip", "logical_request_id": "r1", "attempt_id": "r1-1", "scenario": "minimal", "phase": "measurement", "details": {"scheduled_elapsed_s": 0}}
                events = [{**common, "event": "scheduled", "elapsed_s": .02}, {**common, "event": "started", "elapsed_s": .02}, {**common, "event": "completed", "elapsed_s": .2, "latency_s": .2, "status": "correct"}]
                stdout = io.StringIO("\n".join(json.dumps({"msg": "BENCH_EVENT " + json.dumps(event)}) for event in events))
                return SimpleNamespace(stdout=stdout, wait=lambda: 0, poll=lambda: 0, kill=lambda: None)
            with patch("benchlib.runner.subprocess.Popen", side_effect=launch):
                run = run_k6(config, "roundtrip", Path(temp))
            result = analyze_run(run)
            self.assertEqual(result["counts"]["correct"], 1)
            self.assertEqual(result["latency"]["generator_lag"]["p99"], .02)
            self.assertEqual(result["counts"]["dropped"], 0)
            self.assertTrue((run / "k6-raw-summary.json").exists())
            self.assertEqual(verify_archive(run), [])

    def test_budget_includes_reset_calibration_and_collection(self):
        config = self.config()
        config["evidence"]["auto_calibrate"] = True
        config["execution"].update(reset_command=["reset"], reset_timeout_s=5, evidence_command=["collect"], evidence_timeout_s=5)
        self.assertEqual(planned_wall_seconds(config), 1 + 1 + 120 + 10 + 20 + 5 + 5)

    def test_malformed_later_pair_design_rejects_before_starting_any_run(self):
        config = self.config()
        config["comparison"] = {"pair_plan": [{}, {}, "malformed"]}
        with patch("benchlib.campaign.run_k6") as runner:
            with self.assertRaisesRegex(ValueError, "pair_plan"):
                execute_campaign(config, "smoke", "unused", local=True)
            runner.assert_not_called()

    def test_golden_report_is_bound_to_digest_and_actual_database_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            config = self.config()
            image = "example.invalid/engine@sha256:" + "a" * 64
            config["artifact"] = {"image": image}
            config["comparison"] = {"environment": {"database_product": "PostgreSQL", "database_version": "17"}}
            report = Path(temp) / "correctness.json"
            report.write_text(json.dumps({"passed": True, "cases": [{"status": "passed"}], "scope": {"image": image, "database_product": "PostgreSQL", "database_version": "17"}}))
            config["evidence"].update(golden_file=str(report), golden_sha256=hashlib.sha256(report.read_bytes()).hexdigest())
            with patch("benchlib.runner.subprocess.Popen", side_effect=FileNotFoundError()):
                first = run_k6(config, "matching", Path(temp))
            self.assertTrue(json.loads((first / "k6-summary.json").read_text())["golden_validated"])
            config["comparison"]["environment"]["database_version"] = "18"
            with patch("benchlib.runner.subprocess.Popen", side_effect=FileNotFoundError()):
                second = run_k6(config, "changed-db", Path(temp))
            self.assertFalse(json.loads((second / "k6-summary.json").read_text())["golden_validated"])

    def test_worker_override_rejects_golden_from_different_or_missing_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            config = self.config()
            image = "example.invalid/engine@sha256:" + "a" * 64
            worker = "example.invalid/worker@sha256:" + "b" * 64
            config["artifact"] = {"image": image, "worker_image": worker}
            config["comparison"] = {"environment": {"database_product":"PostgreSQL", "database_version":"17"}}
            for number, (artifact, expected) in enumerate([
                    ({"worker_image":worker}, True), ({"services_image":worker}, True),
                    ({"worker_image":image}, False), ({}, False)]):
                report = Path(temp) / (str(number) + ".json")
                report.write_text(json.dumps({"passed":True,"cases":[{"status":"passed"}],"artifact":artifact,
                    "scope":{"image":image,"database_product":"PostgreSQL","database_version":"17"}}))
                config["evidence"].update(golden_file=str(report),golden_sha256=hashlib.sha256(report.read_bytes()).hexdigest())
                with patch("benchlib.runner.subprocess.Popen", side_effect=FileNotFoundError()):
                    run = run_k6(config, "worker-"+str(number), Path(temp))
                result = json.loads((run/"k6-summary.json").read_text())
                self.assertEqual(result["golden_worker_image_matched"], expected)
                self.assertEqual(result["golden_validated"], expected)


if __name__ == "__main__":
    unittest.main()
