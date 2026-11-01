# FNV-06 local routing and batch qualification

All **12 case families passed** on Fluxnova 3.0.1, Java 21 and a fresh local H2 database. The independently checked population contains **42 workflow instances**, fourteen functional operations and three asynchronous batches. Thirteen intended event completions occurred; twenty-nine processes remained waiting until owned cleanup. No unintended transition or duplicate end event was observed.

This is a functional qualification, not a performance comparison. It used no OCI resource, external payment backend or model call. The original reference environment was not accessed.

| Group | Cases | What passed |
| --- | ---: | --- |
| Message correlation | 6 | Exact match, unmatched/ambiguous rejection, consumed-message repeat, tenant selection and intentional all-matches delivery |
| Signal delivery | 4 | Broadcast membership, execution targeting, missing-execution rejection and tenant filtering |
| Asynchronous batches | 2 | Four-member message delivery and four-member suspend/reactivate operations, with two excluded controls in each case |

Four deliberately invalid requests returned their declared errors. The missing-execution signal's HTTP 500 is expected behavior of the pinned REST implementation; it produced no fallback broadcast or incident.

The [separate auditor](../../scripts/audit_fnv06_functional.py) checked 52 sealed files. It bound the frozen inputs and deployed BPMN to the request journal, complete counted REST inventories, per-process activity/variable history, exact recipient sets and successful batch job records. It checked earlier-case controls in every later snapshot. This is an independent analysis of retained REST evidence, not an independent SQL/H2 store audit.

The first setup attempt stopped before creating any workflow because the engine rejected hyphenated tenant IDs. Its evidence remains retained. A fresh attempt used alphanumeric IDs and completed all cases. Both owned engines stopped cleanly; the successful attempt removed all active test processes, subscriptions and batches and ended with no incidents or external tasks. No failed performance arm was replaced; no performance arm was run.

See the [aggregate receipt](summary.json), [case corpus](../../workloads/fnv06/cases.json) and [run/audit guide](../../docs/fnv06-events-and-batches.md). OCI/PostgreSQL qualification, burst/fan-in behavior, batch-size sweeps, concurrent duplicate races and recovery remain open. Research and implementation: **Nicholas Cusato**; Fluxnova software: **FINOS contributors**.
