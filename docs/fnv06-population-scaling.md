# FNV-06: what happens when more workflows need the same update?

Imagine ten payments waiting for a status update. Now imagine one hundred, then one thousand. The next comparison asks how long it takes to finish the intended work at each size while the machine and client settings stay fixed.

We analyze three patterns separately: individual keyed messages, one broadcast signal, and one asynchronous message batch. They have different routing semantics. A fast broadcast does not make it a substitute for a targeted message.

## Qualification before the repeated comparison

The [larger local qualification](../validation/local-20261101-fnv06-scale/README.md) passed nine observations on fresh Fluxnova 3.0.1 / Java 21 / H2: 3,330 expected completions, 18 unchanged controls and three completed batches. Native k6 issued all 1,116 planned requests. One observation per cell qualifies the driver and evidence handling; these timings are not repeated performance results.

The same larger observations also passed the [isolated PostgreSQL qualification](../validation/oci-20261101-fnv06-scale/README.md), including independent nine-table SQL reconciliation. After retaining and auditing that evidence, a one-time reset of the owned trial schema was followed by a passing small qualification from empty workflow state. This verifies the reset rehearsal. The subsequent [six-cell lifecycle attempt](../validation/oci-20261101-fnv06-repeat/README.md) passed three cells and three resets, then stopped on a 30-second signal-request timeout. The later completion counter is retained as a diagnostic; complete outcomes for that cell were not verified.

Larger populations exceed the previous 1,000-record inventory limit. The new collector counts before reading, fetches every 500-row page, requires an empty final page, then counts again. It rejects changing counts, missing pages and duplicate IDs. The independent auditor binds every page and count to its original request, including filtered batch job logs. Inventories are read while workload mutation is stopped; this does not make REST a database transaction. The separate cloud gate adds read-only SQL reconciliation.

```powershell
python scripts/ci_tools.py
python scripts/run_fnv06_load_qualification.py --scale --output dist/fnv06-scale-NEW
python scripts/audit_fnv06_load_qualification.py --source dist/fnv06-scale-NEW --output dist/fnv06-scale-NEW-audit
```

Use the verified engine and Java installation from the [load-driver guide](fnv06-load-driver.md). Always choose fresh output paths. The [scale qualification protocol](../workloads/fnv06/scale-qualification.json) declares 120-second recipient and 240-second batch-terminal bounds before execution. These are qualification limits, not customer service levels or revisions to the earlier small tests. HTTP requests retain their 30-second timeout.

## The planned comparison is explicit

| Item | Declared design |
| --- | --- |
| Baseline | 10 intended recipients |
| Changed factor | 100 or 1,000 intended recipients |
| Repeats | Five per pattern and population: 45 measured cells |
| Message clients | Four in every measured message cell |
| Starting state | Fresh owned schema, then the same 100-recipient warmup for that pattern |
| Primary result | Interval in which all intended recipients finished, measured from the scheduled release |
| Other results | Correct work observed by the synthetic two-second threshold, request errors, unfinished outcomes and resource use |

The [declared design](../workloads/fnv06/population-scaling-design.json) sets population and pattern order in advance. Each population occupies each order position once or twice across the five repeats. It keeps warmup separate, preserves controls, and requires every attempt and unfavorable result to remain accounted for. It plans 16,650 measured recipients, 4,500 warmup recipients and 180 controls.

**The 45-cell campaign is not running yet.** The PostgreSQL scale check and one-time reset rehearsal passed. The reusable cloud attempt is partial: three cells passed, the 1,000-recipient signal request timed out, and two batch cells were not started. The [controlled cloud timeout check](../validation/oci-20261101-fnv06-timeout-capture/README.md) now verifies final REST/SQL capture after an injected request timeout. The [six-cell outcome follow-up](../validation/oci-20261101-fnv06-outcomes/README.md) now verifies all six final outcome sets and five resets with request failures and deadline uncertainty retained. The [resource and failed-reset trial](../validation/oci-20261101-fnv06-telemetry/README.md) now verifies message-workload resource collection and failed-request reset. Complete campaign execution/accounting, retain resource/headroom results for every measured cell, and refresh the cost window before execution. The design file deliberately cannot start a campaign.

The results will describe finite bursts under declared client limits. They will not establish maximum sustainable throughput, production payment latency, a live-agent advantage, or a cross-pattern speedup. Five cohort observations also do not support a reliable cohort p99 estimate.

The [repeated-cell qualification guide](fnv06-repeated-cells.md) now records passing local warmup checks and the separate native Linux resource-reader probe. The earlier cloud attempt remains partial. Controlled timeout capture and the subsequent six-cell outcome lifecycle are now qualified. All requests succeeded in that six-cell follow-up. The later [resource and failed-reset trial](../validation/oci-20261101-fnv06-telemetry/README.md) verified reset after four deliberately timed-out requests, preserved those failures and collected audited resource observations during the following 1,000-recipient message cell. The campaign runner and complete planned/attempted/unattempted accounting remain open. Resource/headroom checks must accompany every measured cell, including signals and batches.
