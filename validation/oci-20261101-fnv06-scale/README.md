# FNV-06: larger waiting populations and a clean reset

The same driver used for the small qualification also delivered updates to 10, 100 and 1,000 intended workflows on isolated OCI PostgreSQL. This checks that the test machinery can handle larger populations and retain complete evidence before a repeated performance study.

On 1 November 2026 UTC, all nine observations passed independent audit:

- 3,348 workflows created: 3,330 intended completions and 18 controls that kept waiting.
- 1,116 native k6 requests across individual messages, broadcast signals and asynchronous message batches.
- Three completed batches, with request acknowledgment, recipient completion and batch bookkeeping checked separately.
- Complete counted 500-row pages, including inventories and batch job logs above 1,000 records.
- Nine PostgreSQL tables reconciled against final REST state in a separate read-only, repeatable-read transaction.

The larger evidence was sealed, downloaded and independently audited before a reset rehearsal. After the engine stopped, ownership and the exact 3,348 historical process IDs were checked on that same temporary database. Only its `fluxnova` schema and exact trial role were removed, in one transaction. A fresh engine then passed the small nine-observation qualification: 81 new workflows, 63 intended completions and 18 unchanged controls. Its initial workflow state was independently verified as empty, and its final nine SQL tables reconciled. The original larger evidence remained unchanged.

This is **one successful reset rehearsal**, not a qualified repeated-performance runner. Equal post-warmup state, resource telemetry, generator headroom and complete repeated-run failure accounting still require qualification. The [45-cell population comparison](../../docs/fnv06-population-scaling.md) is declared but has not run.

All temporary containers, the temporary database and Bastion access were retired. Reference images, services and stored data were preserved, apart from verified append-only idle metering records. Raw logs, identifiers, HTTP responses and SQL rows remain private; only the [sanitized aggregate](summary.json) is included here.

These are fixed-population qualification results, with one observation per cell and no performance warmup. They do not establish a speedup, sustainable capacity, production payment latency, a live-agent result or equivalence between the three delivery semantics. The [local larger qualification](../local-20261101-fnv06-scale/README.md) and [earlier small PostgreSQL qualification](../oci-20261101-fnv06-load/README.md) retain their separate evidence scopes.
