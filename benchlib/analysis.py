"""Evidence-first benchmark aggregation; no successful outcome is inferred."""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from .contracts import is_number, parse_timestamp, validate_event

FIXTURE_WARNING = "SYNTHETIC HARNESS VALIDATION — NOT FLUXNOVA PERFORMANCE"
LOCAL_WARNING = "LOCAL FUNCTIONAL VALIDATION — NOT OCI PERFORMANCE"


def percentile(values: list[float], quantile: float) -> float | None:
    """Nearest-rank empirical quantile; do not average precomputed percentiles."""
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between zero and one")
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def distribution(values: list[float]) -> dict:
    return {
        "n": len(values), "unit": "s", "method": "nearest_rank",
        "p50": percentile(values, 0.5), "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "p99_9": percentile(values, 0.999) if len(values) >= 100_000 else None,
        "p99_9_suppressed": len(values) < 100_000,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def merge_histograms(histograms: list[dict]) -> dict:
    """Merge identical, noncumulative bucket schemas, never their quantiles."""
    if not histograms:
        return {"bounds": [], "counts": [], "n": 0}
    bounds = histograms[0]["bounds"]
    if any(h["bounds"] != bounds for h in histograms):
        raise ValueError("cannot merge histograms with different bucket boundaries")
    counts = [0] * (len(bounds) + 1)
    for histogram in histograms:
        bucket_counts = histogram.get("counts", [])
        if len(bucket_counts) != len(counts) or any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in bucket_counts):
            raise ValueError("histogram counts must be nonnegative integers, including overflow bucket")
        counts = [a + b for a, b in zip(counts, bucket_counts)]
    return {"bounds": bounds, "counts": counts, "n": sum(counts)}


def paired_effect_interval(baseline: list[float], candidate: list[float], *, seed: int = 42, resamples: int = 10_000) -> dict:
    """Paired run-level bootstrap of ratio of means; diagnostic, not equivalence.

    Inputs must be independent matched runs, never individual correlated HTTP
    requests. The caller is responsible for the paired experimental design.
    """
    if len(baseline) != len(candidate) or len(baseline) < 5:
        raise ValueError("at least five matched independent run pairs are required")
    if resamples < 100:
        raise ValueError("at least 100 bootstrap resamples are required")
    if any(not is_number(x) or x <= 0 for x in baseline + candidate):
        raise ValueError("paired measurements must be finite and positive")
    rng = random.Random(seed)
    n = len(baseline)
    samples = []
    for _ in range(resamples):
        indices = [rng.randrange(n) for _ in range(n)]
        samples.append(mean(candidate[i] for i in indices) / mean(baseline[i] for i in indices) - 1)
    return {"relative_effect": mean(candidate) / mean(baseline) - 1,
            "ci95_low": percentile(samples, 0.025), "ci95_high": percentile(samples, 0.975),
            "pairs": n, "resamples": resamples, "seed": seed,
            "method": "paired_run_bootstrap_ratio_of_means",
            "qualification": "Exploratory at small run counts; CI overlap is not proof of equivalence."}


def pass_power_k(trials: dict[str, list[bool]], k: int = 5) -> dict:
    """Empirical fraction of tasks passing ALL k prespecified independent trials."""
    if k < 1:
        raise ValueError("k must be positive")
    if any(any(not isinstance(v, bool) for v in values) for values in trials.values()):
        raise ValueError("trial outcomes must be explicit booleans")
    eligible = [values for values in trials.values() if len(values) >= k]
    return {"metric": f"pass^{k}", "eligible_tasks": len(eligible),
            "insufficient_trial_tasks": len(trials) - len(eligible),
            "value": sum(all(values[:k]) for values in eligible) / len(eligible) if eligible else None,
            "policy": "first k prespecified trials; every trial must pass"}


