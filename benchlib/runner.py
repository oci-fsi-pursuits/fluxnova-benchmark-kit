from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from .util import ROOT, utc_now, write_json, read_json, repository_hashes, auth_environment, safe_run_id, sha256

def create_run(config, run_id, output_root=None):
    run_id = safe_run_id(run_id)
    cost_sources = []
    for item in config.get("costs", {}).get("source_files", []):
        source = Path(item["path"])
        if source.suffix.lower() not in {".csv", ".json", ".parquet"} or not source.is_file() or sha256(source) != item["sha256"]:
            raise ValueError("Raw cost evidence changed or is unavailable; regenerate the cost import")
        cost_sources.append(source)
    directory = Path(output_root or ROOT / "runs") / run_id
    directory.mkdir(parents=True, exist_ok=False)
    frozen = copy.deepcopy(config)
    frozen.setdefault("run", {}).update({"id": run_id, "started_at": utc_now()})
    manifest = {"schema_version": "1.0", "run_id": run_id, "created_at": utc_now(),
                "config": frozen, "source_hashes": repository_hashes()}
    write_json(directory / "manifest.json", manifest)
    write_json(directory / "costs.json", config.get("costs", {"currency": "USD", "complete": False, "records": []}))
    for i, source in enumerate(cost_sources):
        (directory / (f"cost-source-{i:02d}" + source.suffix)).write_bytes(source.read_bytes())
    return directory

def extract_event(line):
    """k6 JSON console envelope or a raw BENCH_EVENT line; ignore non-events."""
    try:
        envelope = json.loads(line)
        message = envelope.get("msg", "") if isinstance(envelope, dict) else ""
    except (ValueError, TypeError):
        message = line.strip()
    if not message.startswith("BENCH_EVENT "):
        return None
    event = json.loads(message[len("BENCH_EVENT "):])
    if not isinstance(event, dict) or not event.get("event"):
        raise ValueError("Malformed benchmark event")
    return event

def normalize_event(event):
    """Retain actual emit time while using the planned arrival as start boundary."""
    from .contracts import is_number
    from datetime import timedelta
    from .contracts import parse_timestamp
    event = copy.deepcopy(event)
    details = event.get("details", {})
    scheduled = details.get("scheduled_elapsed_s") if isinstance(details, dict) else None
    if event.get("event") == "scheduled" and is_number(scheduled):
        actual = event.get("elapsed_s")
        event.setdefault("details", {})["observed_emit_elapsed_s"] = actual
        event["elapsed_s"] = scheduled
        stamp = parse_timestamp(event.get("at"))
        if stamp is not None and is_number(actual):
            event["details"]["observed_emit_at"] = event["at"]
            event["at"] = (stamp - timedelta(seconds=actual - scheduled)).isoformat()
    return event


def normalize_summary(raw, work):
    """Flatten only measurement counts; preserve original phase evidence."""
    raw = raw if isinstance(raw, dict) else {}
    result = copy.deepcopy(raw)
    benchmark = raw.get("benchmark", {})
    measurement = raw.get("measurement", {})
    if isinstance(benchmark, dict):
        for key, value in benchmark.items():
            if key not in {"generator_validated", "telemetry_complete"}:
                result[key] = value
    for key in ("scheduled", "started", "dropped"):
        if isinstance(measurement, dict) and key in measurement:
            result[key] = measurement[key]
    result.setdefault("scheduled", work["rate"] * work["duration_s"])
    result["measurement_duration_s"] = work["duration_s"]
    metrics = raw.get("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
    if "dropped" not in result:
        dropped = metrics.get("dropped_iterations{scenario:measurement}", {}).get("values", {}).get("count")
        if dropped is None and work.get("warmup_s", 0) == 0:
            dropped = metrics.get("dropped_iterations", {}).get("values", {}).get("count")
        if dropped is not None:
            result["dropped"] = dropped
    return result


def _expanded_argv(command, run_id, directory):
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
        raise ValueError("Execution hooks must be nonempty argv arrays")
    return [arg.replace("{run_id}", run_id).replace("{run_dir}", str(directory.resolve())) for arg in command]


def _run_reset(config, run_id, directory, env):
    command = config.get("execution", {}).get("reset_command")
    if not command:
        return
    argv = _expanded_argv(command, run_id, directory)
    maximum = config.get("execution", {}).get("reset_timeout_s", 600)
    try:
        completed = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=maximum)
    except subprocess.TimeoutExpired as exc:
        decode=lambda value:value.decode('utf-8',errors='replace') if isinstance(value,bytes) else value or ''
        (directory/'reset.log').write_text(decode(exc.stdout)+'\n'+decode(exc.stderr),encoding='utf-8')
        write_json(directory/'reset.json',{'run_id':run_id,'success':False,'error':'Reset hook timed out',
                                        'completed_at':utc_now()})
        raise RuntimeError('Initial-state reset hook timed out; workload will not start') from None
    # Keep stderr separate from reported metadata; reset tools must not print secrets.
    (directory / "reset.log").write_text(completed.stdout + "\n" + completed.stderr, encoding="utf-8")
    evidence = {"run_id": run_id, "success": completed.returncode == 0, "returncode": completed.returncode, "completed_at": utc_now()}
    try:
        declared = json.loads(completed.stdout)
        from .reset_mailbox import validate_receipt
        design=config.get('comparison',{})
        expected={key:design[key] for key in ('dataset_sha256','state_fingerprint') if key in design}
        if expected and len(expected)!=2:raise ValueError('Both checkpoint identities must be pinned')
        validate_receipt(declared,run_id,expected or None)
        for key in ('dataset_sha256','state_fingerprint','method'):
            evidence[key]=declared[key]
    except (ValueError,TypeError,KeyError) as exc:
        evidence.update(success=False,error=str(exc))
    write_json(directory / "reset.json", evidence)
    if not evidence['success']:
        raise RuntimeError("Initial-state reset command failed; inspect retained reset evidence")


