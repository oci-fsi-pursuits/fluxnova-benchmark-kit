# Benchmark roadmap

Working plan updated 10 November 2026. The goal is reproducible evidence about **time and cost to a correct, authorized outcome**. A completed functional gate is not a performance result. Owners are currently the repository contributors; external collaborators below are proposed, not committed.

## Daily pickup

1. Read the latest entry below and choose the first unfinished gate.
2. Inspect the working tree and preserve unrelated changes. Use fresh output directories; retain failed attempts.
3. Declare inputs, expected outcomes, controls, clock, resource limits and acceptance before executing.
4. Run the smallest useful experiment, independently audit it, and record results and limitations.
5. Update this file with evidence, blockers and the exact next action. Publish only sanitized summaries; raw credentials, logs and infrastructure state stay local.

The daily reminder is a pickup prompt, not an instruction to allocate infrastructure or run paid model evaluations automatically.

## Current baseline

- W01, W02, W02 mixed, W03 and PAY-05 comparisons are complete in their declared synthetic scopes. Preserve their failures and regressions; do not rerun to seek a preferred result.
- FNV-06: the 45-cell event/batch campaign completed, with two generator-headroom flags retained.
- FNV-07: local retry/effect qualification, one/two-engine PostgreSQL functional checks and local fixed-arrival timing qualification passed previously. The 1/2/3/6-engine measured campaign has not started.
- Capacity, complete attributed cost per outcome and measured live-agent benefit remain open.

See [completed patterns](docs/workload-completion.md), [FNV-07](docs/fnv07-cluster-jobs.md) and [measurement contract](docs/measurement-contract.md).

## Ordered milestones

| ID / status | Work and acceptance | Next action / dependency |
| --- | --- | --- |
| R0 — passed locally | Refresh local FNV-07 retry/effect and timing qualification. Reconcile original inputs, attempts, exact receipts, job history and all scheduled arrivals; retain failures and verify owned cleanup. | Explicit connection-close restored local timing qualification on 12 November. Both default-policy timing and six-case retry/effect audits passed. All prior failures remain retained; see the evidence below. |
| R1 — next | Qualify the multi-engine PostgreSQL deployment, reset/cleanup and telemetry. Capture generator, observer, receipt-service, engine and DB resources; show every declared executor actually performs work. | Declare host placement, CPU/memory allocation, versions, ingress, DB configuration and resource validity thresholds. Provisioning and budget must be explicitly scoped. |
| R2 — planned | Separate offered-load pilot, followed by frozen 1/2/3/6-replica × five-repeat protocol. Keep per-engine resources fixed; disclose increasing total resources. Preserve offered arrivals, drops, retries and unresolved outcomes. | Use R1 evidence to select offered load, warmup, duration and headroom thresholds before measured runs. Do not choose them after seeing the comparison. |
| R3 — planned | Execute and audit the frozen FNV-07 campaign. Publish paired outcome/latency/resource evidence and cost accounting. Report invalid cells and uncertainty; fixed load does not establish capacity or linear scaling. | R1/R2 must pass. Do not infer multi-host scale-out from co-located functional results. |
| R4 — planned | Versioned agent-ready pattern catalogue: BPMN, typed contracts, policy invariants, negative/retry/duplicate cases, plugin versions, independent oracle and evidence links. | Start with one existing payment/dispute pattern; discuss conformance needs with Fluxnova/ElevenAI contributors. |
| R5 — planned | Bounded improvement-loop comparison: one-shot, independent retries at matched budget, and feedback-guided revision. Freeze model/tools, evaluator, tasks and promotion rules. Retain held-out regressions, review requirements, tokens, cost and failures. | Implement a synthetic task and independent policy/effect oracle. Keep candidate edits away from evaluator and policy. Model-weight training is outside this milestone. |
| R6 — planned | Optional Aeron adapter behind a transport-neutral worker contract. Compare baseline and reliable IPC, then UDP/Archive as separate factors; verify acknowledgment, durable recording, replay, backpressure, late results and exact effects. | Qualify the command/result contract first. No connector or speedup is established. Aeron Cluster, if needed, is a separate deterministic-service study. |
| R7 — planned | Domain and governance blueprints: CDM lifecycle workload, Morphir policy oracle, CALM/control-to-runtime evidence. | Choose one synthetic domain case and obtain domain review. Proposed partners: TradeHeader, FINOS governance contributors and Morphir community. |
| R8 — planned | Portable provisioning and broader capacity/cost work. Optional ORB/HTC/OpenGRIS adapters must preserve workload semantics and verify owned cleanup. | Stabilize workload/environment contracts first; record capacity delivered, allocated cost and failures. No turnkey cross-cloud parity claim. |

R4/R5 design can progress while infrastructure-dependent gates wait. Measure Aeron and improvement loops separately before combining them.

## Collaboration backlog

- Fluxnova maintainers, Summit58/iTmethods: common performance/migration protocol and reproducible workloads.
- ElevenAI and agent-evaluation contributors: tool contracts, bounded reflection and independent quality/effect scorecards.
- Symphony: generated BPMN draft/publication boundaries and synthetic dispatch checks.
- TradeHeader: one CDM translation/qualification/lifecycle blueprint.
- Aeron maintainers or Adaptive: review proposed transport/Archive semantics. No contact has been made.

Contact through public project channels only when outreach is explicitly authorized. Session-derived opportunities are hypotheses, not agreed partnerships.

## Progress log

### 12 November 2026 — standalone reproduction and local qualification restored

