"""Reproducible reports compiled exclusively from retained run evidence."""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from .analysis import analyze_run


def _display(value: object) -> str:
    if value is None:
        return "Not established"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _metric_rows(summary: dict) -> list[tuple[str, str, object]]:
    rows = [("counts", key, value) for key, value in sorted(summary["counts"].items())]
    rows.extend(("rates", key, value) for key, value in sorted(summary["rates"].items()))
    rows.extend(("outcomes", key, summary[key]) for key in ("correctness_fraction", "deadline_success_fraction"))
    rows.extend(("backlog", key, summary["backlog"][key]) for key in ("measurement_end_outstanding_started", "oldest_outstanding_started_age_s", "peak_observed_outstanding_started", "net_growth_per_s", "timing_complete"))
    for name, values in summary["latency"].items():
        rows.extend((f"latency.{name}", key, values[key]) for key in ("n", "p50", "p95", "p99", "p99_9"))
    for basis, values in summary["cost"]["by_basis"].items():
        rows.extend((f"cost.{basis}", key, values[key]) for key in ("total_spend", "measurement_and_drain_spend", "per_1000_correct", "per_1000_deadline_correct"))
    return rows


def _markdown(summary: dict, rows: list[tuple]) -> str:
    # Preserve the evidence label in exported documents, not only in a UI badge.
    text = [f"# Benchmark evidence: {summary['run_id']}", "", f"**{summary['evidence_label']}**", "",
            f"Contract status: **{summary['status'].upper()}**", "", summary["status_scope"] + ".", "",
            "This status applies to the recorded run only. It is not a production SLO or a cloud ranking.", ""]
    for title, key in (("Invalid evidence", "errors"), ("Observed failures", "failures"), ("Missing evidence", "missing_evidence"), ("Interpretation", "warnings")):
        if summary[key]:
            text.extend([f"## {title}", ""])
            text.extend(f"- {str(value).replace(chr(10), ' ')}" for value in summary[key])
            text.append("")
    text.extend(["## Measurements", "", "All latency values are seconds. Empty evidence is shown as 'Not established'.", "",
                 "| Group | Metric | Value |", "|---|---|---:|"])
    text.extend(f"| {group} | {key} | {_display(value)} |" for group, key, value in rows)
    text.extend(["", "## Cost boundary", "", summary["cost"]["denominator_boundary"] + ".",
                 "List estimates and actual effective costs are separate. Incomplete actual costs do not produce an actual unit price.",
                 "Zero correct outcomes have undefined unit cost. Deployment, warmup and idle costs remain in total spend.", "",
                 "## Latency interpretation", "", "Completed-observed and correct-only latency are conditional distributions; incomplete requests are not silently assigned a fast duration.",
                 "Deadline success uses all offered logical requests. p99.9 is suppressed below 100,000 observations (an explicit project reporting rule).",
                 "The outcome CSV preserves censoring, attempts and safety failures. Aggregated quantiles use raw observations, never an average of run or replica percentiles.", "",
                 "## Evidence hashes", "", "| Input | SHA-256 |", "|---|---|"])
    text.extend(f"| {name} | `{digest}` |" for name, digest in sorted(summary["input_sha256"].items()))
    text.append("")
    return "\n".join(text)