def wilson_interval(successes: int, total: int) -> dict:
    if total == 0:
        return {"low": None, "high": None, "method": "wilson_95", "n": 0}
    z = 1.959963984540054
    p = successes / total
    divisor = 1 + z * z / total
    center = (p + z * z / (2 * total)) / divisor
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / divisor
    return {"low": max(0.0, center - radius), "high": min(1.0, center + radius),
            "method": "wilson_95", "n": total,
            "qualification": "Descriptive binomial interval assumes independent, representative cases; not a safety guarantee."}


def _read_json(path: Path, errors: list[str], default: dict | None = None) -> dict:
    if not path.exists():
        return {} if default is None else default
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        return value
    except (OSError, ValueError) as exc:
        errors.append(f"{path.name}: {exc}")
        return {}


def _delta(start: dict | None, end: dict | None) -> float | None:
    if not start or not end:
        return None
    if is_number(start.get("elapsed_s")) and is_number(end.get("elapsed_s")):
        value = end["elapsed_s"] - start["elapsed_s"]
    else:
        begin, finish = parse_timestamp(start.get("at")), parse_timestamp(end.get("at"))
        if begin is None or finish is None:
            return None
        value = (finish - begin).total_seconds()
    return value if value >= 0 else None


def _event_order(event: dict) -> tuple:
    stamp = parse_timestamp(event.get("at"))
    return (event.get("elapsed_s", stamp.timestamp() if stamp else 0), event["_index"])