- Built a bounded shared-Java-client/Python-server reproducer with no Fluxnova engine or load scheduler. Two pairs alternated legacy-header and explicit-close arms at concurrency two, 40 calls per arm. The legacy arms each completed 22/40 calls; all 36 failed calls lacked a corresponding server request. Both explicit-close arms completed 40/40 with matching client/server IDs and response counts. Independent saved-evidence reconciliation verified every arm, including failures.
- This isolates a connection-lifecycle interoperability problem in the tested Java 21/Python 3.14 setup. It does not prove a particular JVM defect. The receipt fixture now explicitly declares `Connection: close` by default, matching its existing one-request-per-connection behavior. A legacy diagnostic option preserves reproduction of the old behavior; no client mutation retry was added.
- A fresh traced workflow attempt (`004`) still failed: 18/20 original arrivals completed, with zero delegate failures; the other two arrivals were missed. It remains rejected and retained.
- A separate explicit-close attempt without synchronous tracing (`005`) passed the unchanged timing auditor. A subsequent fresh run using the new default (`007`) also passed: 20 workflows, 60 delegate/backend attempts, 60 effects, 120 job records, zero retries or missing arrivals. The default-policy six-case functional run (`006`) passed independent audit: 19 attempts, eight intended failures, 12 effects, three completed cases, two expected blocked cases and one waiting control.
- Deadline results must stay separate from driver validity: `005` was 20/20 timely under the original two-second deadline; `007` was **0/20 timely**. All 20 misses remain visible. This variation blocks any stable latency claim and reinforces the need for controlled resource/scheduling investigation in R1.
- Added a standalone evidence auditor and tests that reject changed/resealed summaries, missing client calls and duplicated IDs. All owned services stopped, raw evidence remains local, and no cloud resources or paid models were used.
- [Sanitized qualification evidence](validation/local-20261112-fnv07-connection-close/README.md) records hashes and scope. These are functional/driver qualifications, not capacity or latency comparisons.
- **Next gate is R1:** declare and qualify PostgreSQL host placement, resource telemetry, executor participation, reset and cleanup. Preserve the existing measurement clock and fixed-arrival denominator; select offered load only in a separate pilot before freezing the replica campaign.

### 12 November 2026 — transport diagnostic tooling

- Added opt-in receipt-server metadata tracing, sealed alongside the run; no headers, bodies or exception messages are recorded. The diagnostic report verifies checksums, separates delegate attempts from durable backend attempts/effects, and reruns the unchanged qualification rules. Added read-only immutable opening of the sealed SQLite backup in the timing auditor, consistent with the functional auditor.
- The historical 10 November evidence still fails its original no-retry gate. It is unchanged and has no retrospective trace.
- Retained three new diagnostic attempts: baseline inside sandbox (77 delegate attempts, 26 failures, 17 registered cases, 51 effects); baseline outside sandbox (78 attempts, 24 failures, 19 registered cases, 54 effects); explicit connection-close arm (39 attempts, zero delegate failures, 16 registered cases, 39 effects). **All three failed independent timing qualification.** Registered cases are not completed workflows, and fewer delegate errors do not establish an improvement when arrivals remain missing or unverified.
- The outside-sandbox trace captured a `ConnectionAbortedError`; this shows the issue is not confined to sandbox execution. It does not establish the original EOF's root cause. Adding an explicit close header is an experimental option, not a qualified fix. Tracing adds synchronous I/O overhead, so these are diagnostic results rather than performance comparisons.
- Every attempt retained its source/input seal and stopped its owned engine and receipt service. No cloud resources or paid models were used. Four new diagnostic tests, 41 existing FNV-07 tests, eight release tests and catalogue checks passed.
- **Next gate:** build a small Java/Python HTTP reproducer independent of engine startup and offered-load scheduling. Compare default and explicit-close behavior under fixed concurrency, retain connection/request/error counts, then use the result to design the next timing diagnostic. Do not proceed to the replica campaign yet.

### 10 November 2026 — restart

- Confirmed the next unfinished research question is FNV-07 engine-replica timing, with deployment/resource/pilot prerequisites still open.
- Started R0 in a fresh local build directory. Initial Java compilation encountered a dependency-file access denial; the failed build and compiler log are retained privately. A fresh retry uses the verified Java 21 compiler.
- The fresh delegate build succeeded. Independent retry/effect audit passed: six cases, 19 delegate attempts, eight retained failures, 12 distinct external effects, three completed workflows, two expected blocked workflows and one unchanged control. Engine and receipt service stopped; no cloud resource was accessed.
- The timing collection completed but **failed independent qualification** with `unexpected_attempt_or_job_outcomes`: 20 completed workflows and 60 distinct effects, but 61 delegate attempts, one `java.io.IOException` and 121 job-log records rather than the expected 120. The original Java error was an HTTP response-header EOF. The backend recorded 60 execution attempts, so the failed delegate call has no matching backend execution entry; the exact transport cause remains unproven.
- This failure is retained. Do not classify the run as passed because final outcomes completed; do not weaken the no-retry gate. Any diagnostic retry is a new attempt and does not replace this record. Both owned services stopped cleanly.
- Offline validation passed 41 FNV-07 tests, eight public-release tests and the catalogue check (17 studies, nine illustrated patterns). These checks do not override the failed live timing qualification.
- Created this ordered roadmap and a daily pickup reminder. No cloud resources or paid model calls were started.
- Next: inspect the Java HTTP client / local receipt-service connection lifecycle, capture request/connection failures without credentials, and design a transport diagnostic before rerunning timing qualification. Then define R1’s deployment/resource qualification. No PostgreSQL, cloud or multi-replica performance work started today.

## Result and promotion rules

Use the [benchmark protocol](docs/benchmark-protocol.md) and [release policy](docs/public-release.md). Keep timing qualification, functional checks, measured campaigns and proposed designs distinct. Publish only reviewed synthetic inputs and sanitized evidence. A candidate cannot approve itself by changing the oracle, dropping failures or weakening required reviews.
