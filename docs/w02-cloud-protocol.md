# W02: sequential and parallel checks on OCI

The question is whether independent KYC and risk checks can overlap while preserving the same authorized payment outcome. This is a synthetic workflow comparison, with deterministic 400 ms KYC and 600 ms risk dependencies. It is not a live-model or production payments benchmark. Faster outcomes are a hypothesis; all failed and slower arms remain evidence.

**27 October outcome:** the frozen design below completed all ten arms. The [independent comparison report](../validation/oci-20261027-w02-comparison/README.md) records 32.1% lower median paired observed p95 and 6,000/6,000 correct on-time measured payments. The temporary database and owned runtime were removed. This dated result note does not change the frozen design.

## Isolation and starting state

The prepared [controller runtime](../scripts/run_w02_cloud.py) uses a fresh, tagged single-node OCI PostgreSQL system with the reference allocation of 2 OCPUs and 32 GB memory. It creates a distinct database role and schema. Existing application services and the original database are not reconfigured.

The engine, generator, backend and two-slot worker share the existing controller. Engine limits are two logical CPUs, 4 GiB memory, 2 GiB maximum heap and a ten-connection pool. The backend and worker use the same pinned services image with the frozen W02 source mounted read-only. Both arms use Fluxnova 3.0.1, full history and the same SQL/Docker/Linux instrumentation. Java-agent instrumentation is disabled in this study. This topology differs from the four-host P01 baseline; do not compare their absolute latencies as a controlled experiment.

An internal Docker network blocks direct access to the original services, original database, cloud metadata and Internet. A fixed-destination gateway forwards only to the new PostgreSQL endpoint, preserving end-to-end TLS with hostname and certificate verification. The generator runs on the controller host network and can reach the test services. Trusted registration and worker execution use separate random credentials, kept in the private stage.

The database admin owns the test schema and grants the runtime role only schema usage and object creation. Resets preserve those schema controls while restoring runtime-owned objects in one transaction. This avoids assigning another role's schema ownership, which requires the ability to assume that role. See [PostgreSQL role privileges](https://www.postgresql.org/docs/17/role-attributes.html).

After deploying both BPMNs, the harness stops its writers and captures a PostgreSQL dump plus a SQLite backup. Before each arm it preserves the preceding state, restores both stores, and verifies their logical fingerprints before restarting writers. A reset failure leaves the writers stopped. Each measured arm also retains its raw logs, full outcome/receipt evidence, telemetry, database dump and SQLite snapshot.

## Qualification before measurement

Run the 20-case functional corpus, including rejected payments, lost responses, concurrent completion, repeated operations and cross-process replay. These negative cases are separate from the timed approved-payment population.

Calibrate the actual Linux generator at 80 loopback HTTP requests/second for 15 seconds. This validates HTTP-generation capacity, not the remote database or network. The following cloud lanes then exercise the real engine/backend path, each after a verified reset: sequential 0.5/s, parallel 0.5/s, parallel 1/s, sequential 1/s. Each has 30 seconds of warmup and 120 seconds of measurement.

Qualification requires exact counts and effects, zero generator drops, p99 arrival lag at most 100 ms, bounded observed backlog, all timed outcomes within two seconds, and successful reset/telemetry checks. Linux host CPU p95 must be below 80%, available memory at least 20%, and telemetry gaps no more than 15 seconds. Docker health/resource samples and SQL engine/database counters must cover the execution. This does not collect managed-database host CPU or full distributed traces.

Select the highest of 0.5/s and 1/s at which both arms pass. No latency advantage is required. An invalid run stops the qualification; performance deadline misses can disqualify a rate without making a slower result disappear.

## Frozen matched comparison

After qualification, write and hash `measurement-plan.json` before the first measurement. The [protocol module](../benchlib/w02_cloud.py) predefines:

- Five pairs, each containing sequential and parallel workflows. A run-ID-derived random order assigns three sequential-first and two parallel-first pairs.
- The qualified common rate, 120-second warmup, 600-second measurement and 30-second drain per arm.
- Two worker slots, 16 fixed k6 VUs, 100 ms completion polling and a two-second outcome deadline.
- The same approved payment trace and original-input outcome checks. Only BPMN check ordering changes.
- Primary outcome: paired change in observed completion p95. Secondary outcomes: p99, correct outcomes within the deadline per second, unknown/error/drop counts, and overlap of dependency receipts.

Observed completion includes polling delay; verification after the completion observation has a separate duration. Later receipt/effect checks determine whether the observed outcome counts as correct. A self-reported success flag is insufficient.

Stop on invalid evidence, safety/correctness failures, reset failures or generator validity failures. Retain partial and failed pairs. A deadline miss alone in a frozen measurement is an outcome to report, not a reason to discard the arm. Do not stop because an improvement appears large. Five pairs are a bounded first comparison, not a broad capacity or banking-representativeness claim.

## Recheck and report the results

Download the private controller stage into an ignored local directory, retaining its relative paths, source manifest, raw run events, telemetry and reset records. Keep identity and credential files private. The [summary command](../scripts/summarize_w02_cloud.py) rechecks source hashes, the frozen plan, common controls and every completed arm's raw evidence before producing an aggregate report without cloud resource IDs:

```powershell
python scripts/summarize_w02_cloud.py --evidence dist/W02/controller-stage --output dist/W02/reaudited-results
```

Use a fresh output directory. An incomplete or invalid series produces no aggregate improvement claim. A complete report gives each paired p95 change, the median and range of those five changes, median run p95/p99, and deadline goodput. It separates warmup from the timed denominator and reports regressions. The pair range is not a confidence interval, and a median of run percentiles is not a pooled request percentile.

## Operations and cost

The private launch record must retain the exact new resource ID, ownership tags, immutable source/image identities, original-service snapshot and a cleanup deadline. The independent supervisor refreshes access, observes status and deletes only that owned test database. It does not retry failed workloads. The controller also has a bounded service runtime. A cleanup failure requires explicit follow-up; a local sleep inhibitor is not a cloud cleanup guarantee.

The 27 October planning record reserves USD 298.89 under the previously authorized USD 300 ceiling through 28 October 00:00 UTC. Its temporary-database allowance covers up to two nodes for ten hours; this study uses one node and a shorter lifetime. This is a conservative planning reservation, not measured spending or a provider-enforced shutdown. See the readiness record (retained in the private research archive).

Provisioning remains an operator workflow using the existing OCI infrastructure, pinned Vault credential references and retained private identity records. The runtime requires a prepared controller stage; it is not an unrestricted provisioning command. The portable BPMNs, backend, worker, k6 driver and accounting audit are public. Local reproduction is documented in [the load-driver guide](w02-load-driver.md).
