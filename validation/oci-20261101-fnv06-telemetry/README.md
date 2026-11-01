# FNV-06: observe resource use and continue safely after a failed request

The 1 November 2026 UTC trial verified resource observations during actual message workloads and the first native reset after a deliberately failed job. It used one temporary PostgreSQL database and preserved the reference environment.

| Cell | Purpose | Test workflows | Failed requests | Correct final test workflows |
| --- | --- | ---: | ---: | ---: |
| C01 | Deliberate lock / timeout | 10 | 4 | 10 |
| C02 | After guarded reset, no injected fault | 1,000 | 0 | 1,000 |

Each cell also ran the same 100-workflow warmup. Across both cells, **200 warmup and 1,010 test workflows completed correctly**, while all eight waiting controls remained unchanged. Of 1,210 native requests, **four timed out and remain failures**. Complete REST inventories reconciled with nine SQL tables in each cell.

The first test deliberately held a database lock through the unchanged 30-second request timeout. The lock was released at the declared 36-second point; all intended work then completed. The database observations recorded relation-lock waiting. After the independent outcome audit, the guarded reset archived the original failure marker, verified unchanged outside-schema catalogs and advanced to C02. C01's sealed evidence and four failed requests were preserved.

The new resource collector sampled both the engine and the combined k6/Python-observer container every 100 ms, with database activity/counter observations every 500 ms. The independent audit verified owned process identities, limits, original clocks and samples bracketing each work interval. The generator headroom check **passed in all four warmup/test windows**. CPU and memory constraints are reported separately from correct workflow outcomes.

These are **qualification observations, not an improvement comparison**. The injected delay makes C01 unsuitable as a performance baseline for C02. The database figures describe sampled activity and lagging database-wide counters, including observer overhead; they do not establish database host CPU or memory headroom. This trial exercised messages. Resource validity for every later signal, batch or message measurement still needs its own retained audit. The earlier uncontrolled signal timeout remains unchanged.

All 569 local tests passed with native k6 and no skips. Prior evidence re-audits were unchanged, and incomplete/corrupt evidence controls remained rejected. The temporary database, containers and Bastion session were retired. Raw logs, identifiers and SQL rows remain private; the [aggregate](summary.json) retains audit and archive hashes.

Next: finish the campaign runner and accounting for all planned, attempted, failed and unattempted cells, then perform the [declared 45-cell comparison](../../docs/fnv06-population-scaling.md) with resource/headroom checks on every measured cell and a refreshed cost reservation. That comparison has not started. See the [resource and reset guide](../../docs/fnv06-repeated-cells.md).
