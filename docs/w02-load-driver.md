# W02 load-driver qualification

The [W02 functional study](w02-independent-checks.md) now has a dedicated open-arrival [k6 driver](../load/workflow_checks.js) and an [independent evidence audit](../benchlib/w02_qualification.py). The local qualification is separate from a matched OCI performance campaign. It does not establish an OCI improvement, production capacity or a representative banking workload.

The optional [W02-M1 mixed-outcome qualification](w02-mixed-load.md) reuses this driver with a pinned sensitivity corpus. It reports settlements, expected refusals, safe stops and deadline misses separately. The approved-only defaults and deadline gate described below remain available.

## What the driver measures

Each scheduled arrival starts one process, registers its immutable original payment with the trusted backend, observes durable completion, and verifies both screening receipts and the exact synthetic effect. Registration uses a separate credential from the worker. The backend still controls the effect; engine variables alone cannot authorize it.

The two arms retain the same approved input trace, 400 ms KYC wait, 600 ms independent risk wait, two worker slots, 16 fixed generator VUs, 100 ms history polling and two-second outcome deadline. Each phase has distinct payment identities. Only the workflow graph differs. Workers remain running across warmup and the timed cohort.

The offered schedule is independent of completion time. Rates of 0.5 and 1 start/second use k6's integer arrival rate over a two-second time unit. The half-open cohort excludes the exact end-boundary slot. Dropped iterations remain in the offered denominator; after a drop the index-derived arrival times are approximate, so the run cannot qualify or support a latency ranking.

The scheduled lifecycle event records the declared arrival time; the started event records actual dispatch. Their difference exposes generator lag without moving the offered schedule. Both warmup and measurement have their own fixed VU pools, so finishing warmup work can overlap measurement.

Observed latency ends at the first durable history observation. Later variable, activity and backend checks have a separate verification duration. An unavailable verification remains unknown. A completed process with a wrong effect or receipt identity fails. Warmup uncertainty aborts the run; an unconfirmed start is never blindly resubmitted.

The Python audit reconstructs the declared original input independently, checks all lifecycle stages and their identities, recomputes arrival lag from timestamps, and rechecks history, screening and exact effects. A self-reported `correct` flag is insufficient. Final engine counts and backend row counts must also reconcile, including warmup work.

## Run the local qualification

The prepared checkout needs the retained, checksum-verified Fluxnova 3.0.1 distribution, Java 21 and k6 2.3.0 under `.tools/`. Those tools are ignored by Git and are not downloaded by this command.

```powershell
python scripts/qualify_w02_load.py --output dist/w02-load-qualification-NEW
```

The [runner](../scripts/qualify_w02_load.py) rejects an existing output directory. It first checks the k6 executable against its pinned release archive and the loaded Java libraries against the pinned Fluxnova archive. It then runs an isolated loopback calibration at 50 HTTP requests/second for ten seconds, followed by these predeclared local lanes:

| Order | Workflow | Offered rate | Warmup | Timed cohort | Final drain allowance |
| --- | --- | --- | --- | --- | --- |
| 1 | Sequential | 0.5/s | 20 s | 60 s | 15 s |
| 2 | Parallel | 0.5/s | 20 s | 60 s | 15 s |
| 3 | Parallel | 1/s | 20 s | 60 s | 15 s |
| 4 | Sequential | 1/s | 20 s | 60 s | 15 s |

Each lane owns a new local Fluxnova process, fresh H2 database and separate synthetic backend. The owned engine, support server and workers are stopped afterward, and the temporary admin password is redacted. The arrival observer is also bounded to 15 seconds per request. A failed lane stops the qualification, with all preceding and failed evidence retained. `--warmup` and `--duration` support bounded development checks with even lengths; changed settings belong to a new qualification directory.

Passing requires exact offered/started/correct counts in both phases, zero drops, zero leftover processes/tasks/incidents, exact backend counts, all measured outcomes within the deadline, and p99 scheduling lag at most 100 ms. A short backlog check requires no more than four observed outstanding measurement requests and no increase above one request in late-versus-early average outstanding work. The loopback calibration must also cover twice the observed aggregate HTTP rate. These are qualification guardrails, not a sustainable-capacity estimate.

The directory retains the frozen lane plan, source/workload snapshots, runtime hashes, raw k6 logs, lifecycle/oracle evidence, independent assessments, worker records, final database counts, cleanup receipts and a recursive checksum seal. Inspect `summary.json` and each lane's `assessment.json`; do not convert their local diagnostic latency distributions into conference performance claims.

## Regression and negative checks

The regular Python suite checks the audit. Set `BENCH_TEST_K6_BINARY` to run the actual k6 fixture tests as well:

```powershell
$env:BENCH_TEST_K6_BINARY = (Resolve-Path .tools/k6/k6-v2.3.0-windows-amd64/k6.exe).Path
python -m unittest discover -s tests
```

Those synthetic HTTP tests cover fractional arrival accounting, missing registration/verification, wrong effects, cross-process receipts, warmup abort, deliberate generator drops, and verification delay separated from observed completion. The independent audit also rejects modified effects despite a `correct` flag and derives scheduling lag rather than trusting the driver's lag field.

## Separate OCI qualification

Local Windows/H2 qualification leaves `cloud_generator_headroom_validated` and `cloud_telemetry_complete` false. Those local results remain unchanged. The separate OCI qualification (retained in the private research archive) now passes after inventory/cost checks, isolated deployment, PostgreSQL functional cases, Linux/Docker/SQL telemetry, verified state resets, and generator/load calibration on the actual setup. The negative corpus is separate from the timed approved cohort.

The accepted protocol froze five matched pairs, randomized arm order, a common rate of 1 start/second, 120-second warmup and 600-second measurement before the first pair. The [completed OCI comparison](../validation/oci-20261027-w02-comparison/README.md) now reports 32.1% lower observed p95 at the median paired change, with 6,000/6,000 measured payments correct and on time. Existing P01/P02/T03 evidence remains unchanged; W02 is a new comparison with its own workload and controls.

See [W02 study design](w02-independent-checks.md) and the [measurement contract](measurement-contract.md).

The prepared OCI runtime, isolation model and qualification-to-measurement gates are documented in the [W02 cloud protocol](w02-cloud-protocol.md). A prepared runtime is not evidence of a completed cloud comparison.
