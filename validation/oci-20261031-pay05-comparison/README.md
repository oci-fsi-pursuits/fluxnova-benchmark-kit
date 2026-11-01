# PAY-05: request FX quotes together, then verify the same payment

Five matched OCI comparisons found that parallel quote requests improved the timeliness of correct synthetic settlements under the declared delay profiles. **668 of 1,530 sequential settlements** met the two-second deadline, compared with **1,530 of 1,530 parallel settlements**. Both designs produced every expected payment effect correctly.

All **3,960 measured workflows** reached the expected outcome: 3,060 settlements, 180 unauthorized-payment rejections and 720 safe stops. All **1,106 measured deadline misses** remain in the result; none was replaced or discarded. The independent audit passed all ten measurement arms and both preceding qualification lanes.

The primary result is a **median paired increase of +0.2184 timely settlements per measured second**, with a five-pair range of **+0.2109 to +0.2285/s**. Offered traffic stayed at 0.5 starts/s in both designs. Observed settlement p95 across runs was 3.477-3.481 seconds sequentially and 1.544-1.546 seconds in parallel. These times include 100 ms completion polling.

## What changed

The baseline requests quotes A, B and C in sequence. The treatment requests them concurrently. Both wait for all three receipts, then apply the same deterministic eligibility, source-amount-plus-fee and payment-authority policy. Both have three worker slots and identical resources. The change overlaps independent waiting; it does not change the quote prices or payment rules.

The frozen trace crosses all 22 policy/fault cases with three synthetic delay profiles. Every measured arm repeats the same 66-case trace six times after one trace of warmup. Each arm starts from a restored and fingerprinted PostgreSQL schema and SQLite ledger. The offered rate, trace, pair-order seed, two-second deadline, resources and stop rules were declared before comparison timing. Three pairs start sequentially and two start in parallel.

| Pair | Run order | Sequential timely settlements | Parallel timely settlements | Observed settlement p95, sequential / parallel (s) | Paired timely-settlement change |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | parallel / sequential | 139/306 | 306/306 | 3.477 / 1.546 | +0.2109/s |
| 2 | sequential / parallel | 138/306 | 306/306 | 3.478 / 1.544 | +0.2121/s |
| 3 | sequential / parallel | 133/306 | 306/306 | 3.480 / 1.545 | +0.2184/s |
| 4 | parallel / sequential | 133/306 | 306/306 | 3.480 / 1.544 | +0.2184/s |
| 5 | sequential / parallel | 125/306 | 306/306 | 3.481 / 1.546 | +0.2285/s |

The range describes these five paired observations; it is not a confidence interval or a maximum-capacity estimate. Each measurement lasts 792 seconds with 396 offered starts, preceded by 132 seconds and 66 warmup starts. Warmup and qualification results are retained separately in the aggregate.

## Where the improvement appeared

| Synthetic profile | A/B/C delay (ms) | Sequential timely settlements | Parallel timely settlements |
| --- | --- | ---: | ---: |
| balanced | 200/200/200 | 510/510 | 510/510 |
| one slow provider | 100/400/900 | 158/510 | 510/510 |
| all providers slow | 900/900/900 | 0/510 | 510/510 |

The balanced profile met the settlement deadline in both designs. The gains appear when one or all providers are slower. These are equal-coverage sensitivity cases, not measured customer traffic or provider-response distributions. Quote expiry and expected eligibility were fixed for this population; scenarios where timing changes the eligible route need their own protocol.

| Expected outcome, per design | Correct sequential / parallel | Within two seconds, sequential / parallel |
| --- | ---: | ---: |
| Settlement | 1,530 / 1,530 | 668 / 1,530 |
| Unauthorized-payment rejection | 90 / 90 | 40 / 90 |
| Safe stop | 360 / 360 | 166 / 360 |

Rejections and safe stops have separate denominators and do not count as settlement throughput. The raw receipts bind the original payment, process identity, decision clock, quote eligibility and exact effect. No live model, real FX provider or payment network was used. Signed-intent integration and a matched agent-versus-rules comparison remain separate work.

## Retry overhead

Parallel execution also caused **910 cached backend replays during the five measured arms**, versus zero sequentially. There were no duplicate payment effects. The latency results already include this retry cost; parallel execution was not free of contention.

A separate [post-run auditor](../../scripts/audit_pay05_retries.py) reconciled all 1,109 retained engine optimistic-lock HTTP 500 errors with cached replay receipts by process identity and execution window, and with the worker retry records. Concurrent updates to the engine execution state explain the retained failures. The total separates into 910 performance-measurement replays, 138 performance-warmup replays and 61 qualification replays. These were cached synthetic-backend responses, not additional real FX-provider requests or an inventory of every network call.

The first diagnostic total included 33 qualification-measurement replays in its measurement label. The corrected auditor separates stage and phase explicitly; raw results and per-lane counts did not change. Both diagnostic outputs are retained privately. The read-only log-collection session was closed after audit. Any change intended to reduce contention needs a separately declared comparison.

## Evidence and cleanup

The [independent auditor](../../scripts/audit_pay05_performance.py) verified **506 sealed files**, including raw generator events, SQL process history, saved SQLite ledgers, unique worker-task records, the frozen design and all twelve complete state resets. Including qualification and warmup, all **4,884 outcomes** were correct, with 3,774 expected synthetic effects and 1,110 expected no-effect outcomes. Generator, controller and container/SQL telemetry gates passed. This telemetry does not establish managed-database CPU utilization or complete distributed tracing.

The runtime was Fluxnova 3.0.1 with OCI PostgreSQL 17.6. Engine, generator, quote backend and workers shared one 2-OCPU/16-GB controller; the fresh database had one 2-OCPU/32-GB node. Container limits, image digests, per-arm telemetry, profile p95/p99 and task/replay counts are recorded in the [aggregate evidence](summary.json). This topology differs from the four-host reference.

The temporary database, containers and network are retired. All five owned Bastion sessions are closed or expired, and the local tunnel port is free. All 48 non-metering reference tables, schema/access metadata, original services and verifier were preserved. Full hashes matched the original 17,388 metering rows and 144 later periodic appends. The original reference remains allocated and billable; complete cost per outcome is still open.

The final administrative recheck needed renewed isolated authentication after the measured run, independent audit and database-deletion confirmation had completed. The retained deleted-resource identity was reconciled with a fresh authorized listing. This did not alter or repeat any benchmark arm. Earlier functional overlap-probe failures and the prior load qualification remain separate evidence.

## Reproduce or adapt

Read the [workflow guide](../../docs/pay05-fx-quotes.md), [frozen protocol](../../workloads/pay05/performance-protocol.json), [trace/design resolver](../../benchlib/pay05_performance.py) and [owned-stage runner](../../scripts/run_pay05_performance_cloud.py). Start with local functional qualification, declare an isolated deployment and cost/cleanup window, then qualify the exact performance trace before measuring pairs. Cloud allocation and integration preparation remain operator responsibilities.

To audit a retained raw evidence directory without starting workflows:

```bash
python scripts/audit_pay05_performance.py --source PRIVATE_EVIDENCE_DIRECTORY --output NEW_AUDIT_DIRECTORY
```

The measured source revision passed 469 local tests with real k6 and no skips, plus Linux/Python 3.12, Windows/Python 3.13 and secret-scanning CI. Raw stores and environment metadata remain private. Research and OCI implementation: **Nicholas Cusato**; Fluxnova software: **FINOS contributors**.
