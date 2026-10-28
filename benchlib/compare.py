"""Provisional regression gates for explicitly paired, independent live runs."""
from __future__ import annotations

import re
from pathlib import Path

from .analysis import FIXTURE_WARNING, LOCAL_WARNING, analyze_run, paired_effect_interval
from .contracts import is_number
from .util import read_json, write_json

_SHA256 = re.compile(r"[a-fA-F0-9]{64}")
_IGNORED = {"runtime.base_url", "runtime.support_url", "runtime.auth_username_env", "runtime.auth_password_env",
            "oci.ssh_authorized_key", "oci.db_admin_secret_id", "oci.db_admin_username", "artifact.resolved_at"}


def _flatten(value, prefix=""):
    if not isinstance(value, dict):
        return {prefix: value}
    result = {}
    for key, item in sorted(value.items()):
        result.update(_flatten(item, f"{prefix}.{key}" if prefix else key))
    return result


def _signature(config):
    result = {}
    for name in ("artifact", "runtime", "database", "oci", "telemetry"):
        result.update(_flatten(config.get(name, {}), name))
    design = config.get("comparison", {})
    result.update(_flatten(design.get("environment", {}) if isinstance(design, dict) else {}, "comparison.environment"))
    result["campaign.region"] = config.get("campaign", {}).get("region")
    result["campaign.provider"] = config.get("campaign", {}).get("provider", "oci")
    return {key: value for key, value in result.items() if key not in _IGNORED}


def _gate(interval, threshold, direction):
    low, high = interval["ci95_low"], interval["ci95_high"]
    epsilon = 1e-12
    if direction == "increase_is_worse":
        return "failed" if low > threshold + epsilon else "pass" if high <= threshold + epsilon else "inconclusive"
    return "failed" if high < -threshold - epsilon else "pass" if low >= -threshold - epsilon else "inconclusive"


def _check_design(path, manifest, summary, errors, missing):
    config = manifest.get("config", manifest)
    design = config.get("comparison", {})
    label = str(summary["run_id"])
    artifact = config.get("artifact", {})
    if not artifact.get("image"):
        missing.append(f"{label}: immutable runtime image provenance is missing")
    elif not re.fullmatch(r"[^\s@]+@sha256:[a-fA-F0-9]{64}", str(artifact["image"])):
        errors.append(f"{label}: runtime image must be recorded by immutable digest")
    if not isinstance(design, dict):
        errors.append(f"{label}: comparison metadata must be an object")
        return {}
    for key in ("pair_id", "dataset_sha256", "state_fingerprint"):
        if not design.get(key):
            missing.append(f"{label}: comparison.{key} is missing")
    if design.get("pair_id") is not None and not isinstance(design["pair_id"], str):
        errors.append(f"{label}: pair_id must be a string")
    for key in ("dataset_sha256", "state_fingerprint"):
        if design.get(key) and not _SHA256.fullmatch(str(design[key])):
            errors.append(f"{label}: comparison.{key} must be a SHA-256 fingerprint")
    if design.get("randomized_order") is not True or design.get("pair_order") not in {"AB", "BA"}:
        missing.append(f"{label}: randomized pair order (AB/BA) is not recorded")
    environment = design.get("environment", {})
    for key in ("cpu_arch", "cpu_model", "topology", "database_product", "database_version", "durability", "network_path"):
        if not isinstance(environment, dict) or not environment.get(key):
            missing.append(f"{label}: comparison.environment.{key} is missing")
    if not manifest.get("source_hashes"):
        missing.append(f"{label}: harness/workload source hashes are missing")
    reset_name = design.get("reset_evidence_file")
    if not reset_name:
        missing.append(f"{label}: sealed reset evidence is missing")
    elif not isinstance(reset_name, str) or Path(reset_name).name != reset_name:
        errors.append(f"{label}: reset_evidence_file must name a file inside the run archive")
    elif not (path / reset_name).is_file():
        missing.append(f"{label}: reset evidence file is absent")
    else:
        try:
            reset = read_json(path / reset_name)
            seal = read_json(path / "checksums.json")
            if reset_name not in seal:
                missing.append(f"{label}: reset evidence is not covered by archive checksums")
            if reset.get("run_id") != summary["run_id"] or reset.get("success") is not True:
                errors.append(f"{label}: reset evidence does not establish successful reset for this run")
            for key in ("dataset_sha256", "state_fingerprint"):
                if reset.get(key) != design.get(key):
                    errors.append(f"{label}: reset evidence {key} differs from comparison metadata")
        except (OSError, ValueError, AttributeError):
            errors.append(f"{label}: reset evidence or checksum seal cannot be read")
    return design