def seal_archive(directory):
    write_json(directory / "checksums.json", {p.name: sha256(p) for p in sorted(directory.iterdir())
                                            if p.is_file() and p.name != "checksums.json"})


def run_k6(config, run_id, output_root=None):
    """Always finalize/seal collected evidence, including process/hook failures."""
    from .contracts import is_number
    from .analysis import percentile
    from .evidence import TelemetrySampler, calibrate_generator
    directory = create_run(config, run_id, output_root)
    work = config["workload"]
    execution = config.get("execution", {})
    evidence_settings = config.get("evidence", {})
    env = auth_environment(config)
    env.update({"BENCH_RUN_ID": run_id, "BENCH_RUN_DIR": str(directory.resolve())})
    if config.get("artifact", {}).get("k6_image"):
        env["BENCH_K6_IMAGE"] = config["artifact"]["k6_image"]
    values = {"BASE_URL": config["runtime"]["base_url"], "SUPPORT_URL": config["runtime"].get("support_url", ""),
              "RUN_ID": run_id, "OUTPUT_DIR": str(directory.resolve()), "SCENARIO": work["scenario"],
              "RATE": work["rate"], "DURATION_S": work["duration_s"], "WARMUP_S": work.get("warmup_s", 600),
              "DRAIN_S": work.get("drain_s", 300), "DEADLINE_S": work.get("deadline_s", 1),
              "PAYLOAD_BYTES": work.get("payload_bytes", 1024), "SEED": work.get("seed", 42),
              "PREALLOCATED_VUS": work.get("preallocated_vus", 256), "MAX_VUS": work.get("max_vus", 256),
              "POLL_INTERVAL_MS": work.get("poll_interval_ms", 100),
              "LATE_POLL_INTERVAL_MS": work.get("late_poll_interval_ms", work.get("poll_interval_ms", 100)),
              "ABORT_ON_WARMUP_UNKNOWN": str(work.get("abort_on_warmup_unknown", False)).lower()}
    env.update({"BENCH_" + key: str(value) for key, value in values.items()})
    maximum = execution.get("max_run_seconds", sum(work.get(k, 0) for k in ("duration_s", "warmup_s", "drain_s")) + 120)
    argv = [execution.get("k6_binary", "k6"), "run", "--log-format", "json", "--no-color", str(ROOT / "load" / "benchmark.js")]
    started = time.monotonic()
    timed_out = threading.Event()
    malformed, returncode = 0, None
    proc = timer = sampler = None
    calibration, telemetry = {}, {}
    observed_started, observed_scheduled = set(), set()
    lags = []
    problems = []
    interrupted = None
    (directory / "events.ndjson").touch()
    try:
        _run_reset(config, run_id, directory, env)
        if evidence_settings.get("auto_calibrate", True):
            calibration = calibrate_generator(config, run_id, directory)
        if evidence_settings.get("auto_collect", True):
            sampler = TelemetrySampler(config, run_id, directory).start()
        load_window = {"run_id": run_id, "started_at": utc_now(), "measurement_offset_s": work.get("warmup_s", 0),
                       "measurement_duration_s": work["duration_s"], "drain_s": work.get("drain_s", 0),
                       "scope": "Process launch/finish envelope; exact request times are retained in events.ndjson"}
        write_json(directory / "load-window.json", load_window)
        with (directory / "runner.log").open("w", encoding="utf-8") as log, (directory / "events.ndjson").open("w", encoding="utf-8") as events:
            proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
            def stop():
                timed_out.set()
                if proc.poll() is None:
                    proc.kill()
            timer = threading.Timer(maximum, stop)
            timer.start()
            for line in proc.stdout:
                log.write(line)
                try:
                    raw_event = extract_event(line)
                    if raw_event:
                        event = normalize_event(raw_event)
                        events.write(json.dumps(event, allow_nan=False) + "\n")
                        events.flush()
                        if event.get("phase", "measurement") == "measurement":
                            identity = event.get("logical_request_id")
                            if event["event"] == "started":
                                observed_started.add(identity)
                                scheduled = event.get("details", {}).get("scheduled_elapsed_s")
                                if is_number(scheduled) and is_number(event.get("elapsed_s")):
                                    lags.append(max(0, event["elapsed_s"] - scheduled))
                            elif event["event"] == "scheduled":
                                observed_scheduled.add(identity)
                except (ValueError, TypeError):
                    malformed += 1
            returncode = proc.wait()
        load_window.update(finished_at=utc_now(), returncode=returncode)
        write_json(directory / "load-window.json", load_window)
        hook = execution.get("evidence_command")
        if hook:
            with (directory / "evidence-collector.log").open("w", encoding="utf-8") as stream:
                collected = subprocess.run(_expanded_argv(hook, run_id, directory), cwd=ROOT, env=env,
                                           stdout=stream, stderr=subprocess.STDOUT, timeout=execution.get("evidence_timeout_s", 120))
            if collected.returncode:
                problems.append("Evidence collector command failed")
    except BaseException as exc:
        problems.append(type(exc).__name__)
        if proc is not None and proc.poll() is None:
            proc.kill()
            returncode = proc.wait()
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            interrupted = exc
    finally:
        if timer is not None:
            timer.cancel()
        if sampler is not None:
            try:
                telemetry = sampler.stop()
            except Exception as exc:
                problems.append("Telemetry finalization: " + type(exc).__name__)
        summary_file = directory / "k6-summary.json"
        raw = {}
        if summary_file.exists():
            try:
                (directory / "k6-raw-summary.json").write_bytes(summary_file.read_bytes())
                raw = read_json(summary_file)
                if not isinstance(raw, dict):
                    raise ValueError("Summary must be an object")
            except (ValueError, OSError):
                raw = {}
                problems.append("Missing or malformed k6 summary")
        else:
            problems.append("k6 did not produce its summary")
        summary = normalize_summary(raw, work)
        summary.setdefault("started", len(observed_started))
        if "dropped" not in summary:
            problems.append("Measurement-phase dropped-iteration evidence missing")
        elif summary["dropped"]:
            summary["scheduling_precision"] = "approximate_after_drops"
            summary["warning"] = (str(summary.get("warning", "")) + " Scheduled times are derived from started-iteration index; after drops they are approximate, not exact replay slots. Do not rank latency from this run.").strip()
        lag_p99 = percentile(lags, .99)
        raw_metrics = raw.get("metrics", {})
        raw_rate = raw_metrics.get("http_reqs", {}).get("values", {}).get("rate") if isinstance(raw_metrics, dict) else None
        rate_supported = is_number(raw_rate) and is_number(calibration.get("requested_http_rate")) and raw_rate * calibration.get("headroom_multiplier", 2) <= calibration["requested_http_rate"]
        automatic_generator_valid = (calibration.get("valid") is True and telemetry.get("generator_host_valid") is True
                                     and lag_p99 is not None and lag_p99 <= evidence_settings.get("maximum_generator_lag_s", .1) and rate_supported
                                     and summary.get("dropped") == 0)
        generated = {"run_id": run_id, "valid": automatic_generator_valid, "calibration_passed": calibration.get("valid") is True,
                     "in_run_headroom_passed": telemetry.get("generator_host_valid") is True, "arrival_lag_p99_s": lag_p99,
                     "observed_http_rate": raw_rate, "calibrated_rate_covers_observed": rate_supported,
                     "scope": "Loopback calibration plus in-run Linux host headroom and scheduling lag; remote path is part of tested system."}
        write_json(directory / "generator-validation.json", generated)
        summary.update({"generator_validated": automatic_generator_valid, "telemetry_complete": telemetry.get("valid") is True})
        summary["golden_validated"] = False
        golden_source = evidence_settings.get("golden_file")
        if golden_source:
            try:
                source = Path(golden_source.replace("{run_id}", run_id).replace("{run_dir}", str(directory.resolve())))
                golden = read_json(source)
                target = directory / "golden-correctness.json"
                target.write_bytes(source.read_bytes())
                cases = golden.get("cases", [])
                passes = (golden.get("passed") is True or golden.get("status") == "pass") and bool(cases) and all(case.get("status") in {"passed", "pass"} for case in cases)
                pinned = evidence_settings.get("golden_sha256") == sha256(target)
                environment = config.get("comparison", {}).get("environment", {})
                expected_scope = {"image": config.get("artifact", {}).get("image"),
                                  "database_product": environment.get("database_product"), "database_version": environment.get("database_version")}
                scope = golden.get("scope", {})
                scoped = all(expected_scope.values()) and all(scope.get(key) == value for key, value in expected_scope.items())
                if "worker_image" in config.get("artifact", {}):
                    recorded = golden.get("artifact", {})
                    actual_worker = recorded.get("worker_image", recorded.get("services_image"))
                    worker_matches = bool(actual_worker) and actual_worker == config["artifact"]["worker_image"]
                    summary["golden_worker_image_matched"] = worker_matches
                    scoped = scoped and worker_matches
                summary.update(golden_validated=bool(passes and pinned and scoped), golden_sha256=sha256(target),
                               golden_cases_passed=passes, golden_scope_matched=bool(scoped), golden_hash_matched=pinned)
            except (OSError, ValueError, AttributeError, TypeError):
                summary["golden_validation_error"] = "Golden evidence is missing or malformed"
        for name, config_key in (("generator_validated", "generator_validation_file"), ("telemetry_complete", "telemetry_file")):
            source = evidence_settings.get(config_key)
            if not source:
                continue
            summary[name] = False
            try:
                source = Path(source.replace("{run_id}", run_id).replace("{run_dir}", str(directory.resolve())))
                evidence = read_json(source)
                target = directory / (config_key + ".json")
                write_json(target, evidence)
                summary[name] = evidence.get("valid") is True and evidence.get("run_id") == run_id
                summary[config_key + "_sha256"] = sha256(target)
            except (OSError, ValueError, AttributeError):
                problems.append(config_key + " unavailable or malformed")
        if timed_out.is_set() or malformed or returncode not in (0,):
            summary["generator_validated"] = False
        if summary.get("scheduling_precision") == "approximate_after_drops":
            summary["generator_validated"] = False
        if problems:
            summary["generator_validated"] = False
        summary.update({"runner_exit_code": returncode, "runner_timed_out": timed_out.is_set(), "malformed_events": malformed,
                        "runner_problems": problems, "observed_measurement_scheduled": len(observed_scheduled)})
        write_json(summary_file, summary)
        write_json(directory / "execution.json", {"status": "timeout" if timed_out.is_set() else "error" if problems or returncode != 0 else "finished",
                    "returncode": returncode, "wall_seconds": time.monotonic() - started, "malformed_events": malformed,
                    "problems": problems, "finished_at": utc_now()})
        seal_archive(directory)
    if interrupted is not None:
        raise interrupted
    return directory

