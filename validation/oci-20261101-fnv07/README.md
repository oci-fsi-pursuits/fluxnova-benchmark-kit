# FNV-07: one and two engines sharing PostgreSQL

Both functional checks passed the same [six-case retry/effect corpus](../../docs/fnv07-cluster-jobs.md). The two-engine check proved that both engines executed jobs from the same PostgreSQL database. It did not measure a speedup.

| Observation | One engine | Two engines |
| --- | --- | --- |
| Original workflow instances | 6 | 6 |
| Completed / deliberately blocked / waiting | 3 / 2 / 1 | 3 / 2 / 1 |
| Delegate attempts, including failures | 19 | 19 |
| Deliberately unsuccessful attempts retained | 8 | 8 |
| Distinct backend effects | 12 | 12 |
| Attempts attributed to each engine | 19 | 11 and 8 |
| Job creation / success / failure records | 13 / 11 / 8 | 13 / 11 / 8 |
| Final SQL tables independently reconciled | 8 | 8 |

All expected states remained correct. In particular, the case that committed an external effect but exhausted its retries remained a **blocked workflow**. Cached backend replies were distinguished from new effects. The fixture enforces one effect per registered workflow step; these checks do not prove a general exactly-once guarantee.

## What was held constant

Both checks used the verified Fluxnova 3.0.1 artifacts, Java 21.0.12.1, the same compiled delegate, PostgreSQL 17.11, full history, explicit executor settings and the same original corpus. Each engine had a 1-vCPU limit, 2 GiB memory limit, 1 GiB maximum Java heap, two executor threads and a ten-connection JDBC pool. Distinct executor lock owners and distinct container/log identities were verified.

All components ran on one existing 4-vCPU, 16-GiB OCI controller. PostgreSQL used a fresh private container for each attempt, with verified TLS connections. This was not OCI Database with PostgreSQL, a multi-host topology or a measured capacity comparison. Generator, database and receipt-backend headroom still need a separate qualification under load.

## Independent checks and retained failures

The auditor reconciled original inputs and requests, complete REST inventories, each engine's delegate logs, the original SQLite receipt ledger and a repeatable-read, read-only PostgreSQL snapshot after the engines stopped. It rejected altered counters, misclassified job outcomes, a wrongly linked incident and a missing job record. The harness passed **615 local tests with no skips**, including real k6 and 31 FNV-07 tests.

An earlier single-engine attempt stopped before model deployment because the operator passed the staged model path instead of its sealed evidence path. Its failed state and cleanup are retained separately; it contributes no successful cases. The first full regression attempt also exposed a Windows HTTP rejection issue, fixed by consuming the bounded request body before rejecting credentials.

Two SQL audit mappings were corrected against the pinned engine artifacts: historic job activities use `ACT_ID_`, and integer variables store matching numeric and textual representations. The original workload, expected results and collected evidence were unchanged. The corrected auditor passed both native runs and added negative checks.

## Closeout and next step

All test containers and their private networks were removed. The owned Bastion session was deleted. The reference services stayed running, all 48 non-metering tables remained unchanged, and existing metering rows were preserved with 24 normal rows appended. No new VM, managed database or model calls were allocated. The original reference remains allocated and billable.

The public kit includes the corpus, local runner, Java delegate, receipt service and SQL auditors. OCI staging remains a retained private operator prototype pending standardization; this report is not a claim that a generic cloud cluster launcher is ready.

Next: qualify resource capture and placement, run a separate offered-load pilot, then freeze and execute the proposed 1/2/3/6-replica performance matrix. No measured cells from that matrix have started. Node-loss recovery and managed-database behavior remain separate studies.

[Machine-readable summary](summary.json). Raw logs, local paths, generated identities, connection details and credentials are excluded from the release inventory.
