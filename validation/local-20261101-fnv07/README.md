# FNV-07 local retry and effect qualification

The six declared cases passed on one native Fluxnova 3.0.1 engine, Java 21.0.12.1 and a fresh H2 database. This is a functional gate for the [cluster-job pattern](../../docs/fnv07-cluster-jobs.md), with no OCI access or performance comparison.

| Observation | Independently verified result |
| --- | --- |
| Workflow population | 6 original instances |
| Terminal states | 3 completed, 2 deliberately blocked, 1 untouched waiting control |
| Delegate attempts | 19, including all 8 deliberately unsuccessful attempts |
| Backend effects | 12 distinct receipts, with cached replays kept separate |
| Historic jobs | 13 creations, 11 successes, 8 failures |
| Blocked after an effect | Retained as a blocked workflow, not counted as completed |
| Runtime cleanup | Owned engine and probe stopped |

The auditor matched original input hashes and process identities across counted REST inventories, job history, delegate logs, engine variables and the original SQLite ledger. It checked effective executor settings and the Java delegate artifact. H2 durable engine SQL was not independently inspected; the engine state came from REST, while the backend ledger was read directly from SQLite.

A separate deliberate failure after real collection verified that failure snapshots, logs and the ledger remained sealed after cleanup. The auditor rejected that failed collection. It is retained separately and is not an additional successful trial.

The fixture enforces one backend effect per registered workflow step. These results do not establish a general exactly-once network guarantee, clustering, managed PostgreSQL behavior, throughput or latency improvement. The initial auditor and its later stricter re-audit both remain in the private evidence; the latter also checks exact job/stage identity and effect-to-attempt binding.

[Machine-readable summary](summary.json). Raw logs, generated identities, local credentials and private operator files are excluded from the release inventory.
