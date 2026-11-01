# FNV-06: give every comparison the same starting conditions

The planned study asks how long it takes to finish 10, 100 or 1,000 intended workflows. Before comparing those populations, each run needs the same preparation and complete evidence.

The fixed qualification uses six cells: two populations (10 and 1,000) for each of messages, signals and batches. Every cell starts fresh, runs 100 warmup recipients with two waiting controls, then runs its test burst with two additional controls. Warmup and test event names differ. The auditor checks every expected history and variable, verifies the waiting controls, and compares warmup signatures within each pattern. Those signatures describe equivalent declared workflow state, not identical physical cache or JIT state.

The [local six-cell qualification](../validation/local-20261101-fnv06-repeat/README.md) passed. Reproduce it with verified Fluxnova, Java and k6 installations from the [driver guide](fnv06-load-driver.md):

```powershell
python scripts/run_fnv06_repeat_qualification.py --output dist/fnv06-repeat-NEW
```

Use fresh output paths. The command retains each cell, independently audits it before starting the next, and stops on failure. It starts six local engines in sequence and closes each one. It does not run the 45 performance cells or allocate cloud resources.

## Guarded PostgreSQL lifecycle

The cloud runner now accepts the same fixed qualification cells. An operator must supply a fresh signed OCI inventory, retain the preceding cell and its passing independent audit, and bind the evidence seal before requesting a reset. It rejects the reference database, changed ownership, active sessions, remaining work, foreign process history and stale guards. The role and schema names are fixed by the owned trial identity.