def verify_archive(directory):
    directory = Path(directory).resolve()
    checksums = read_json(directory / "checksums.json")
    if not isinstance(checksums, dict) or not checksums:
        raise ValueError("Archive checksum map must be a nonempty object")
    for name, digest in checksums.items():
        if not isinstance(name, str) or Path(name).name != name or not (directory / name).resolve().is_relative_to(directory):
            raise ValueError("Archive contains an unsafe checksum path")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("Archive contains an invalid checksum")
    return [name for name, digest in checksums.items() if not (directory / name).is_file() or sha256(directory / name) != digest]

def demo_run(output_root=None):
    """A deterministic fixture proving the report pipeline, never a benchmark result."""
    config = {"schema_version": "1.0", "campaign": {"name": "synthetic-harness-validation", "currency": "USD"},
              "artifact": {"release": "FIXTURE"}, "runtime": {"base_url": "http://fixture.invalid", "cpu": 4, "memory_gib": 8, "heap_gib": 4},
              "run": {"mode": "fixture"}, "workload": {"scenario": "minimal", "duration_s": 10, "warmup_s": 0, "rate": 1, "deadline_s": 1, "drain_s": 5, "seed": 42, "payload_bytes": 1024},
              "costs": {"currency": "USD", "complete": False, "records": []}}
    directory = create_run(config, "fixture-" + str(time.time_ns()), output_root)
    with (directory / "events.ndjson").open("w", encoding="utf-8") as f:
        for i in range(10):
            common = {"run_id": directory.name, "logical_request_id": str(i), "attempt_id": str(i) + "-1", "scenario": "minimal", "phase": "measurement", "deadline_s": 1}
            for kind in ("scheduled", "started", "accepted"):
                f.write(json.dumps({**common, "event": kind, "elapsed_s": i}) + "\n")
            if i != 9:
                f.write(json.dumps({**common, "event": "completed", "elapsed_s": i + .1, "latency_s": .1, "status": "correct"}) + "\n")
    write_json(directory / "k6-summary.json", {"scheduled": 10, "started": 10, "dropped": 0, "measurement_duration_s": 10, "generator_validated": False, "telemetry_complete": False})
    write_json(directory / "checksums.json", {p.name: sha256(p) for p in directory.iterdir() if p.is_file()})
    return directory
