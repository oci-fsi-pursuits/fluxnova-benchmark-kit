# FNV-06: all six lifecycle cells have verified final outcomes

The follow-up on 1 November 2026 UTC completed all six fixed cloud cells and five owned schema resets. It used the same 100-recipient warmup before each test burst, the unchanged 30-second HTTP timeout and the same fixed client limits. Each cell reconciled its full REST inventories against nine PostgreSQL tables before the next cell started.

| Cell | Pattern | Test recipients | Failed test requests | Correct final test outcomes | Correct by two seconds: lower–upper bound |
| --- | --- | ---: | ---: | ---: | ---: |
| C01 | message | 10 | 0 | 10 | 10–10 |
| C02 | message | 1,000 | 0 | 1,000 | 143–152 |
| C03 | signal | 10 | 0 | 10 | 10–10 |
| C04 | signal | 1,000 | 0 | 1,000 | 0–0 |
| C05 | batch | 10 | 0 | 10 | 10–10 |
| C06 | batch | 1,000 | 0 | 1,000 | 366–394 |

Across warmup and test phases, **600 warmup and 3,030 test workflows completed correctly**. All 24 waiting controls remained unchanged. **All 1,218 native requests succeeded.** The collector status for each cell is retained in the [aggregate](summary.json). Warmup and test denominators are separate.

The deadline column reports conservative bounds from completion polling. Work observed by a poll that finished within two seconds contributes to the lower bound; a poll started after the threshold constrains the upper bound. The difference is uncertain. These counts are supported by the final outcome audit, but they do not identify individual completion timestamps. The two-second threshold is synthetic, not a customer SLA.

**This is lifecycle and evidence qualification, not a repeated performance result.** There is one test observation per cell, with 10 and 1,000 recipients. The separately declared performance design adds 100 recipients and five repeats per pattern/population. These three routing patterns have different semantics and must not be ranked as interchangeable alternatives.

Five resets verified empty owned runtime state, exact prior workflow history and unchanged outside-schema catalogs. All requests succeeded in this follow-up. The reset path after a failed request was not exercised and remains a separate native cloud validation gate. The earlier controlled timeout-capture check remains separate evidence. Incomplete or unknown outcomes cannot authorize a reset. The earlier [signal timeout with incomplete final evidence](../oci-20261101-fnv06-repeat/README.md) remains separate and unchanged; the follow-up does not retrospectively repair it. The successful signal request in this follow-up is not an attributed improvement; the original and follow-up are individual observations, not a paired performance study.

The temporary database, containers and Bastion session were retired. Reference data and services were preserved apart from verified append-only idle metering. Raw identifiers, logs, HTTP responses and SQL rows remain private. The aggregate includes source, audit and archive hashes. All 557 local tests passed with native k6 and no skips; five resealed corrupt-evidence controls were rejected.

The subsequent [resource and failed-reset trial](../oci-20261101-fnv06-telemetry/README.md) verified reset after four deliberately timed-out requests and actual message-workload resource observations. Next: finish campaign execution/accounting and per-cell resource/headroom checks before the [45-cell population comparison](../../docs/fnv06-population-scaling.md). That comparison has not started. Use the [offline outcome auditor](../../docs/fnv06-repeated-cells.md#separate-request-failures-from-final-outcomes) to inspect retained cells.
