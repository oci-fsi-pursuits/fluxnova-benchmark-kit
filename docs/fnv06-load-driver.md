# FNV-06: measuring when waiting workflows actually finish

A request being accepted is not the same as the work being finished. This qualification checks that the load driver sends exactly the intended requests and that a separate observer waits for the intended workflows to complete.

The [routing qualification](fnv06-events-and-batches.md) answers **which workflows move?** This stage answers **can we reliably generate and observe a small burst?** A later, separately declared performance study will ask how that behavior changes with load.

## Three distinct operations

| Pattern | What the driver sends | What must happen |
| --- | --- | --- |
| Individual messages | One keyed message per intended recipient, using up to four clients | Every intended workflow completes once; other workflows keep waiting |
| Signal broadcast | One named signal | Every matching subscriber completes once; subscribers to other names keep waiting |
| Asynchronous message batch | One request listing the intended process IDs | Every listed workflow completes once; controls keep waiting; the batch later reaches its terminal state |

These operations have different delivery semantics. A broadcast is not a drop-in substitute for a targeted message, and batch acceptance is not a completion result.

## Fixed qualification

The [protocol](../workloads/fnv06/load-qualification.json) fixes recipient populations at **1, 4 and 16**, with two waiting controls per observation. Its nine observations create 81 workflows, expect 63 completions and retain 18 controls. Native k6 sends 27 HTTP requests in total, including three asynchronous batch requests.

All inputs and the intended recipients are recorded before dispatch. Each observation uses new workflow definitions and event names. Earlier controls are checked again after later observations. The generator is checksum pinned, has a future release time and must account for every request, including HTTP or transport failures.

A separate observer checks finished process counts every 100 ms. It records the interval in which all intended recipients finished. HTTP acknowledgment time, recipient completion and asynchronous batch bookkeeping are kept separate. The completion limit is 30 seconds; the subsequent batch-terminal limit is 120 seconds. These are qualification bounds, not proposed production service levels.

The [local qualification](../validation/local-20261101-fnv06-load/README.md) passed on a fresh Fluxnova 3.0.1 / Java 21 / H2 engine. The same nine observations subsequently passed the separate [OCI/PostgreSQL qualification](../validation/oci-20261101-fnv06-load/README.md), including final SQL reconciliation and owned cleanup. These timings are diagnostics only: there is one observation per cell, no performance warmup and no capacity claim.

## Run and independently audit

Prepare the verified engine and Java installation described in the [functional guide](fnv06-events-and-batches.md). Install the pinned k6 binary, then use fresh output paths:

```powershell
python scripts/ci_tools.py
python scripts/run_fnv06_load_qualification.py --output dist/fnv06-load-NEW
python scripts/audit_fnv06_load_qualification.py --source dist/fnv06-load-NEW --output dist/fnv06-load-NEW-audit
```

The collector owns and stops its local engine and k6 processes. The auditor reads sealed files without calling a running service or importing the collector. It checks raw k6 logs, original requests, counted state inventories, completion observations, exact recipients, unchanged controls and cleanup. A zero k6 exit code alone cannot pass.

The [cloud runner](../scripts/run_fnv06_load_cloud.py) reuses the owned, isolated PostgreSQL cell. It adds a read-only SQL snapshot before workflow cleanup and checks the final durable state against REST. It needs guarded operator staging, a fresh database, a bounded cost window and separate infrastructure/access closeout. It does not allocate resources itself or change the reference deployment.

## Next: performance experiments

The protocol records candidate populations of 10, 100 and 1,000 with five repeats. They are **not executable or approved performance cells yet**. Before running them, freeze separate message, signal and batch experiments with an explicit comparison question, fresh equivalent state, counterbalanced order, warmup, generator headroom, telemetry, deadlines, drain rules and a cost window. Define fan-in rate tests separately from these finite bursts.

Retain every attempt and unfavorable result. Report useful completed work, errors and missed deadlines alongside latency; do not compare acknowledgment-only timings to completed-work timings. This pattern has no payment backend or live model and does not establish broker delivery guarantees or general exactly-once effects.

The [larger-population qualification and planned comparison](fnv06-population-scaling.md) now cover complete pagination at 10, 100 and 1,000 recipients. The local and [PostgreSQL scale gates](../validation/oci-20261101-fnv06-scale/README.md) passed, followed by an owned reset rehearsal. Equal warmup state, a reusable reset path and telemetry qualification still precede the declared 45-cell campaign.
