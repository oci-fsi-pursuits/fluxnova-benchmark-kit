# Measurement contract 1.0

This contract defines comparable evidence from deployment through business outcome and cost. It standardizes the workload and measurements; it does not imply that equal vCPU counts, managed database products, or cloud topology have equal performance. A fixture is always labeled **SYNTHETIC HARNESS VALIDATION — NOT FLUXNOVA PERFORMANCE** in every exported artifact.

A real engine exercised locally uses mode `local-functional` and the label **LOCAL FUNCTIONAL VALIDATION — NOT OCI PERFORMANCE**. It establishes application/harness behavior only, especially when using embedded H2. Neither functional run nor synthetic fixture becomes a cloud performance result.

## Inputs and immutable evidence

Each run retains `manifest.json` containing the exact `config` snapshot and immutable application/image, source, process, dataset and configuration hashes. Record CPU architecture/model/SMT, allocated resources and cgroup quota, JVM/JDBC/engine settings, database product/version/HA, region, network path, load-generator version and telemetry sampling. A different topology or durability setting is a different comparison class. Keep secrets in environment variables or the deployment identity, never in manifests or URLs.

Bootstrap provisioning can accept `artifact.image_source=maven-distribution` before a custom container exists, but only with an HTTPS distribution URL, its SHA-256 and an immutable Java base image. The build must verify those inputs and pin its resulting container digest before a cloud benchmark run. The configuration validator's `require_built_image=False` is exclusively for this bootstrap path; the default and all cloud runs require `artifact.image` by digest. An upstream reference SHA is not proof that a published binary was built from it; preserve any unverified provenance qualification.

`events.ndjson` is the business evidence ledger, `k6-summary.json` holds aggregate generator evidence, and `costs.json` holds allocated costs. Original billing exports and observability exports remain additional evidence. Input hashes in the report allow later integrity checks. The schemas describe wire format; `benchlib.contracts` implements the dependency-free checks and cloud preflight rules. Extra provider fields are allowed without changing the core schema.

`checksums.json` seals the input files after collection. The analyzer checks it before reporting and invalidates changed or missing sealed evidence. A missing seal makes a non-fixture run inconclusive. The stored seal detects later edits relative to that copy; it is not an authenticated digital signature or protection against an attacker replacing both evidence and its seal.

The lifecycle of a request is `scheduled → started → accepted → completed → outcome`. `completed` denotes durable terminal completion; `outcome` is the independently verified business assertion and may arrive later. Emit an explicit `correct`, `incorrect`, `expected_rejection`, or `unknown` status. Acceptance, an HTTP 2xx, and a completed engine instance alone do not prove correctness. A valid, policy-required rejection can be a correct outcome. A transient transport error should use `unknown`; use `incorrect` only when a wrong business outcome is established.

Every record carries `run_id`, `logical_request_id`, scenario, and a timestamp or `elapsed_s`. Retries have distinct `attempt_id` values under one logical request. Workload adapters must verify final state and side effects; duplicate attempts cannot create duplicate goodput. Record any unauthorized or duplicate side effect as `details.safety_violation=true` or `details.policy_violation=true`, with `details.violations` describing it. An incorrect or unsafe observation is sticky even if a retry later succeeds.

## Timing, arrival cohorts and denominators

