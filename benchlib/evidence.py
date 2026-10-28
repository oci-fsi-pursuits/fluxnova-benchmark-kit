"""Measured generator calibration and read-only telemetry collection.

No success flag is synthesized from HTTP workload outcomes. Linux host samples
validate generator headroom; application/database/engine metrics must be collected
from explicitly configured sources before telemetry can be called complete.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .contracts import is_number
from .util import ROOT, read_json, utc_now, write_json


def linux_host_sample(proc_root=Path("/proc")):
    """Read cumulative CPU ticks and available memory; caller computes deltas."""
    proc_root = Path(proc_root)
    ticks = [int(n) for n in (proc_root / "stat").read_text().splitlines()[0].split()[1:9]]
    memory = {}
    for line in (proc_root / "meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        memory[key] = int(value.strip().split()[0]) * 1024
    return {"cpu_total_ticks": sum(ticks), "cpu_idle_ticks": ticks[3] + ticks[4],
            "memory_total_bytes": memory["MemTotal"], "memory_available_bytes": memory.get("MemAvailable", memory.get("MemFree", 0)),
            "logical_cpus": os.cpu_count()}


def _prometheus_metrics(body, expected):
    values = {}
    for line in body.splitlines():
        found = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+([^\s]+)", line)
        if found:
            try:
                number = float(found.group(2))
                if math.isfinite(number):
                    values[found.group(1)] = number
            except ValueError:
                pass
    if not expected or any(name not in values for name in expected):
        raise ValueError("configured required metric names are absent")
    return {key: values[key] for key in expected}


def collect_source(source):
    """Execute only the explicitly configured read-only metrics argv/endpoint."""
    result = {"name": source.get("name", "unnamed"), "role": source.get("role", "unspecified"), "ok": False}
    try:
        kind = source.get("kind", "http")
        if kind == "docker_stats":
            command = ["docker", "stats", "--no-stream", "--format", "{{json .}}"]
            command += source.get("containers", [])
            collected = subprocess.run(command, check=True, capture_output=True, text=True, timeout=5)
            values = [json.loads(line) for line in collected.stdout.splitlines() if line.strip()]
            if not values:
                raise ValueError("no container samples")
            result.update(ok=True, metrics=values)
            return result
        if kind == "http":
            headers = {}
            if source.get("authorization_env"):
                headers["Authorization"] = os.environ[source["authorization_env"]]
            request = urllib.request.Request(source["url"], headers=headers)
            with urllib.request.urlopen(request, timeout=3) as response:
                body = response.read(4 * 1024 * 1024).decode("utf-8")
        elif kind == "command":
            argv = source.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
                raise ValueError("metrics command requires an argv array")
            body = subprocess.run(argv, check=True, capture_output=True, text=True, timeout=5).stdout
        elif kind == "file":
            # Controller-pushed snapshots avoid giving the load generator SSH or
            # database credentials. Never accept an old snapshot as live data.
            body = Path(source['path']).read_text(encoding='utf-8')
            snapshot = json.loads(body)
            meta = snapshot.get('_meta', {})
            observed = datetime.fromisoformat(meta['observed_at'].replace('Z', '+00:00'))
            maximum = source.get('max_age_s', 30)
            if observed.tzinfo is None or not is_number(maximum) or not 0 < maximum <= 60:
                raise ValueError('Snapshot needs a timezone and a bounded freshness limit')
            age = (datetime.now(timezone.utc) - observed).total_seconds()
            if age < -2 or age > maximum or meta.get('errors') != {}:
                raise ValueError('Telemetry snapshot is stale, future-dated or unsuccessful')
            result.update(observed_at=meta['observed_at'], snapshot_age_s=age)
        else:
            raise ValueError("unsupported telemetry source kind")
        expected = source.get("required_metrics", [])
        if source.get("format", "prometheus") == "json" or kind == "file":
            values = json.loads(body)
            if not isinstance(values, dict) or not expected or any(not is_number(values.get(key)) for key in expected):
                raise ValueError("required numeric JSON metrics are absent")
            metrics = {key: values[key] for key in expected}
        else:
            metrics = _prometheus_metrics(body, expected)
        result.update(ok=True, metrics=metrics)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, subprocess.SubprocessError) as exc:
        # Store error class/reason, not raw HTTP bodies, headers or stderr which
        # can contain credentials. Exact details remain with configured systems.
        result["error"] = type(exc).__name__
    return result


class TelemetrySampler:
    def __init__(self, config, run_id, directory):
        self.config, self.run_id, self.directory = config, run_id, Path(directory)
        self.settings = config.get("evidence", {})
        self.interval = max(0.2, float(self.settings.get("sample_interval_s", 1)))
        self.sources = self.settings.get("telemetry_sources", [])
        self.stop_signal = threading.Event()
        self.samples = []
        self.thread = None
        self.started = time.monotonic()

    def start(self):
        self.thread = threading.Thread(target=self._collect, daemon=True)
        self.thread.start()
        return self

    def _collect(self):
        path = self.directory / "telemetry.ndjson"
        with path.open("w", encoding="utf-8") as stream:
            while not self.stop_signal.is_set():
                sample = {"at": utc_now(), "elapsed_s": time.monotonic() - self.started, "sources": []}
                try:
                    sample["generator_host"] = linux_host_sample()
                except (OSError, ValueError, KeyError, IndexError):
                    sample["generator_host"] = None
                sample["sources"] = [collect_source(source) for source in self.sources]
                self.samples.append(sample)
                stream.write(json.dumps(sample, allow_nan=False) + "\n")
                stream.flush()
                self.stop_signal.wait(self.interval)

    def stop(self):
        self.stop_signal.set()
        if self.thread:
            self.thread.join(timeout=max(15, len(self.sources) * 6 + 5))
        return self.summary()

    def summary(self):
        cpu = []
        hosts = [sample["generator_host"] for sample in self.samples if sample.get("generator_host")]
        for left, right in zip(hosts, hosts[1:]):
            total = right["cpu_total_ticks"] - left["cpu_total_ticks"]
            idle = right["cpu_idle_ticks"] - left["cpu_idle_ticks"]
            if total > 0:
                cpu.append(max(0, min(1, 1 - idle / total)))
        available = [s["memory_available_bytes"] / s["memory_total_bytes"] for s in hosts if s["memory_total_bytes"]]
        gaps = [b["elapsed_s"] - a["elapsed_s"] for a, b in zip(self.samples, self.samples[1:])]
        roles = set()
        sources_ok = len(self.samples) >= 2
        for index, source in enumerate(self.sources):
            ok = all(len(s["sources"]) > index and s["sources"][index]["ok"] for s in self.samples)
            if ok and self.samples:
                roles.add(source.get("role"))
            elif source.get("required", True):
                sources_ok = False
        required_roles = set(self.settings.get("required_telemetry_roles", ["application", "database", "engine"]))
        gap_ok = bool(gaps) and max(gaps) <= max(5, self.interval * 3)
        host_valid = len(cpu) >= 2 and bool(available) and max(cpu) < 0.85 and min(available) >= 0.10 and gap_ok
        complete = sources_ok and bool(required_roles) and required_roles <= roles and gap_ok
        result = {"run_id": self.run_id, "valid": complete, "sample_count": len(self.samples),
                  "successful_roles": sorted(role for role in roles if role), "required_roles": sorted(required_roles),
                  "generator_host_valid": host_valid, "cpu_busy_fraction_max": max(cpu) if cpu else None,
                  "memory_available_fraction_min": min(available) if available else None,
                  "maximum_sample_gap_s": max(gaps) if gaps else None,
                  "scope": "Read-only host and declared application/database/engine telemetry; completeness requires all required metrics throughout sampling.",
                  "limits": "Generator headroom defaults (<85% host CPU, >=10% available RAM) are engineering guardrails, not universal capacity guarantees."}
        write_json(self.directory / "telemetry-validation.json", result)
        return result


def calibrate_generator(config, run_id, directory):
    """Run k6 against an isolated loopback stub at >=2x expected HTTP volume."""
    directory = Path(directory)
    settings = config.get("evidence", {})
    work = config["workload"]
    result = {"run_id": run_id, "valid": False, "scope": "Loopback HTTP generator headroom; does not benchmark Fluxnova or prove remote network capacity."}
    if platform.system() != "Linux" and not settings.get("calibrate_on_nonlinux", False):
        result["reason"] = "Automatic live headroom validation requires Linux /proc; local functional mode remains unranked."
        write_json(directory / "generator-calibration.json", result)
        return result
    multiplier = max(2, float(settings.get("calibration_headroom_multiplier", 2)))
    calls_per_workflow = max(2, int(settings.get("estimated_http_calls_per_workflow", 8)))
    requested_rate = math.ceil(work["rate"] * calls_per_workflow * multiplier)
    seconds = max(3, int(settings.get("calibration_duration_s", 10)))
    class Stub(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    destination = directory / "generator-calibration-summary.json"
    script = directory / "generator-calibration.js"
    url = f"http://127.0.0.1:{server.server_port}/calibration"
    script.write_text("import http from 'k6/http';\n" +
                      "export const options = {scenarios:{calibration:{executor:'constant-arrival-rate',rate:" + str(requested_rate) + ",timeUnit:'1s',duration:'" + str(seconds) + "s',preAllocatedVUs:256,maxVUs:256,gracefulStop:'5s'}},thresholds:{http_req_failed:['rate==0'],dropped_iterations:['count==0']}};\n" +
                      "export default function(){http.get(" + json.dumps(url) + ",{timeout:'2s'});}\n" +
                      "export function handleSummary(data){return {[" + json.dumps(str(destination.resolve())) + "]:JSON.stringify(data)};}\n", encoding="utf-8")
    try:
        command = [config.get("execution", {}).get("k6_binary", "k6"), "run", "--no-color", str(script)]
        environment = os.environ.copy()
        if config.get("artifact", {}).get("k6_image"):
            environment["BENCH_K6_IMAGE"] = config["artifact"]["k6_image"]
        with (directory / "generator-calibration.log").open("w", encoding="utf-8") as stream:
            completed = subprocess.run(command, cwd=ROOT, env=environment, stdout=stream, stderr=subprocess.STDOUT, timeout=seconds + 20)
        data = read_json(destination)
        metrics = data.get("metrics", {})
        requests = metrics.get("http_reqs", {}).get("values", {}).get("count", 0)
        dropped = metrics.get("dropped_iterations", {}).get("values", {}).get("count", 0)
        errors = metrics.get("http_req_failed", {}).get("values", {}).get("rate", 1)
        result.update(valid=completed.returncode == 0 and requests >= requested_rate * seconds and dropped == 0 and errors == 0,
                      requested_http_rate=requested_rate, requests=requests, dropped=dropped, error_fraction=errors,
                      duration_s=seconds, headroom_multiplier=multiplier, estimated_http_calls_per_workflow=calls_per_workflow)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result["reason"] = type(exc).__name__
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    write_json(directory / "generator-calibration.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only benchmark telemetry and generator calibration")
    parser.add_argument("command", choices=("collect", "calibrate"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--duration-s", type=float, default=30)
    args = parser.parse_args(argv)
    config = read_json(args.config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "calibrate":
        result = calibrate_generator(config, args.run_id, args.output_dir)
    else:
        if args.duration_s <= 0:
            parser.error("--duration-s must be positive")
        sampler = TelemetrySampler(config, args.run_id, args.output_dir).start()
        try:
            time.sleep(args.duration_s)
        finally:
            result = sampler.stop()
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
