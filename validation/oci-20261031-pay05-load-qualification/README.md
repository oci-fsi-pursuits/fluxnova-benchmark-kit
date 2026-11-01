# PAY-05 offered-load, reset and telemetry qualification

The sequential and parallel FX-quote workflows passed four short OCI qualification runs. All 264 workflows reached their expected outcome: 204 exact synthetic payment effects and 60 expected no-effect outcomes. The independent audit verified the arrival events against saved payment ledgers and separately queried SQL history.

This establishes that the harness can run the fixed cases under offered load and restore the complete state between runs. It is not a paired performance comparison or a maximum-capacity result.

| Model | Offered rate | Correct warmup | Correct measured | Measured within 2 s | Reset and telemetry |
| --- | ---: | ---: | ---: | ---: | --- |
| Sequential | 0.5/s | 22/22 | 44/44 | 42/44 | Passed |
| Parallel | 0.5/s | 22/22 | 44/44 | 44/44 | Passed |
| Parallel | 1/s | 22/22 | 44/44 | 44/44 | Passed |
| Sequential | 1/s | 22/22 | 44/44 | 42/44 | Passed |

Every run used the same 22 revision-2 cases, three worker slots, 100 ms completion-observation interval and complete PostgreSQL-schema plus SQLite-ledger checkpoint. There were 22 warmup arrivals and 44 measured arrivals per run, with no dropped arrivals. The generator calibration, controller headroom, container/SQL telemetry and all four reset checks passed.

**Seven deadline misses remain in the evidence:** four measured arrivals and three warmup arrivals, all from the deliberately long `lowest_total_cost` concurrency probe. That case uses 1000 ms per quote in both models. All 264 outcomes were correct; 257 were observed within two seconds including warmup. These short qualification runs do not establish a speedup.

The topology was Fluxnova 3.0.1, k6, the synthetic quote service and the three-slot worker on the existing controller, with a separate fresh single-node OCI PostgreSQL 17.6 database (2 OCPUs, 32 GB). This differs from the four-host P01 reference. TLS endpoint/certificate verification and five isolation probes passed. Host headroom is not a managed-database CPU or end-to-end tracing result.

## Evidence and cleanup

The independent audit checked 289 sealed files. The temporary database, containers, network and Bastion session were removed. All 48 non-metering reference tables, schema/access metadata, original services and verifier were preserved. The metering table's existing rows were preserved, with independently verified periodic appends. The original reference remains allocated and billable.

The source revision passed 458 local tests without skips, real k6 checks, Linux/Python 3.12 CI, Windows/Python 3.13 CI and secret scans. Read the [aggregate evidence](summary.json), [workflow guide](../../docs/pay05-fx-quotes.md), [runner](../../scripts/run_pay05_load_cloud.py) and [independent auditor](../../scripts/audit_pay05_load.py). Raw stores and environment metadata remain private.

Next, freeze and qualify a declared quote-delay/expiry performance population and repeat protocol before the paired timing study. Live models, real FX providers, payment networks, signed-intent integration and complete cost attribution remain outside this evidence.
