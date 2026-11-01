# FNV-06: OCI message, signal and batch qualification

**All 12 fixed case families passed across 42 workflow instances on Fluxnova 3.0.1 and fresh OCI managed PostgreSQL.** This is a functional qualification of correct recipients and state changes; it is not a throughput, latency or capacity comparison.

The same [frozen corpus](../../workloads/fnv06/cases.json) was previously [qualified locally](../local-20261101-fnv06/README.md). The cloud run used digest-pinned engine and collector images, verified database TLS, a fresh schema and an isolated network. No model or payment backend was called.

| Check | Independently audited result |
| --- | --- |
| Case families / workflow instances | 12 / 42 |
| Functional operations | 14, including four deliberately invalid requests with expected errors |
| Expected event completions | 13 |
| Waiting controls before cleanup | 29, all preserved until owned cleanup |
| Asynchronous batches | Three completed, with job completion evidence |
| Unintended transitions / duplicate end events | 0 / 0 |
| SQL reconciliation | Nine tables; 42 process histories; 29 waiting process roots and their 29 catch-scope children |
| Closeout | Owned engine stopped; temporary database and access deleted; reference data/services preserved |

The [collector](../../scripts/run_fnv06_cloud.py) retained original requests, deployed XML, complete counted REST inventories and batch-job records. A separate repeatable-read, read-only SQL transaction captured the final durable state before cleanup. The [independent auditor](../../scripts/audit_fnv06_functional.py) checked the saved evidence without contacting the engine or importing the collector. Intermediate suspension/reactivation states remain REST-observed; the SQL snapshot checks final state.

## Auditor correction after collection

The first SQL audit rejected an incorrect checker assumption that every waiting process has only one execution row. The pinned engine [parses standalone intermediate catches as scopes](https://github.com/finos/fluxnova-bpm-platform/blob/c465509aa71f76508274d9f464a2a90f83667525/engine/src/main/java/org/finos/fluxnova/bpm/engine/impl/bpmn/parser/BpmnParse.java#L1522) and [creates a child while deactivating its parent](https://github.com/finos/fluxnova-bpm-platform/blob/c465509aa71f76508274d9f464a2a90f83667525/engine/src/main/java/org/finos/fluxnova/bpm/engine/impl/pvm/runtime/operation/PvmAtomicOperationCreateScope.java#L38). The corrected checker requires exactly that pair and binds each subscription to its catch child. Negative tests reject missing, extra, misparented, misbound or changed executions. The initial rejection is retained privately. Raw evidence, the collector and expected business outcomes were unchanged; the cloud workload was not rerun.

## Retention and limits

The [sanitized aggregate](summary.json) records the executed source commit, both auditor hashes, the sealed-evidence hash and the archive hash. Raw request journals, SQL rows, connection metadata and cloud identities remain private. Reference preservation includes full unchanged content hashes for all nonmetering tables and a verified append-only idle-metering prefix.

This small synthetic population does not establish correlation p99, burst handling, batch-size scaling, maximum capacity, broker delivery guarantees, concurrent duplicate handling, message-start semantics, recovery or general exactly-once effects. Next: declare separate message fan-in, signal fan-out and batch-size load protocols, with fixed rates, populations, repeats, resets and failure accounting.