- Use open-arrival load for independent external arrivals. Closed users are a separate profile for bounded human/operator journeys. Preallocate and validate generator capacity; retain scheduled-versus-started lag and dropped arrivals. [k6 load models](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/), [dropped iterations](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/dropped-iterations/).
- For the measurement phase, `elapsed_s` is seconds from measurement start, measured by the same monotonic observer clock. Use timezone-aware UTC `at` for correlation. Warmup IDs are disjoint and excluded. Drain events belong to the cohort of requests scheduled during measurement. New work must stop at measurement end.
- `latency_s` on a completion is scheduled-arrival to durable completion. It excludes time spent subsequently verifying an already completed state. If unavailable, analysis subtracts lifecycle timestamps. Acknowledgement latency is send-to-accept, and generator lag is schedule-to-send. Do not subtract unsynchronized server clocks or add nested/parallel spans to obtain wall time.
- The current REST observer detects durable completion when a history poll first returns `endTime`. Its local observation time is an upper bound on completion that includes polling and network delay; it is not the engine's internal elapsed time. k6 preserves this boundary in `completed.elapsed_s`, `completed.at`, and `latency_s`, then records later variable/blob/ledger verification in `details.validation_elapsed_s` and `details.verified_at`. Reports expose verification delay separately. A prompt completion with unresolved verification remains an unknown business outcome.
- `workload.poll_interval_ms` controls the delay between incomplete history observations (default 100 ms). The first poll remains immediate. Increasing this interval can reduce observer traffic while delaying reported completion; do not attribute that observation delay to engine execution. `measurement_history_polls`, `measurement_history_poll_errors` and `measurement_engine_requests` retain raw request counts for measurement-start iterations, including their drain activity and excluding warmup. History errors count non-200 poll responses. Engine requests include starts, history polls and later variable/blob/activity checks; separate simulator receipt requests are excluded. For an entirely correct async cohort, engine requests equal starts plus history polls plus two verification requests per completion. Counters are diagnostic evidence, not substitutes for lifecycle/count validation.
- Continue observing the measurement cohort for the declared `drain_s`, then preserve unresolved state. An unfinished completion is right-censored with its observation lower bound. A completed request with an unknown business outcome is separately unresolved correctness, not automatically a completion censor. Do not turn a timeout into a completed latency sample. [NIST censoring](https://www.itl.nist.gov/div898/handbook/apr/section1/apr131.htm).
- The offered denominator includes generator-dropped and unobserved requests supplied by the aggregate scheduled count. Throughput measures unique logical outcomes. Attempt counts and retries are separate. A missing ledger entry never disappears from the denominator.

| Metric | Contract |
|---|---|
| Offered / started / accepted rate | Corresponding unique logical request count divided by measurement seconds |
| Correct completion rate | Unique verified correct durable outcomes for the measurement-start cohort divided by measurement seconds |
| Deadline-goodput | The same numerator restricted to outcomes meeting the configured deadline |
| Correctness fraction | Correct outcomes / all offered logical requests |
| Deadline success fraction | Correct outcomes within deadline / all offered logical requests |
| Latency | Separate distributions for all observed completions, correct completions, acknowledgements, generator lag and subsequent verification delay |
| Incompleteness | Unknown outcomes, censored completions and unobserved offered requests, reported explicitly |
| Observed outstanding work | Started but not completed at measurement end, oldest age and peak count; separate from job/database queues |
| Recovery | Fault timestamp, service restoration, backlog drainage, loss and duplicate effects; fault runs are a distinct workload profile |

Goodput is our workflow-specific SLI, consistent with [good-event/total-event SLI practice](https://sre.google/workbook/implementing-slos/). The cohort rate can include outcomes in the drain window: it describes the offered cohort, not an instantaneous completion rate. A sustainable-capacity claim also needs stable backlog and repeated steady-state trials; a single run cannot establish it.

The current analyzer uses a strict single-run check: zero incorrect/unsafe outcomes, zero drops, every offered request proven correct, and every proven completion within its deadline. This is a harness default, not a production SLO. Outcome is `invalid` for malformed/inconsistent evidence or failed generator validation, `failed` for observed correctness/safety/drop/deadline failures, `inconclusive` for missing generator validation/telemetry/outcomes, otherwise `pass`. Invalid evidence takes precedence, while all failures remain visible. `k6-summary.json` must explicitly set `generator_validated=true` and `telemetry_complete=true`; these assertions must be supported by retained utilization/collector evidence for a live run, never guessed from HTTP success.

## Statistics and cross-system comparisons

Use fixed seeded datasets and workload traces. Reset equivalent history/data before paired comparisons; disclose independent tuning and compare like durability/network paths. Separate warmup from measurement and cold-start experiments. Start with five paired, randomized A/B runs as a pilot, then choose a fixed sample size from observed variation and the predeclared precision/budget. Never repeatedly peek and stop as soon as a favorable result appears. An imprecise result remains inconclusive. [Kalibera and Jones, rigorous benchmarking](https://kar.kent.ac.uk/33611/).

The analyzer uses nearest-rank empirical p50/p95/p99 from retained observations and shows sample count. p99.9 is suppressed below 100,000 observations, an explicit project reporting floor providing roughly 100 tail observations, not an industry rule or sufficient guarantee of precision. Percentiles from different replicas or runs are never averaged to produce a pooled percentile. The histogram merge helper accepts identical, noncumulative bucket boundaries; dashboards should use compatible native/explicit histograms. [Prometheus histogram guidance](https://prometheus.io/docs/practices/histograms/).

`paired_effect_interval` provides a seeded paired bootstrap of the ratio of run means for at least five independent matched run pairs. It is a tool for analysis, not automatic proof of equivalence; CI overlap or a nonsignificant test does not show systems are equal. Request-level observations inside one run are not independent experiment repetitions. Correctness proportions include a descriptive Wilson 95% interval with its independence assumption; zero observed failures does not establish zero failure probability.

For future stochastic agent tests, `pass_power_k` measures the fraction of tasks whose first k prespecified trials **all** succeed. Report pass^1 and pass^5, unsafe-action counts and final-state assertions. This differs from pass@k, where any successful attempt is enough. Tasks lacking k trials remain explicitly insufficient, never counted as reliable. [tau-bench](https://arxiv.org/html/2406.12045v1).

## Telemetry and cost portability

Use OTLP, version-pinned semantic conventions, seconds for duration, and provider/region/resource attributes. Keep request and process IDs in traces/logs/ledger instead of high-cardinality metric labels. Capture generator saturation, JVM CPU/heap/GC, CPU quota/throttling, DB pool/locks/I/O, job queue/age, backlog, observability drop counts and model/tool durations. Export all required outcome counters independently of trace sampling. [OTel metric conventions](https://opentelemetry.io/docs/specs/semconv/general/metrics/), [cloud resource conventions](https://opentelemetry.io/docs/specs/semconv/resource/cloud/).

Keep the original billing export using its declared FOCUS version. The small `costs.json` schema is **FOCUS-inspired allocation data, not a FOCUS conformance claim**. Preserve source/resource/charge-period references. `list_estimate` and `effective_actual` remain separate, in USD; mixed currencies require an explicit upstream conversion. Effective cost includes applicable discounts and commitment amortization. [FOCUS 1.3](https://focus.finops.org/docs/specification/v1-3/), [effective cost](https://focus.finops.org/docs/specification/v1-3/features/effective-cost-analysis/).

Include runtime, database, storage/IO, load balancer, network, telemetry, model/tool and generator costs. Declare allocation and billing granularity. Unit cost uses measurement plus drain allocation over the matching measurement-start outcome cohort; total spend also discloses deployment, warmup and idle. Failed work costs stay in the numerator. Actual unit cost is suppressed until the cost ledger explicitly declares completeness. Zero correct outcomes produce undefined unit cost, never zero. Public-list estimates remain provisional even when billing completeness is true.

Match logical resources within architecture and publish equal-cost comparisons separately. Do not equate OCI OCPU with a universal vCPU: x86, A1 and A2/A4 shape mappings differ. Record actual allocation, processor generation, SMT and quotas. [OCI shapes](https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm), [Kubernetes CPU units](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/).

Reports are deterministic JSON, Markdown, standalone HTML, metrics CSV and outcome CSV. All retain fixture provenance. Optional Parquet export requires `pyarrow`; absence raises an explicit error and does not claim a file was saved. The JSON/CSV evidence path runs on Python 3.12 standard library alone.

## Paired comparison gate

`bench compare --baseline <A1> <A2> ... --candidate <B1> <B2> ... --output <directory>` pairs directories by position. At least five independent unique pairs are required. It writes JSON and Markdown with `pass`, `failed`, `inconclusive`, or `invalid`, plus each metric's effect estimate and 95% interval. It reads the analyzer's actual `latency.correct_only.p95/p99` and `rates.deadline_goodput_per_s` fields.

The provisional margins are +10% p95, +15% p99, and -5% deadline-goodput. A latency gate passes only when the entire effect interval is at or below its margin, fails when the entire interval exceeds it, otherwise remains inconclusive. Goodput uses the opposite direction. This compares goodput at the same fixed offered rate, not sustainable-throughput capacity. A within-margin gate does not establish equality. Invalid evidence or incompatible comparisons block a verdict; fixtures and local functional trials remain diagnostic.

Preserve the following `config.comparison` metadata in each immutable run manifest:

```json
{
  "pair_id": "pair-01",
  "pair_order": "BA",
  "randomized_order": true,
  "dataset_sha256": "<64 hex characters>",
  "state_fingerprint": "<64 hex characters of normalized initial logical state>",
  "reset_evidence_file": "reset.json",
  "experimental_factors": ["artifact.image", "artifact.release", "artifact.source_commit"],
  "environment": {
    "cpu_arch": "amd64",
    "cpu_model": "<observed CPU model>",
    "topology": "single-vm",
    "database_product": "PostgreSQL",
    "database_version": "<actual version>",
    "durability": "<actual commit/HA policy>",
    "network_path": "<documented placement and network path>"
  }
}
```

The reset producer records `run_id`, `success: true`, `dataset_sha256`, and `state_fingerprint` in `reset.json` before the run; include that file in `checksums.json`. Verify the reset producer and its normalized state assertions; a JSON declaration alone cannot prove the database was restored. Both sides of each pair must match state, workload and design, and each lane must hold configuration constant across repetitions. Harness/workload source hashes must match. Declare differing resource/software fields as experimental factors instead of silently treating different hardware as equivalent. Endpoint URLs and credential-reference names are ignored as controls; resource allocations, CPU architecture, database/durability, build artifacts and telemetry settings are not. Missing design/reset/provenance evidence makes the comparison inconclusive.

`bench report <run-directory> --parquet` adds an actual Parquet outcome export when `pyarrow` is available; the archive retains its evidence label in Parquet schema metadata as well as in outcome rows. If the optional dependency is absent, JSON/Markdown/HTML/CSV remain written, the command exits with an explicit error, and no Parquet artifact is claimed.

## Executable collection and calibration

The runner now captures evidence automatically instead of requiring hand-authored success flags. Before the workload it runs an isolated loopback HTTP stub and k6 at twice the declared expected HTTP volume (default eight calls per workflow), retaining script, raw k6 output and calibration results. It then samples Linux `/proc/stat` and `/proc/meminfo` during the workload. Automatic generator validation requires a passing calibration, at least three host samples, less than 85% maximum observed host CPU, at least 10% available memory, acceptable sampling gaps, p99 arrival lag at most 100 ms, and calibrated headroom above the observed aggregate HTTP rate. These are explicit starting guardrails, not proof of universal capacity. The stub does not exercise Fluxnova or remote network capacity. Windows local functional runs do not acquire a live generator-headroom pass from unavailable `/proc` evidence.

`execution.k6_binary` selects either an installed binary or an executable container wrapper. Both calibration and workload subprocesses receive `BENCH_K6_IMAGE` from `artifact.k6_image`. The Linux Docker wrapper must preserve host networking for the calibration loopback listener, environment references, and absolute workspace/output paths. Pin the k6 image by digest and retain its value in the manifest.

Configure `evidence.telemetry_sources` to collect actual application, database and engine metrics. Each source has `name`, `role`, `kind` (`http`, `command`, or `docker_stats`), and `required` (default true). HTTP sources supply `url`, optional `authorization_env`, `format` (`prometheus` or `json`), and a nonempty `required_metrics` list of the real metric names exposed by that endpoint. Command sources supply a read-only `argv` plus format/required metric names. Docker sources use read-only `docker stats --no-stream` with an optional container-name list. Failed/missing required samples or excessive collection gaps prevent a completeness pass. Generator host metrics alone never establish application/database/engine telemetry completeness. Default required roles are `application`, `database`, and `engine`; actual deployment exporters and access must be configured before a live run can pass.

The built-in collector is also executable independently:

```text
python -m benchlib.evidence collect --config config.json --run-id RUN --output-dir EVIDENCE --duration-s 30
python -m benchlib.evidence calibrate --config config.json --run-id RUN --output-dir EVIDENCE
```

`execution.evidence_command` remains available for querying telemetry backends after a run. It receives `{run_id}` and `{run_dir}` substitutions and the `BENCH_RUN_ID`/`BENCH_RUN_DIR` environment variables. Query the actual recorded `load-window.json` and event timestamps; post-run health samples are not substitutes for measurements during the load. External generator/telemetry files must explicitly bind their validity to the same run ID and are copied into the sealed archive. Failure in reset, k6 launch, logging, collector command or normal finalization retains the event ledger, execution errors, summary and checksums for diagnosis.

The current k6 phase schedule starts measurement immediately after warmup arrivals stop. Warmup in-flight work can overlap measurement; this is disclosed in the summary rather than claimed to be an isolated phase. Total load allowance is warmup + measurement + one drain. Campaign budget coverage additionally includes bounded calibration, reset, collection and runner overhead. Scheduled events use `details.scheduled_elapsed_s`; the original emit time remains in details. k6 derives the schedule from started-iteration ordinal: after dropped iterations exact planned slot times are unknown, so the run is invalid for latency ranking while its drops and failure evidence remain visible.

Before a live run can pass, `evidence.golden_file` must point to an actual successful correctness report and `evidence.golden_sha256` must pin it. Its nonempty cases must all pass. The report's `scope.image`, `scope.database_product`, and `scope.database_version` must match the tested runtime digest and `comparison.environment` database identity. The archive retains this report and its digest. A local/H2 report cannot establish the live OCI/database gate. Missing or mismatched golden evidence leaves the live result inconclusive even when sampled requests succeeded.

For repeatability, `execution.reset_command` runs before every repeated baseline, including the first. The runner saves its exit status and log inside the run archive. The command must print a JSON object containing the exact `run_id`, `success: true`, measured `dataset_sha256`, `state_fingerprint`, and `method`; these become sealed `reset.json` evidence. A zero exit without a valid matching receipt does not start the workload. See the [controller reset broker](state-checkpoints.md). `comparison.pair_plan` may supply explicit per-run pair metadata; it never fabricates randomized execution order across separately deployed environments.
