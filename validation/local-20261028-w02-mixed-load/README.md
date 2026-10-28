# W02-M1 mixed-outcome load qualification

**Passed locally, 28 October 2026 UTC.** Four fixed runs verified all 300 measured arrivals: 120 exact settlements, 120 expected refusals and 60 safe stops on unknown checks. All 60 warmup outcomes also verified. There were zero dropped arrivals, incorrect outcomes or unresolved outcomes. **58 measured outcomes missed two seconds** and remain in the evidence.

This is qualification of continuous mixed-outcome accounting on Windows, Fluxnova 3.0.1, Java 21 and embedded H2. It is not a representative customer mix, matched OCI effect, live-agent result or sustainable-capacity claim. The [frozen protocol and commands](../../docs/w02-mixed-load.md) describe the controls. Existing presentation comparisons keep their original populations and results.

## Outcome and deadline counts

Each category cell shows **within two seconds / verified**. Only timely settlements count toward settlement goodput. Refused and blocked cases produced no effect. Every row includes whole cycles of the same 50-case grid.

| Workflow | Offered rate | Measured arrivals | Settlements | Expected refusals | Safe stops | Total late |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Sequential | 0.5/s | 50 | 15/20 | 16/20 | 8/10 | 11 |
| Parallel | 0.5/s | 50 | 18/20 | 20/20 | 10/10 | 2 |
| Parallel | 1/s | 100 | 36/40 | 26/40 | 18/20 | 20 |
| Sequential | 1/s | 100 | 30/40 | 29/40 | 16/20 | 25 |

Both arms use two worker slots, separate fixed 16-VU warmup/measurement pools, 100 ms polling, 20 seconds of warmup and 100 seconds of measurement. Each run owns fresh engine/backend state. The 15-second drain is separate from the two-second outcome deadline. Deadline misses were prespecified as reportable rather than a qualification failure; exact outcomes, zero drops, scheduling, bounded backlog and clean final state were still required. Do not rank these single-pass local observations as a paired speedup.

## Independent checks

The offline audit verifies all **88 sealed files**, reconstructs events from raw k6 logs and recomputes each assessment. Original SQLite state confirms **360 registrations, 156 exact effects, 1,080 receipts and 80 injected one-time faults**, including warmup. The outcome oracle retains original amount, beneficiary and authority, process/receipt binding and completion history. All four owned engines stopped with no remaining processes, external tasks or incidents.

Three negative controls reject a changed sealed file, an altered SQLite payment amount after resealing its hash, and altered settled/blocked report counts after resealing. The new audit's SQLite handles close on both success and failure, verified by the Windows cleanup regression. No source archive was changed.

Chrome automation verified all four run rows and twenty dependency-profile rows, JSON-link navigation, keyboard disclosure and laptop page width. Desktop, expanded-profile and laptop screenshots passed visual review. The offline report starts no workloads and makes no external requests. The repository gate passes **374 tests with zero skips**, including optional real-k6 fixture checks.

Source commit: `e5302a21a84412f247068bb1065c1902592ac3ba`. All 19 executed source files match that commit after CRLF/LF normalization; original bytes retain their own hashes. [Sanitized machine-readable evidence](summary.json) includes the protocol, corpus, runtime, source and archive hashes. Raw archives and browser captures remain in ignored local `dist/` storage.

## Next step and cloud status

Qualify the mixed driver and telemetry on the actual OCI setup, then predeclare repeated pairs and outcome-specific metrics. OCI session validation and refresh failed on 28 October UTC; fresh interactive sign-in is pending. The prior $298.89 planning reservation expired at 28 October 00:00 UTC and is not actual spending. Refresh receipt-reconciled cost coverage before further cloud allocation. The original reference remains allocated and billable; this local qualification created no OCI resource or model request.

Research and OCI implementation: Nicholas Cusato. Fluxnova software: FINOS contributors.