def _verify_seal(run_dir: Path, errors: list[str]) -> dict:
    """Check the independently stored archive hashes without following paths out."""
    present = (run_dir / "checksums.json").is_file()
    if not present:
        return {"seal_present": False, "verified": False, "mismatches": []}
    seal = _read_json(run_dir / "checksums.json", errors)
    mismatches = []
    required = {"manifest.json", "events.ndjson", "k6-summary.json", "costs.json"}
    for name in sorted(required - set(seal)):
        mismatches.append(f"missing hash: {name}")
    for name, expected in seal.items():
        if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
            mismatches.append("invalid archive path")
            continue
        path = run_dir / name
        if not path.is_file():
            mismatches.append(f"missing file: {name}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            mismatches.append(f"changed file: {name}")
    if mismatches:
        errors.append("archive checksum mismatch; evidence changed or is incomplete")
    return {"seal_present": True, "verified": not mismatches and bool(seal), "mismatches": mismatches,
            "qualification": "Detects changes relative to the stored seal; it is not an authenticated digital signature."}


def _costs(raw: dict, correct: int, deadline_correct: int, errors: list[str]) -> dict:
    totals: dict[str, float] = defaultdict(float)
    allocations: dict[str, float] = defaultdict(float)
    currencies = set()
    records = raw.get("records", [])
    if not isinstance(records, list):
        errors.append("costs.records must be a list")
        records = []
    for record in records:
        if not isinstance(record, dict) or not is_number(record.get("amount")):
            errors.append("cost record requires a finite amount")
            continue
        basis = record.get("basis")
        allocation = record.get("allocation")
        if basis not in {"list_estimate", "effective_actual"}:
            errors.append("cost record basis must be list_estimate or effective_actual")
            continue
        if allocation not in {"measurement", "warmup", "drain", "deployment", "idle"}:
            errors.append("cost record allocation is unsupported")
            continue
        currency = record.get("currency", raw.get("currency"))
        currencies.add(currency)
        totals[basis] += record["amount"]
        allocations[f"{basis}:{allocation}"] += record["amount"]
    if currencies - {"USD"}:
        errors.append("cost records require USD; currency conversion must be explicit upstream")
    # Only measurement + drain costs share the start-cohort denominator. Warmup,
    # deployment and idle costs remain disclosed separately and in total spend.
    unit = {}
    for basis in ("list_estimate", "effective_actual"):
        present = basis in totals
        cohort_cost = allocations.get(f"{basis}:measurement", 0) + allocations.get(f"{basis}:drain", 0)
        usable = present and (basis == "list_estimate" or raw.get("complete") is True)
        unit[basis] = {"total_spend": totals.get(basis) if present else None,
                       "measurement_and_drain_spend": cohort_cost if present else None,
                       "per_1000_correct": cohort_cost * 1000 / correct if usable and correct else None,
                       "per_1000_deadline_correct": cohort_cost * 1000 / deadline_correct if usable and deadline_correct else None,
                       "provisional": basis == "list_estimate" or raw.get("complete") is not True}
    return {"currency": "USD", "complete": raw.get("complete") is True,
            "records": records, "by_allocation": dict(sorted(allocations.items())), "by_basis": unit,
            "denominator_boundary": "measurement-start cohort; measurement and drain cost; all failed work costs retained",
            "undefined_unit_cost": correct == 0}


def analyze_run(run_dir: Path) -> dict:
    """Read run evidence and return a deterministic, JSON-serializable summary.

    A pass is only a single-run evidence contract pass. It is never a production
    certification or a statistical comparison against another environment.
    """
    run_dir = Path(run_dir)
    errors: list[str] = []
    missing: list[str] = []
    failures: list[str] = []
    warnings: list[str] = []
    integrity = _verify_seal(run_dir, errors)
    manifest = _read_json(run_dir / "manifest.json", errors)
    if not manifest:
        errors.append("manifest.json is required")
    config = manifest.get("config", manifest)
    if not isinstance(config, dict):
        errors.append("manifest config must be an object")
        config = {}
    workload = config.get("workload", {})
    if not isinstance(workload, dict):
        errors.append("manifest workload must be an object")
        workload = {}
    run = config.get("run", {})
    if not isinstance(run, dict):
        run = {}
    run_id = run.get("id", manifest.get("run_id", run_dir.name))
    declared_mode = run.get("mode", manifest.get("mode"))
    fixture = declared_mode == "fixture" or manifest.get("synthetic") is True
    local = declared_mode == "local-functional" and not fixture
    if declared_mode not in {"live", "fixture", "local-functional"} and not fixture:
        missing.append("run mode is not declared; provenance cannot be established")
    if not integrity["seal_present"] and not fixture:
        missing.append("checksums.json is missing; live evidence has not been sealed")
    source_summary = _read_json(run_dir / "k6-summary.json", errors)
    if declared_mode == "live" and source_summary.get("golden_validated") is not True:
        missing.append("golden correctness gate lacks passed, hashed evidence for this runtime/database scope")
    observation = source_summary.get("completion_observation")
    if observation:
        warnings.append("Completion timing boundary: " + str(observation))
    if source_summary.get("warning"):
        warnings.append(str(source_summary["warning"]))
    generator_validated = source_summary.get("generator_validated")
    telemetry_complete = source_summary.get("telemetry_complete")
    if generator_validated is False:
        errors.append("load generator validation failed")
    elif generator_validated is not True:
        missing.append("load generator validation is missing")
    if telemetry_complete is not True:
        missing.append("required telemetry is missing or incomplete")
    duration = source_summary.get("measurement_duration_s", workload.get("duration_s"))
    if not is_number(duration) or duration <= 0:
        errors.append("positive measurement duration is required")
        duration = None
    default_deadline = workload.get("deadline_s")
    if not is_number(default_deadline) or default_deadline <= 0:
        missing.append("positive workload deadline_s is required for deadline-goodput")
        default_deadline = None
    events: list[dict] = []
    event_path = run_dir / "events.ndjson"
    if not event_path.exists():
        missing.append("events.ndjson is missing; a k6 HTTP summary cannot establish workflow correctness")
    else:
        for number, line in enumerate(event_path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                validation = validate_event(event)
                if validation:
                    errors.extend(f"events.ndjson:{number}: {error}" for error in validation)
                    continue
                if event["run_id"] != run_id:
                    errors.append(f"events.ndjson:{number}: run_id does not match manifest")
                    continue
                event["_index"] = number
                events.append(event)
            except ValueError:
                errors.append(f"events.ndjson:{number}: invalid JSON")
    # Warmup identities never enter the measurement cohort, even when their
    # completions are observed during the measurement or drain period.
    warmup_ids = {e["logical_request_id"] for e in events if e.get("phase") == "warmup" and e["event"] in {"scheduled", "started"}}
    cohort_ids = {e["logical_request_id"] for e in events if e.get("phase", "measurement") == "measurement" and e["event"] in {"scheduled", "started", "dropped"}}
    if warmup_ids & cohort_ids:
        errors.append("logical request IDs must not be reused across warmup and measurement")
    measurement_ids = cohort_ids - warmup_ids
    by_id: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        if event["logical_request_id"] in measurement_ids and event.get("phase") != "warmup":
            by_id[event["logical_request_id"]].append(event)
        elif event["logical_request_id"] not in warmup_ids and event.get("phase") != "warmup":
            missing.append("terminal or accepted events without a scheduled/started measurement identity")
    outcomes = []
    latencies: list[float] = []
    correct_latencies: list[float] = []
    ack_latencies: list[float] = []
    generator_lags: list[float] = []
    verification_delays: list[float] = []
    outstanding_windows: list[tuple[float, float | None]] = []
    counts = Counter()
    for logical_id, records in sorted(by_id.items()):
        records.sort(key=_event_order)
        kinds = defaultdict(list)
        for event in records:
            kinds[event["event"]].append(event)
        scheduled = (kinds["scheduled"] or kinds["started"] or kinds["dropped"])[0]
        started = kinds["started"][0] if kinds["started"] else None
        accepted = kinds["accepted"][0] if kinds["accepted"] else None
        counts["scheduled_observed"] += bool(kinds["scheduled"])
        counts["started"] += started is not None
        counts["accepted"] += accepted is not None
        counts["dropped"] += bool(kinds["dropped"])
        attempts = {e.get("attempt_id") for e in kinds["started"] if e.get("attempt_id")}
        counts["attempts"] += len(attempts) if attempts else len(kinds["started"])
        counts["duplicate_attempts"] += max(0, (len(attempts) if attempts else len(kinds["started"])) - 1)
        counts["error_events"] += len(kinds["error"])
        status_events = [e for e in records if e["event"] in {"completed", "outcome", "error"} and e.get("status") in {"correct", "expected_rejection", "incorrect"}]
        # Any incorrect observation or policy violation is sticky; subsequent
        # successful retries must not erase an observed unsafe/wrong outcome.
        incorrect = any(e.get("status") == "incorrect" for e in status_events)
        safe_fail = any(e.get("details", {}).get("safety_violation") is True or e.get("details", {}).get("policy_violation") is True or bool(e.get("details", {}).get("violations")) for e in records)
        if safe_fail:
            counts["unsafe_outcomes"] += 1
        definitive = [e for e in status_events if e["event"] in {"completed", "outcome"}]
        terminal = definitive[-1] if definitive else None
        correct = terminal is not None and terminal.get("status") in {"correct", "expected_rejection"} and not incorrect and not safe_fail
        state = "incorrect" if incorrect or safe_fail else (terminal["status"] if terminal else "unknown")
        # A later outcome assertion may confirm an earlier completed event. Its
        # timestamp is observation lag, not workflow completion time.
        completion = kinds["completed"][0] if kinds["completed"] else terminal
        latency = completion.get("latency_s") if completion else None
        if not is_number(latency):
            latency = _delta(scheduled, completion)
        if latency is not None:
            latencies.append(latency)
        verification = completion.get("details", {}).get("validation_elapsed_s") if completion else None
        if not is_number(verification) or verification < 0:
            verification = _delta(completion, terminal) if completion and terminal and terminal is not completion else None
        if verification is not None:
            verification_delays.append(verification)
        if started and is_number(started.get("elapsed_s")):
            completion_time = completion.get("elapsed_s") if completion else None
            if not is_number(completion_time) and completion and latency is not None and is_number(scheduled.get("elapsed_s")):
                completion_time = scheduled["elapsed_s"] + latency
            outstanding_windows.append((started["elapsed_s"], completion_time if is_number(completion_time) else None))
        deadline = scheduled.get("deadline_s", default_deadline)
        within = correct and latency is not None and deadline is not None and latency <= deadline
        if correct:
            counts["correct"] += 1
            counts["deadline_correct"] += within
            counts["expected_rejections"] += state == "expected_rejection"
            if latency is not None:
                correct_latencies.append(latency)
            else:
                counts["missing_completion_latency"] += 1
        if incorrect or safe_fail:
            counts["incorrect"] += 1
        if state == "unknown":
            counts["unknown"] += 1
        counts["completed_observed"] += bool(kinds["completed"])
        if correct and latency is not None and deadline is not None and not within:
            counts["deadline_misses"] += 1
        lag = _delta(scheduled, started)
        ack = _delta(started, accepted)
        if lag is not None:
            generator_lags.append(lag)
        if ack is not None:
            ack_latencies.append(ack)
        observed_end = duration + workload.get("drain_s", 0) if duration is not None and is_number(workload.get("drain_s", 0)) else None
        lower_bound = max(0, observed_end - scheduled["elapsed_s"]) if observed_end is not None and is_number(scheduled.get("elapsed_s")) else None
        outcomes.append({"logical_request_id": logical_id, "scenario": scheduled["scenario"],
                         "status": state, "correct": correct, "deadline_met": bool(within),
                         "deadline_s": deadline, "latency_s": latency, "ack_latency_s": ack, "verification_delay_s": verification,
                         "generator_lag_s": lag, "attempts": len(attempts) if attempts else len(kinds["started"]),
                         "safety_violation": safe_fail, "completion_censored": completion is None,
                         "censor_lower_bound_s": lower_bound if completion is None else None})
    offered = len(by_id)
    summary_scheduled = source_summary.get("scheduled")
    for key in ("scheduled", "started", "dropped"):
        if key in source_summary and (not is_number(source_summary[key]) or source_summary[key] < 0 or int(source_summary[key]) != source_summary[key]):
            errors.append(f"summary {key} must be a nonnegative integer")
    if is_number(summary_scheduled):
        if int(summary_scheduled) != summary_scheduled or summary_scheduled < offered:
            errors.append("summary scheduled count is nonintegral or smaller than observed logical requests")
        else:
            offered = int(summary_scheduled)
    summary_dropped = source_summary.get("dropped")
    if is_number(summary_dropped):
        if int(summary_dropped) != summary_dropped or summary_dropped < counts["dropped"]:
            errors.append("summary dropped count is nonintegral or smaller than observed drops")
        else:
            counts["dropped"] = int(summary_dropped)
    unobserved = max(0, offered - len(by_id))
    counts["unknown"] += unobserved
    if counts["started"] + counts["dropped"] > offered:
        errors.append("started plus dropped exceeds offered logical requests")
    if source_summary.get("started") is not None and source_summary["started"] != counts["started"]:
        missing.append("summary started count does not reconcile with observed logical starts")
    if counts["scheduled_observed"] < len(by_id):
        warnings.append("Some arrivals lack scheduled timestamps; latency starts at observed send and excludes unmeasured generator lag.")
    if counts["incorrect"]:
        failures.append("incorrect or unsafe final outcomes observed")
    if counts["deadline_misses"]:
        failures.append("correct outcomes exceeded the configured deadline")
    if counts["dropped"]:
        failures.append("offered arrivals were dropped")
    if counts["unknown"]:
        missing.append("one or more offered requests have no proven final outcome")
    if counts["missing_completion_latency"]:
        missing.append("proven outcomes lack completion latency")
    if offered == 0:
        missing.append("no measurement requests were observed")
    if unobserved:
        missing.append("aggregate scheduled count contains requests without lifecycle evidence")
    cost_summary = _costs(_read_json(run_dir / "costs.json", errors), counts["correct"], counts["deadline_correct"], errors)
    if not cost_summary["complete"]:
        warnings.append("Actual cost evidence is incomplete; any estimate is provisional and no actual unit cost is asserted.")
    if fixture:
        warnings.insert(0, FIXTURE_WARNING)
    elif local:
        warnings.insert(0, LOCAL_WARNING)
    warnings.append("This is one run. Cross-system or regression claims require independent repeated runs and uncertainty analysis.")
    for name in ("started", "accepted", "attempts", "duplicate_attempts", "dropped", "correct", "incorrect", "unknown", "deadline_correct", "deadline_misses", "unsafe_outcomes", "error_events", "completed_observed", "expected_rejections"):
        counts.setdefault(name, 0)
    counts["offered"] = offered
    counts["unobserved_offered"] = unobserved
    counts["right_censored"] = sum(o["completion_censored"] for o in outcomes) + unobserved
    status = "invalid" if errors else "failed" if failures else "inconclusive" if missing else "pass"
    input_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(run_dir.iterdir()) if p.name in {"manifest.json", "events.ndjson", "k6-summary.json", "costs.json"} and p.is_file()}
    rates = {f"{name}_per_s": counts[name] / duration if duration else None for name in ("offered", "started", "accepted", "correct", "deadline_correct")}
    rates["deadline_goodput_per_s"] = rates["deadline_correct_per_s"]
    backlog = {"measurement_end_outstanding_started": None, "oldest_outstanding_started_age_s": None,
               "peak_observed_outstanding_started": None, "net_growth_per_s": None,
               "timed_started_requests": len(outstanding_windows), "timing_complete": len(outstanding_windows) == counts["started"],
               "scope": "Observed started-to-completed workflows; engine/database job queues require separate telemetry."}
    if duration is not None and outstanding_windows:
        remaining = [begin for begin, end in outstanding_windows if begin <= duration and (end is None or end > duration)]
        timeline = [(begin, 1) for begin, _ in outstanding_windows if begin <= duration]
        timeline += [(end, -1) for _, end in outstanding_windows if end is not None and end <= duration]
        grouped = defaultdict(int)
        for at, delta in timeline:
            grouped[at] += delta
        pending = peak = 0
        for at in sorted(grouped):
            pending += grouped[at]
            peak = max(peak, pending)
        backlog.update({"measurement_end_outstanding_started": len(remaining),
                        "oldest_outstanding_started_age_s": max((duration - begin for begin in remaining), default=0),
                        "peak_observed_outstanding_started": peak, "net_growth_per_s": len(remaining) / duration})
    return {"schema_version": "1.0", "run_id": run_id, "mode": "fixture" if fixture else "local-functional" if local else "live",
            "evidence_label": FIXTURE_WARNING if fixture else LOCAL_WARNING if local else "LIVE RUN EVIDENCE — SINGLE-RUN CONTRACT CHECK",
            "status": status, "status_scope": "single-run evidence, correctness and configured deadline contract",
            "errors": sorted(set(errors)), "failures": sorted(set(failures)), "missing_evidence": sorted(set(missing)),
            "warnings": warnings, "measurement_duration_s": duration, "counts": dict(counts), "rates": rates,
            "correctness_fraction": counts["correct"] / offered if offered else None,
            "deadline_success_fraction": counts["deadline_correct"] / offered if offered else None,
            "correctness_interval": wilson_interval(counts["correct"], offered),
            "latency": {"completed_observed": distribution(latencies), "correct_only": distribution(correct_latencies),
                        "acknowledgement": distribution(ack_latencies), "generator_lag": distribution(generator_lags),
                        "verification_delay": distribution(verification_delays)},
            "cost": cost_summary, "backlog": backlog, "outcomes": outcomes, "input_sha256": input_hashes,
            "configuration": config, "evidence_integrity": integrity, "telemetry_complete": telemetry_complete is True,
            "generator_validated": generator_validated is True}
