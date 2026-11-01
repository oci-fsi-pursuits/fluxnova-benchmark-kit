# FNV-06: a timeout is not the same as a verified outcome

The six-cell OCI warmup/reset qualification stopped at its fourth cell on 1 November 2026 UTC. The result is retained as **partial**, including the unfavorable observation.

| Cell | Pattern | Test recipients after 100 warmup recipients | Result |
| --- | --- | --- | --- |
| C01 | Individual messages | 10 | Independent outcome and SQL audit passed |
| C02 | Individual messages | 1,000 | Independent outcome and SQL audit passed |
| C03 | Broadcast signal | 10 | Independent outcome and SQL audit passed |
| C04 | Broadcast signal | 1,000 | Request timed out; final outcome evidence incomplete |
| C05 | Asynchronous message batch | 10 | Not started |
| C06 | Asynchronous message batch | 1,000 | Not started |

The three passing cells account for 1,332 workflows: 1,320 verified completions and 12 unchanged controls. Each reconciled nine PostgreSQL tables. The message cells had matching declared warmup-state signatures. Three owned schema resets passed, including verification that outside-schema catalogs were unchanged.

For C04, the single native k6 signal request timed out at the unchanged 30-second limit. The observer later saw the intended definition's completed-process counter reach 1,000, approximately 46.9 seconds after release. That counter is a diagnostic, not verification of every recipient, variable or control. The collector stopped before its final state inventory and SQL reconciliation, so **the 1,000 outcomes are not counted as verified**. One observation does not establish a performance distribution or maximum capacity.

## What was corrected

An earlier attempt passed C01 but its reset guard incorrectly counted the trial's own TOAST storage as unrelated objects. The transaction rolled back; the schema, role and all prior workflow history were verified intact. That attempt and its cleanup remain in the [aggregate](summary.json).

The corrected guard follows exact parent-table catalog links for [PostgreSQL TOAST storage](https://www.postgresql.org/docs/current/storage-toast.html), while preserving checks for unrelated objects. A direct negative control created a temporary dependent view in another owned test schema. The reset was rejected and rolled back; schema, role, storage, history and outside catalogs were verified unchanged. The temporary view and test schema were then removed before the successful reset sequence continued.

The request-failure path has also been updated to attempt final REST state and a separate SQL snapshot before owned cleanup, and to report unavailable captures explicitly. A timeout still fails the qualification; the 30-second limit is unchanged. Five regression tests cover late state capture, drain expiry, missing state and SQL-before-cleanup behavior. This collection change still needs a native cloud failure qualification.

Both temporary databases, containers and Bastion sessions were retired. The reference environment was preserved apart from verified append-only idle metering records. Raw logs, identifiers, HTTP responses and SQL rows remain private; the aggregate records evidence hashes and each attempt separately.

Next: qualify failure collection, complete the remaining [warmup/reset checks](../../docs/fnv06-repeated-cells.md), integrate actual workload resource sampling and generator calibration, then execute the [declared 45-cell comparison](../../docs/fnv06-population-scaling.md) with every failure and unattempted cell accounted for. The 45-cell study has not started.
