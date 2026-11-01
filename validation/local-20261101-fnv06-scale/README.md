# Larger FNV-06 local evidence qualification

Nine fixed observations passed at 10, 100 and 1,000 intended recipients for individual messages, broadcast signals and asynchronous message batches.

- 3,348 workflow instances: 3,330 intended completions and 18 unchanged waiting controls.
- 1,116 actual native k6 requests; three completed asynchronous batches.
- Complete counted pages for process, activity, variable, subscription and job inventories, including batch job logs exceeding a single page.
- No unintended transition; owned workflow cleanup and engine shutdown passed.

The [independent audit summary](summary.json) is derived from sealed local evidence on Fluxnova 3.0.1, Java 21, H2 and checksum-verified k6 v2.3.0. The raw evidence remains private. The collector and auditor are separate implementations; the auditor checks every retained request and complete recipient state. Regression checks also retain compatibility with the earlier local and PostgreSQL qualifications.

This is larger-population qualification, with one observation per cell and no performance warmup. It does not establish a speedup or capacity limit. The [comparison design](../../docs/fnv06-population-scaling.md) declares the subsequent 45-cell study and its remaining reset, telemetry and cloud gates.
