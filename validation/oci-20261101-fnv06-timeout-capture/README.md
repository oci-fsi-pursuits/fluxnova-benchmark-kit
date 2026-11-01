# FNV-06: verify what happened after a client timeout

The deliberate timeout-capture check passed on OCI managed PostgreSQL on 1 November 2026 UTC. **The job and its four timed-out requests remain failed.** The successful check establishes that their final workflow state can still be collected and independently verified.

After 100 ordinary warmup recipients, ten messages were dispatched using the unchanged four-client limit and 30-second HTTP timeout. A brief SHARE lock on the fresh trial database's event-subscription table blocked writes. The operator released it 36 seconds after scheduled dispatch. The evidence retains the original requests, native k6 responses, database lock samples and transaction release. This is an injected fault, not an explanation of the earlier signal timeout.

| What was checked | Retained result |
| --- | --- |
| Warmup requests | 100 successful |
| Test requests | Four timeouts and six successful responses |
| Final outcomes | All 110 warmup/test workflows completed correctly |
| Routing controls | Four unrelated workflows remained waiting |
| Independent reconciliation | Full REST inventories matched nine SQL tables |
| Failure status | Failed job retained; ordinary qualification auditor rejected it |

The dedicated auditor returns `expected_timeout_evidence_passed`. This result cannot authorize the normal schema reset or count as a passing performance run. It verifies this controlled request-failure path; it does not establish behavior for every possible failure or interruption. The previous [1,000-recipient signal failure](../oci-20261101-fnv06-repeat/README.md) remains unchanged, with incomplete final outcome evidence.

An earlier helper startup failed because its filename collided with Python's standard library. No workload or database schema had been started. That setup failure was sealed and retained; launching with Python's safe import path corrected it without changing the operator or benchmark source bytes.

The trial database, containers and temporary access were retired. Reference data and services were preserved apart from verified append-only idle metering. Raw identifiers, HTTP responses, SQL rows and logs stay private. The [safe aggregate](summary.json) binds the retained archives, source and independent audit with hashes. All 545 local tests passed with real k6 and no skips; re-auditing twelve earlier evidence sets preserved their results.

The [protocol and offline auditor](../../docs/fnv06-repeated-cells.md#deliberate-timeout-capture-check) are reusable. Next: complete the remaining cloud lifecycle checks, retain request failures and deadline misses in general accounting, and qualify integrated resource observations and generator calibration before the [45-cell comparison](../../docs/fnv06-population-scaling.md). That comparison has not started.