The reset runs in one transaction and checks catalogs outside the trial schema before committing. PostgreSQL documents that [DROP SCHEMA CASCADE can affect dependencies in other schemas](https://www.postgresql.org/docs/current/sql-dropschema.html); an outside-catalog change or an expired window causes rollback. Prior evidence stays sealed, and the next cell receives a fresh engine schema and role. The [cloud attempt](../validation/oci-20261101-fnv06-repeat/README.md) passed its first three cells and three resets, including a direct cross-schema rollback control. It stopped when the 1,000-recipient signal request timed out; final outcome evidence for that cell is incomplete, and both batch cells remain unattempted. The [six-cell outcome follow-up](../validation/oci-20261101-fnv06-outcomes/README.md) subsequently verified all six final outcome sets and five resets, with request failures retained separately. The earlier [one-time reset rehearsal](../validation/oci-20261101-fnv06-scale/README.md) remains separate evidence.

## Resource checks for short bursts

The [native Linux probe](../validation/native-20261101-fnv06-resources/README.md) passed both an idle control and deliberate throttling detection. The reader keeps raw cgroup-v2 and host counters. Its independent auditor requires samples on both sides of the work interval, checks stable process identity and limits, and examines ancestor throttling too. Sample gaps, clock disagreement and counter resets cannot be converted into a pass.

That probe qualifies the reader separately. The [resource and failed-reset trial](../validation/oci-20261101-fnv06-telemetry/README.md) now verifies actual message-workload resource observations and recovery after a failed request. The [45-cell population campaign](../validation/oci-20261101-fnv06-campaign/README.md) completed all 45 planned cells with verified final outcomes and resource evidence; 2 cells did not pass the predeclared generator headroom gate. Affected timing comparisons remain flagged rather than replaced. The results compare populations within each delivery pattern and preserve request failures, deadline uncertainty and batch bookkeeping separately. The frozen populations, client counts and separate pattern semantics are unchanged.

The opt-in [workload resource protocol](../workloads/fnv06/resource-observation.json) now adds collection hooks for the actual engine and job containers. The job contains both k6 and the Python observer, so its measurements describe their combined resource use. A host-side reader checks owned container identities, retains original cgroup counters every 100 ms and waits for samples after work finishes before the job exits. A separate read-only PostgreSQL connection records owned-role activity and trial-database counters every 500 ms. PostgreSQL statistics [can lag actual activity](https://www.postgresql.org/docs/current/monitoring-stats.html#MONITORING-STATS-VIEWS); database counters include observer overhead and do not measure database host CPU or memory headroom.

```powershell
python scripts/audit_fnv06_telemetry.py --source PRIVATE_CELL --output dist/resource-audit-NEW
```

This offline auditor binds sample clocks to the original release, request returns and completion polls. It rejects missing coverage, changed identities, changed limits and regressing counters. Engine constraints remain workload observations; generator constraints make the headroom check fail. A deliberate C01 timeout control also requires `--control PRIVATE_SEALED_CONTROL`, an explicit pre-run fault declaration and independently verified final outcomes. The [resource and failed-reset trial](../validation/oci-20261101-fnv06-telemetry/README.md) passed with both message cells fully reconciled, four failed requests preserved through reset and resource evidence verified across all four warmup/test windows. The generator headroom check passed in all four warmup/test windows. The earlier idle/throttle probe remains separate evidence; every performance cell will require its own resource audit.

## Deliberate timeout capture check

The [fixed fault protocol](../workloads/fnv06/timeout-capture.json) checks the evidence collector separately from performance. On a fresh owned trial database, it runs the normal 100-recipient message warmup, then briefly holds a SHARE lock on the trial event-subscription table during a ten-recipient message burst. The first four requests retain the unchanged 30-second timeout. The lock is released 36 seconds after scheduled dispatch, allowing final workflow state to be captured. This does not diagnose the earlier signal timeout or replace that failed attempt.

The separate auditor requires four native timeouts, six successful test requests, all 110 completed warmup/test workflows, four unchanged waiting controls, full REST inventories and reconciliation against nine SQL tables. It also verifies sealed lock samples, transaction release and the owned database identity. The ordinary qualification auditor must reject this failed job. A successful fault check is labeled `expected_timeout_evidence_passed`; it cannot authorize a schema reset or count as a passing performance run.

```powershell
python scripts/audit_fnv06_timeout.py --source PRIVATE_CELL --control PRIVATE_CONTROL --output dist/timeout-audit-NEW
```

The command audits saved evidence offline. It does not inject a fault, allocate resources or connect to a database. The [controlled cloud timeout check](../validation/oci-20261101-fnv06-timeout-capture/README.md) passed: four timed-out requests remained failed, while all 110 intended workflows and four waiting controls reconciled against REST and nine SQL tables. This qualifies the controlled request-failure capture path; other failure modes and general deadline accounting remain separate work.

## Separate request failures from final outcomes

The [outcome-accounting protocol](../workloads/fnv06/outcome-accounting.json) keeps three questions separate: did the request receive a successful response, did every intended workflow finish correctly, and how much correct work can be confirmed by the synthetic two-second threshold?

```powershell
python scripts/audit_fnv06_outcomes.py --source PRIVATE_CELL --output dist/outcome-audit-NEW
```

The auditor can verify complete final outcomes while preserving request timeouts, transport errors and HTTP failures. A verified result is labeled `outcomes_verified` or `outcomes_verified_with_request_failures`. A missing final snapshot, unfinished recipient, wrong variable, unexpected control transition or SQL disagreement produces `not_verified`; missing evidence never becomes zero failed work. Warmup requests must still succeed. A failed batch request without complete batch evidence cannot qualify through this path.

For the two-second threshold, the lower bound counts completions observed by a poll that finished by the deadline. The upper bound uses a poll that started at or after the deadline. Any difference stays uncertain. These are cohort-count bounds, not individual completion timestamps. Warmup and test results remain separate, and retrospective reports cannot authorize a reset.

A new cloud run may explicitly declare `outcome_accounting_v1` and bind this protocol before execution. Its reset guard then requires a complete independent outcome audit, nine-table SQL reconciliation, exact failure accounting, stopped containers and empty owned runtime state. A failed request remains failed when the next cell starts. Runtime failure markers are archived with the prior cell only after the guarded reset commits. Undeclared, incomplete or mismatched evidence is rejected. The [six-cell outcome follow-up](../validation/oci-20261101-fnv06-outcomes/README.md) verified all six final REST/SQL outcome sets and five resets. All requests succeeded in that six-cell follow-up. The later [resource and failed-reset trial](../validation/oci-20261101-fnv06-telemetry/README.md) verified reset after four deliberately timed-out requests, preserved those failures and collected audited resource observations during the following 1,000-recipient message cell. The original attempt remains partial and unchanged.
