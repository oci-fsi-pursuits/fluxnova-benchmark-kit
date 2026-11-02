# Start using the benchmark toolkit

Start with reporting, then choose a functional or measured study. Use a source checkout; a standalone installed Python wheel is not currently a supported distribution of all workload/assets files.

For the simplest explanation, open [the illustrated benchmark guide](benchmark-guide.md), or open `docs/benchmark-guide.html` locally. `python -m benchlib.cli explain W02` describes the baseline, change, metric and result for one study. The [supported-path contract](benchmark-standard.md) separates sample reporting from real functional execution and measured campaigns.

## Read and open a report

With Git and Python 3.12+ installed:

```bash
git clone https://github.com/oci-fsi-pursuits/fluxnova-benchmark-kit.git
cd fluxnova-benchmark-kit
python -m benchlib.cli demo
```

The command prints HTML, JSON, CSV and Markdown paths under `runs/`. Its generated sample data demonstrates reporting, including unsuccessful work; it is not a measured Fluxnova result.

```bash
python scripts/open_report.py --path "PATH_TO_REPORT.html"
python scripts/open_report.py --path docs/assets/fluxnova-research-brief-20261028.pdf
python scripts/open_report.py --path docs/results/w02-mixed/report.html
```

The launcher opens a local file in your default viewer. It starts no server, cloud resource or workload. Copy saved HTML and adjacent JSON/manifest files together. Historical private study exports referenced in documentation are not included in a fresh clone.

## Run a functional workload

Follow the [reproduction guide](reproduce.md) to pin public artifacts, build containers and run golden checks. The isolated local lab uses embedded H2 and loopback-only access. Its demo credentials do not belong in public or production services. This lab does not reproduce the OCI PostgreSQL performance environment.

## Measure a change

Declare the inputs, baseline/treatment, environment, deadlines and validity rules using the [protocol](benchmark-protocol.md). Use your own isolated resources and synthetic data. The [OCI runbook](campaign-runbook.md) is the implemented cloud path; [portability](portability.md) lists work required elsewhere.

A budget field records a planning constraint, not a provider billing cutoff. Retain evidence and delete owned temporary resources by recorded identity after testing. Ending load does not stop VM or database billing.

## Build the one-page brief

The optional authoring path needs `reportlab` and `pypdf`; the offline demo does not. Use a fresh output filename:

```bash
python scripts/build_research_brief.py --content presentations/fluxnova-team-brief-20261028.json --logo docs/assets/fluxnova-logo.png --output runs/research-brief.pdf --audit runs/research-brief-audit.json
```

Render and inspect the PDF before sharing. Windows uses Arial when available; other systems use Helvetica and need a fresh layout check.
