# Payment checks across dependency conditions

The W02-S1 local sensitivity qualification is complete: **100/100 executions reached their prespecified outcomes** across 50 scenarios and both workflow graphs. This broadens functional coverage beyond W02's fixed 400/600 ms performance cohort. It does not establish a new latency improvement, deadline-success rate, capacity limit or representative customer mix.

The [frozen protocol](../workloads/w02/sensitivity-protocol.json) crosses every existing W02 approval, rejection, unknown-result and retry case with five synthetic dependency profiles. A seeded order and balanced sequential-first/parallel-first assignments are resolved before the engine starts. Equal scenario coverage is a test design, not a claim about payment frequencies.

| Profile | KYC / risk wait | Executions | Expected settled / rejected / blocked | Overlapping final check receipts in the parallel arm |
| --- | --- | ---: | --- | ---: |
| Fast | 50 / 70 ms | 20 | 8 / 8 / 4 | 1 of 10 |
| Balanced | 400 / 400 ms | 20 | 8 / 8 / 4 | 9 of 10 |
| Risk dominates | 100 / 900 ms | 20 | 8 / 8 / 4 | 4 of 10 |
| KYC dominates | 900 / 100 ms | 20 | 8 / 8 / 4 | 9 of 10 |
| Both slow | 900 / 1100 ms | 20 | 8 / 8 / 4 | 10 of 10 |

The original SQLite store and archived engine records independently confirm **40 exact payment effects**, **300 receipts**, **600 repeated calls preserving receipts/effects**, **100 premature-settlement denials** and **two blocked cross-process replays**. Twenty injected one-time failures/lost responses were exercised. Rejected and blocked cases produced no effects. A blocked unknown-check case is a safe functional outcome here, not a completed payment.

The auditor verifies all 224 archived file hashes, the resolved case grid and order, original inputs, durable process outcomes and the SQLite records. Negative audit controls reject a changed sealed file, a missing case despite a resealed passing summary, and an altered SQLite payment amount despite a resealed database hash. The owned engine stopped with empty process, external-task and incident counts. No OCI resources or live models were accessed. See the [sanitized result and audit](../validation/local-20261027-w02-sensitivity/README.md).

## What the overlap observation means

A parallel BPMN graph permits concurrency; it does not guarantee that the final receipts overlap for every short or retried operation. Receipt intervals exclude earlier failed attempts. These local observations motivate a controlled sensitivity measurement; they do not explain a proven root cause or quantify a performance gain. Only four declared long, fault-free parallel probes require positive receipt overlap, and all four passed.

This runner uses an owned Fluxnova 3.0.1 instance, Java 21 and embedded H2 on Windows, with two worker slots and 100 ms completion polling. Workers start for each functional case. There is no steady-state offered-load phase, warmup cohort or two-second service-level test. Keep these results separate from the [five-pair OCI W02 latency study](../validation/oci-20261027-w02-comparison/README.md).

## Reproduce and inspect

With the [prepared local runtime](w02-independent-checks.md#reproduce-the-functional-qualification), from the repository root:

```powershell
python scripts/run_w02_functional.py `
  --sensitivity-protocol workloads/w02/sensitivity-protocol.json `
  --output dist/w02-sensitivity-NEW --port 28184

python scripts/audit_w02_functional.py `
  --source dist/w02-sensitivity-NEW --output dist/w02-sensitivity-audit-NEW
```

Use new output names and an unused loopback port. The default W02 ten-case qualification remains available without `--sensitivity-protocol`. Retain failed attempts and original archives.

Open the new run's `report.html` to compare the two graphs, filter the result table and inspect each saved record. On the prepared workstation, repeat browser checks with Python 3.13 and the existing Playwright dependencies:

```powershell
& "$env:USERPROFILE\oci\Scripts\python.exe" scripts/rehearse_w02_functional.py `
  --source dist/w02-sensitivity-NEW --output dist/w02-sensitivity-browser-NEW `
  --playwright-path dist/browser-review-tools --headed
```

All 100 displayed rows, arm filters, actual JSON-link navigation and keyboard operation passed browser verification for the retained run. Desktop and laptop screenshots passed visual review. The page displays saved evidence and executes no new payments.

## Next measurement gate

Before calling a cohort representative, record the intended payment task, real authority/review policy, sourced outcome proportions, joint dependency-delay observations, retry/unknown rates, arrival bursts and business deadlines. Until those are available, label each assumed distribution as a synthetic sensitivity profile.

The subsequent [W02-M1 local mixed-load qualification](w02-mixed-load.md) now passes with 300 measured outcomes, separating settlements, expected refusals and safe stops; all 58 deadline misses remain visible. The next OCI study still needs the same arrivals and dependency schedule for both arms, the preserved outcome oracle, qualified resource telemetry/reset, and predeclared repeated paired measurements before reporting a new performance result. Recheck authentication and the current budget/reservation before any new OCI allocation. An agent-versus-rules study additionally needs tasks where model discretion is useful and the same authority/review rules in both arms.

Research and OCI implementation: Nicholas Cusato. Fluxnova software: FINOS contributors.
