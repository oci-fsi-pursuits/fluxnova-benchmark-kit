# W02-M2: mixed-outcome comparison on OCI

The question is whether parallel independent checks increase **correct settlements observed within two seconds per measurement second** when approvals, refusals, unknown checks and one-time faults share the same worker pool. Expected refusals and safe stops are separate outcomes; neither contributes to settlement goodput.

This protocol follows the completed [local mixed-driver qualification](w02-mixed-load.md). It does not alter the earlier approved-only OCI study or turn its 32.1% p95 reduction into a claim about mixed traffic. The [protocol/reporting module](../benchlib/w02_mixed_cloud.py) fixes the controls before execution.

## Population and controls

Use the same seeded 50-case corpus as W02-S1: ten payment-policy/fault cases crossed with five fixed KYC/risk delay profiles. Each complete cycle contains 20 expected settlements, 20 refusals and 10 safe unknown-check stops. Equal coverage is synthetic; no customer-frequency or live-agent claim is made. Inputs repeat by arrival ordinal with unique phase/payment identities. Both arms use the same schedule and original-input oracle.

Retain the [W02 cloud isolation](w02-cloud-protocol.md#isolation-and-starting-state): an owned one-node 2-OCPU/32-GB PostgreSQL system, fresh role/schema, verified checkpoint resets, and existing-controller containers. The engine has two logical CPUs, 4 GiB memory, 2 GiB heap and a ten-connection pool. Both arms have two workers, 100 ms completion polling, separate fixed 16-VU warmup/measurement pools, full history and identical SQL/Docker/Linux telemetry. An internal network and fixed-destination TLS gateway restrict access to the owned database. This topology is separate from P01's four-host reference.

The prepared private stage must explicitly select `study_id=w02_m2`. The resolved corpus is hashed, included in the source manifest, compared with the frozen source protocols and mounted read-only into k6. Unknown study IDs fail. The original approved-only mode remains available.

## Qualification

Before measurement, verify the twenty original functional cases, exact effects, repeated calls, premature-settlement denials and cross-process replay refusals. Calibrate the real Linux generator at 80 loopback HTTP requests/s for fifteen seconds. Then run sequential 0.5/s, parallel 0.5/s, parallel 1/s and sequential 1/s, each after a verified reset.

Each qualification run has **100 seconds of warmup, 100 seconds of measurement and 30 seconds of drain**. At either rate, both phases cover whole 50-case cycles. Across all four runs, the design offers 300 measured and 300 warmup arrivals.

Require exact offered/started/verified counts, zero drops or unsafe/unresolved outcomes, p99 dispatch lag at most 100 ms, bounded outstanding work, empty final queues, original-store reconciliation, successful resets and complete host/container/SQL telemetry. The existing headroom guards require Linux host CPU p95 below 80%, available memory at least 20%, and telemetry gaps no longer than fifteen seconds. They do not measure managed-database CPU or maximum sustainable capacity.

**Deadline misses are reportable in qualification and measurement.** The slowest sequential dependency profile already totals two seconds before workflow overhead. The highest common rate passing the validity guards is selected; a slower deadline result is never hidden by selecting a different rate for one arm. Freeze the selected measurement plan and its hash after qualification and before the first pair.

## Matched measurements

Run five pairs, using a run-ID-derived order with three sequential-first and two parallel-first pairs. Every arm uses **100 seconds of warmup, 600 seconds of measurement and a 30-second drain** at the same qualified rate. Every phase covers complete corpus cycles. Only the BPMN check ordering changes.

- Primary: parallel minus sequential change in timely correct settlements per second, calculated for each pair; report the median and range of the five changes.
- Secondary: on-time fraction and observed p95/p99 separately for settlements, refusals and safe stops; include every category's offered, verified, late, dropped and unresolved counts. Report category regressions even if settlement goodput improves.
- Retain dependency-profile results, original inputs, process history, receipts, exact effects, reset records and resource telemetry. Later verification determines whether a completion observation is correct; verification time is separate from the observed completion boundary.

Stop on evidence, safety/correctness, reset or generator-validity failure, retaining all failed and partial arms. Do not stop early because an improvement appears large. An incomplete or invalid series produces no aggregate paired claim. Five pairs provide a bounded descriptive comparison; their range is not a confidence interval, and median run percentiles are not pooled request percentiles.

## Independent audit and operations

Download the sealed private phase export, excluding generated credentials, then run:

```powershell
python scripts/audit_w02_mixed_cloud.py --phase qualification `
  --evidence dist/M2/qualification-snapshot --output dist/M2/qualification-audit

python scripts/audit_w02_mixed_cloud.py --phase campaign `
  --evidence dist/M2/campaign-snapshot --output dist/M2/campaign-analysis
```

Use new output directories. The auditor verifies the exported file inventory and source hashes, reconstructs events from raw k6 logs, recomputes outcome/telemetry/reset assessments and compares each valid arm with its original SQLite store, including injected-fault records. It verifies the resolved corpus and frozen plan before producing outcome-specific paired results. Raw PostgreSQL dumps remain retained with their hashes.

The private operator record binds the new target ID, ownership tags and source snapshot. A bounded supervisor renews access and authentication, watches status and retires only the owned temporary database/runtime. It does not retry failed workloads. Original reference data and services require a final preservation check.

The 28 October readiness refresh (retained in the private research archive) reconciles $81.86 in partial receipt-matched posted charges. A revised $210.01 planning envelope stays within the authorized $300 ceiling through 28 October 12:00 UTC, including recent/unposted resource coverage, the new one-node six-hour trial allowance and a $100 uncertainty allowance. This is a planning estimate, not a complete bill, provider-enforced cutoff or proven maximum charge. Earlier cumulative reservations remain historical; they are not added again as actual spending. The original reference remains allocated and billable.

Research and OCI implementation: Nicholas Cusato. Fluxnova software: FINOS contributors.

Execution update, 28 October: the [first qualification](../validation/oci-20261028-w02-mixed-qualification/README.md) passed, but a workstation status disconnect interrupted the handoff before any paired arm started. The isolated target was deleted and original preservation passed. Its full lifetime is retained in the updated $210.30 planning estimate alongside a fresh trial allowance. The next attempt uses bounded read-only observation recovery; workload, corpus, deadline policy and measurement design are unchanged. The [browser report builder](browser-automation.md) re-audits sealed evidence and withholds aggregates from qualification or incomplete campaigns.