def _html(summary: dict, rows: list[tuple]) -> str:
    esc = lambda value: html.escape(_display(value), quote=True)
    notices = "".join(f"<section><h2>{esc(title)}</h2><ul>" + "".join(f"<li>{esc(v)}</li>" for v in summary[key]) + "</ul></section>"
                      for title, key in (("Invalid evidence", "errors"), ("Observed failures", "failures"), ("Missing evidence", "missing_evidence"), ("Interpretation", "warnings")) if summary[key])
    metric_rows = "".join(f"<tr><td>{esc(group)}</td><td>{esc(key)}</td><td>{esc(value)}</td></tr>" for group, key, value in rows)
    hashes = "".join(f"<tr><td>{esc(name)}</td><td><code>{esc(value)}</code></td></tr>" for name, value in summary["input_sha256"].items())
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Benchmark evidence — {esc(summary['run_id'])}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 22px;color:#183041;background:#fafcfd}}h1,h2{{line-height:1.2}}.banner{{padding:18px;border:3px solid #b36808;background:#fff2d9;font-weight:700}}.status{{font-size:1.25rem}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{text-align:left;border-bottom:1px solid #d6e0e6;padding:8px 12px}}th{{background:#e6eef4}}td:last-child{{font-variant-numeric:tabular-nums}}code{{overflow-wrap:anywhere}}section{{margin:28px 0}}@media print{{body{{margin:0;max-width:none}}tr{{break-inside:avoid}}}}</style></head>
<body><h1>Benchmark evidence: {esc(summary['run_id'])}</h1>
<div class="banner">{esc(summary['evidence_label'])}</div>
<p class="status">Contract status: <strong>{esc(summary['status'].upper())}</strong></p>
<p>{esc(summary['status_scope'])}. This is not a production SLO, certification or cloud ranking.</p>
{notices}
<section><h2>Measurements</h2><p>Latencies are seconds. Rates use the measurement duration and its arrival cohort; drain time is disclosed in configuration. Counts are unique logical requests except explicitly labeled attempts/events.</p>
<table><thead><tr><th>Group</th><th>Metric</th><th>Value</th></tr></thead><tbody>{metric_rows}</tbody></table></section>
<section><h2>Interpret the distribution</h2><p>Completed-observed and correct-only latency are conditional distributions. Unfinished requests are retained with censor flags and lower bounds in outcomes.csv. Deadline success includes all offered requests. p99.9 is suppressed below 100,000 observations, a project reporting rule. No percentile is obtained by averaging run or replica percentiles.</p></section>
<section><h2>Cost boundary</h2><p>{esc(summary['cost']['denominator_boundary'])}. Estimates and actual costs are separate; incomplete actual costs produce no actual unit price. Zero correct outcomes have undefined unit cost. Total spend still includes deployment, warmup and idle costs.</p></section>
<section><h2>Evidence hashes</h2><table><thead><tr><th>Input</th><th>SHA-256</th></tr></thead><tbody>{hashes}</tbody></table></section>
<p>Generated deterministically from manifest.json, events.ndjson, k6-summary.json and costs.json. Full configuration and evidence qualifications are in analysis.json.</p>
</body></html>
"""


def write_reports(run_dir: Path, output_dir: Path | None = None) -> dict[str, str]:
    """Write JSON, Markdown, HTML and CSV. Return only paths actually created."""
    run_dir = Path(run_dir)
    output_dir = Path(output_dir) if output_dir is not None else run_dir / "reports"
    summary = analyze_run(run_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = _metric_rows(summary)
    paths = {"analysis_json": output_dir / "analysis.json", "report_markdown": output_dir / "report.md",
             "report_html": output_dir / "report.html", "metrics_csv": output_dir / "metrics.csv",
             "outcomes_csv": output_dir / "outcomes.csv"}
    paths["analysis_json"].write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    paths["report_markdown"].write_text(_markdown(summary, rows), encoding="utf-8")
    paths["report_html"].write_text(_html(summary, rows), encoding="utf-8")
    with paths["metrics_csv"].open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["evidence_label", "run_id", "group", "metric", "value"])
        writer.writerows([summary["evidence_label"], summary["run_id"], group, key, "" if value is None else value] for group, key, value in rows)
    fields = ["logical_request_id", "scenario", "status", "correct", "deadline_met", "deadline_s", "latency_s", "ack_latency_s", "verification_delay_s", "generator_lag_s", "attempts", "safety_violation", "completion_censored", "censor_lower_bound_s"]
    with paths["outcomes_csv"].open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["evidence_label", "run_id"] + fields)
        writer.writeheader()
        writer.writerows({"evidence_label": summary["evidence_label"], "run_id": summary["run_id"], **row} for row in summary["outcomes"])
    return {key: str(path.resolve()) for key, path in paths.items()}


def write_parquet(run_dir: Path, output_path: Path) -> str:
    """Optional export, explicitly fail when the external Parquet engine is absent."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("Parquet export requires the optional 'pyarrow' package; no Parquet file was written. JSON and CSV exports require only Python stdlib.") from exc
    summary = analyze_run(Path(run_dir))
    rows = [{"evidence_label": summary["evidence_label"], "run_id": summary["run_id"], **row} for row in summary["outcomes"]]
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    table = table.replace_schema_metadata({
        b"benchmark.evidence_label": summary["evidence_label"].encode("utf-8"),
        b"benchmark.run_id": str(summary["run_id"]).encode("utf-8"),
        b"benchmark.schema_version": b"1.0",
    })
    pq.write_table(table, output_path)
    return str(output_path.resolve())
