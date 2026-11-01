# FNV-06 PostgreSQL load-driver qualification

The same small qualification that passed locally also passed on isolated OCI managed PostgreSQL on 1 November 2026 UTC.

- Nine observations: individual messages, signal broadcast and asynchronous message batch at 1, 4 and 16 intended recipients.
- 27 real k6 requests, 81 workflow instances, 63 expected completions and 18 unchanged waiting controls.
- Three completed asynchronous batches, with recipient completion observed separately from request acknowledgment and batch bookkeeping.
- A separate read-only, repeatable-read SQL snapshot reconciled all nine declared tables against the final REST inventories before cleanup.
- The temporary engine, database and Bastion access were retired. Reference service/image identities and stored data were preserved, apart from verified append-only idle metering records.

The [aggregate audit](summary.json) contains no private connection details or raw records. Original inputs, HTTP responses, k6 logs, counted inventories, polling observations and SQL rows remain in sealed private evidence. The independent auditor imports neither the workflow collector nor the SQL reader. Raw evidence was not uploaded.

This closes the **small driver and observer qualification**, not the performance study. There is one observation per cell, no performance warmup and no throughput, p99, speedup or maximum-capacity claim. These routing operations have different semantics and cannot be ranked as interchangeable implementations. There is no payment backend or live model in this pattern.

An earlier attempt stopped at collector startup because the standalone script lacked its source import path. It created no test workflows and supplied no workload result. Its evidence and cleanup receipts were retained. A regression check was added, all 514 tests and exact-commit CI passed, and this qualification used a new database and run identity. No valid measured result was replaced.

The [load-driver guide](../../docs/fnv06-load-driver.md) describes reproduction and the gates for separately frozen message, signal and batch performance experiments. The earlier [routing qualification](../oci-20261101-fnv06/README.md) covers matching, ambiguity, tenant selection, repeat messages and suspension behavior separately.