def compare_runs(baseline_dirs, candidate_dirs, output_dir):
    """Compare positional A/B pairs without turning missing design into a pass."""
    if len(baseline_dirs) != len(candidate_dirs) or len(baseline_dirs) < 5:
        raise ValueError("Provide at least five explicitly paired baseline and candidate run directories")
    paths_a = [Path(p).resolve() for p in baseline_dirs]
    paths_b = [Path(p).resolve() for p in candidate_dirs]
    if len(set(paths_a + paths_b)) != 2 * len(paths_a):
        raise ValueError("Independent run directories cannot be reused within or across comparison lanes")
    left, right = [analyze_run(p) for p in paths_a], [analyze_run(p) for p in paths_b]
    summaries = left + right
    manifests = [read_json(p / "manifest.json") for p in paths_a + paths_b]
    configs = [m.get("config", m) for m in manifests]
    errors, missing = [], []
    if len({r["run_id"] for r in summaries}) != len(summaries):
        errors.append("Run IDs must be unique across independent repetitions")
    modes = {r["mode"] for r in summaries}
    if len(modes) > 1:
        errors.append("Fixture, local-functional and live provenance cannot be mixed")
    nonlive = modes != {"live"}
    if nonlive:
        missing.append("Fixture/local-functional comparisons are diagnostic only; no cloud regression verdict is established")
    designs = [_check_design(p, m, r, errors, missing) for p, m, r in zip(paths_a + paths_b, manifests, summaries)]
    count = len(left)
    pair_ids = []
    for i in range(count):
        a, b, da, db = configs[i], configs[i + count], designs[i], designs[i + count]
        if a.get("workload") != b.get("workload"):
            errors.append(f"Pair {i + 1}: workload, seed, payload or timings differ")
        for key in ("pair_id", "dataset_sha256", "state_fingerprint", "pair_order", "randomized_order"):
            if da.get(key) != db.get(key):
                errors.append(f"Pair {i + 1}: comparison.{key} differs")
        pair_ids.append(da.get("pair_id"))
        factors = da.get("experimental_factors", [])
        if not isinstance(factors, list) or any(not isinstance(f, str) or not f for f in factors):
            errors.append(f"Pair {i + 1}: experimental_factors must be a list of dotted field paths")
            factors = []
        if db.get("experimental_factors", []) != factors:
            errors.append(f"Pair {i + 1}: experimental factor declarations differ")
        allowed_roots = {"artifact", "runtime", "database", "oci", "telemetry", "campaign", "comparison"}
        if any(f.split(".")[0] not in allowed_roots for f in factors):
            errors.append(f"Pair {i + 1}: factors cannot override workload/state/reset controls")
        sa, sb = _signature(a), _signature(b)
        for key in sorted(set(sa) | set(sb)):
            if sa.get(key) != sb.get(key) and not any(key == f or key.startswith(f + ".") for f in factors):
                errors.append(f"Pair {i + 1}: undeclared experimental difference: {key}")
        if manifests[i].get("source_hashes") != manifests[i + count].get("source_hashes"):
            errors.append(f"Pair {i + 1}: benchmark harness/workload source hashes differ")
    known_ids = [identity for identity in pair_ids if isinstance(identity, str)]
    if len(known_ids) != len(set(known_ids)):
        errors.append("Pair IDs must be unique; repeated records are not independent repetitions")
    for name, lane, lane_m, lane_d in (("baseline", configs[:count], manifests[:count], designs[:count]), ("candidate", configs[count:], manifests[count:], designs[count:])):
        for index, config in enumerate(lane[1:], 1):
            if _signature(config) != _signature(lane[0]) or config.get("workload") != lane[0].get("workload"):
                errors.append(f"{name} run {index + 1}: software/environment/workload changed within repetitions")
            for key in ("dataset_sha256", "state_fingerprint", "experimental_factors"):
                if lane_d[index].get(key) != lane_d[0].get(key):
                    errors.append(f"{name} run {index + 1}: repeated state/design differs ({key})")
            if lane_m[index].get("source_hashes") != lane_m[0].get("source_hashes"):
                errors.append(f"{name} run {index + 1}: benchmark source hashes changed within repetitions")
    for result in summaries:
        if result["status"] == "invalid":
            errors.append(f"{result['run_id']}: run evidence is invalid")
        elif result["status"] == "inconclusive":
            missing.append(f"{result['run_id']}: run evidence is incomplete")
        if not result.get("evidence_integrity", {}).get("verified"):
            missing.append(f"{result['run_id']}: input archive is not verified")
    metrics = {}
    definitions = (
        ("p95_s", lambda r: r["latency"]["correct_only"]["p95"], 0.10, "increase_is_worse"),
        ("p99_s", lambda r: r["latency"]["correct_only"]["p99"], 0.15, "increase_is_worse"),
        ("deadline_goodput_per_s", lambda r: r["rates"]["deadline_goodput_per_s"], 0.05, "decrease_is_worse"),
    )
    for name, extract, threshold, direction in definitions:
        av, bv = [extract(r) for r in left], [extract(r) for r in right]
        metric = {"baseline_per_run": av, "candidate_per_run": bv, "provisional_regression_fraction": threshold, "direction": direction}
        if all(is_number(v) and v > 0 for v in av + bv):
            interval = paired_effect_interval(av, bv)
            metric.update(interval=interval, verdict=_gate(interval, threshold, direction))
        else:
            metric.update(verdict="inconclusive", reason="Positive per-run observations required; zero goodput remains a failure in run evidence.")
        metrics[name] = metric
    observed_failure = any(r["status"] == "failed" for r in summaries) or any(m["verdict"] == "failed" for m in metrics.values())
    unresolved = bool(missing) or any(m["verdict"] == "inconclusive" for m in metrics.values())
    verdict = "invalid" if errors else "inconclusive" if nonlive else "failed" if observed_failure else "inconclusive" if unresolved else "pass"
    label = FIXTURE_WARNING if "fixture" in modes else LOCAL_WARNING if "local-functional" in modes else "LIVE PAIRED COMPARISON — PROVISIONAL REGRESSION GATES"
    result = {"schema_version": "1.0", "status": verdict, "evidence_label": label, "pairs": count, "metrics": metrics,
              "errors": sorted(set(errors)), "missing_evidence": sorted(set(missing)), "pair_ids": pair_ids,
              "baseline_runs": [str(p) for p in paths_a], "candidate_runs": [str(p) for p in paths_b],
              "gate_policy": "Latency passes when upper 95% effect CI <= +10% p95/+15% p99; fixed-arrival deadline-goodput passes when lower CI >= -5%. Opposite entire-CI breach fails; a straddling CI is inconclusive.",
              "notes": ["Provisional engineering margins, not production SLOs or certification.",
                        "Goodput is at identical fixed offered arrival rates; this is not sustainable-throughput capacity.",
                        "Intervals compare means of independent per-run metric estimates, not pooled request percentiles.",
                        "Five pairs are a pilot minimum. Small-N bootstrap intervals are exploratory and depend on representative independent runs.",
                        "Within-margin is a noninferiority gate under this design, not proof that systems are identical.",
                        "Reset evidence, state fingerprints and randomized order are retained assertions; verify their producer and procedures."]}
    output_dir = Path(output_dir)
    write_json(output_dir / "comparison.json", result)
    lines = ["# Paired benchmark comparison", "", f"**{label}**", "", f"Verdict: **{verdict.upper()}** ({count} pairs)", "",
             "| Metric | Relative effect | 95% CI | Margin | Verdict |", "|---|---:|---|---:|---|"]
    for name, metric in metrics.items():
        interval = metric.get("interval")
        effect = f"{interval['relative_effect']:+.2%}" if interval else "Not established"
        confidence = f"[{interval['ci95_low']:+.2%}, {interval['ci95_high']:+.2%}]" if interval else "Not established"
        lines.append(f"| {name} | {effect} | {confidence} | {metric['provisional_regression_fraction']:.0%} | {metric['verdict']} |")
    lines.extend(["", result["gate_policy"], ""])
    for name in ("errors", "missing_evidence", "notes"):
        if result[name]:
            lines.extend([f"## {name.replace('_', ' ').title()}", ""])
            lines.extend(f"- {item}" for item in result[name])
            lines.append("")
    (output_dir / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    return result
