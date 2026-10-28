"""Bounded sequential campaign profiles, with failure-aware discovery."""
import copy
import time
from pathlib import Path
from .runner import run_k6
from .analysis import analyze_run
from .report import write_reports
from .util import write_json

def planned_wall_seconds(config):
    work, execution, evidence = config["workload"], config.get("execution", {}), config.get("evidence", {})
    total = sum(work.get(key, 0) for key in ("warmup_s", "duration_s", "drain_s")) + 120
    if evidence.get("auto_calibrate", True):
        total += max(3, evidence.get("calibration_duration_s", 10)) + 20
    if execution.get("reset_command"):
        total += execution.get("reset_timeout_s", 600)
    if execution.get("evidence_command"):
        total += execution.get("evidence_timeout_s", 120)
    return total

def planned_runs(config, profile):
    base = copy.deepcopy(config)
    if profile == "smoke":
        for scenario in ("minimal", "variables", "async"):
            run = copy.deepcopy(base)
            run["workload"].update(scenario=scenario, rate=1, duration_s=10, warmup_s=0, drain_s=30, deadline_s=1 if scenario == "minimal" else 2)
            yield run
    elif profile == "discovery":
        for rate in (1, 2, 5, 10, 20, 50, 100):
            run = copy.deepcopy(base)
            run["workload"].update(rate=rate, duration_s=300)
            yield run
    elif profile == "baseline":
        for _ in range(5):
            run = copy.deepcopy(base)
            run["workload"].update(duration_s=1800, warmup_s=600)
            yield run
    else:
        run = copy.deepcopy(base)
        run["workload"]["duration_s"] = 28800 if profile == "soak" else 86400
        yield run

def execute_campaign(config, profile, output_root, local=False):
    series = list(planned_runs(config, profile))
    pair_plan = config.get("comparison", {}).get("pair_plan")
    if pair_plan is not None and (not isinstance(pair_plan, list) or len(pair_plan) != len(series) or not all(isinstance(entry, dict) for entry in pair_plan)):
        raise ValueError("comparison.pair_plan must provide one explicit pair design per planned run")
    wall_seconds = sum(planned_wall_seconds(c) for c in series)
    if not local:
        reserved = config.get("estimated_cost", {}).get("duration_hours")
        if not isinstance(reserved, (int, float)) or reserved * 3600 < wall_seconds:
            raise ValueError("estimated_cost.duration_hours must cover the full campaign including calibration, reset, collection and bounded run overhead")
    if profile == "baseline" and not local:
        reset = config.get("execution", {}).get("reset_command")
        if not reset or not isinstance(reset, list):
            raise ValueError("Cloud baselines require execution.reset_command as argv to restore identical seeded state before every run")
    campaign_id = "campaign-" + str(time.time_ns())
    results = []
    last_valid_rate = None
    for number, item in enumerate(series):
        # Runner executes the reset after creating the evidence directory so
        # reset.json/log survive failure and enter the checksum seal.
        if pair_plan is not None:
            item.setdefault("comparison", {}).update(pair_plan[number])
        item["run"] = {"mode": "local-functional" if local else "live"}
        work = item["workload"]
        item.setdefault("execution", {})["max_run_seconds"] = sum(work.get(k, 0) for k in ("warmup_s", "duration_s", "drain_s")) + 120
        directory = run_k6(item, f"{campaign_id}-{number:02d}", output_root)
        write_reports(directory)
        result = analyze_run(directory)
        results.append({"run": str(directory), "rate": work["rate"], "status": result.get("status"), "scenario": work["scenario"]})
        if result.get("status") == "pass" and not local:
            last_valid_rate = work["rate"]
        if result.get("status") != "pass" and not (local and profile == "smoke"):
            break
    summary = {"campaign_id": campaign_id, "profile": profile, "results": results, "last_valid_rate": last_valid_rate,
               "capacity_claim": "not established; fixed-arrival observations require repeated steady-state and backlog analysis",
               "budgeted_maximum_wall_seconds": wall_seconds,
               "scope": "local-functional" if local else "cloud"}
    write_json(Path(output_root) / (campaign_id + ".json"), summary)
    return summary
