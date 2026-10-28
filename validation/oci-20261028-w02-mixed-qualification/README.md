# W02-M2 cloud qualification — 28 October 2026 UTC

All four qualification runs passed independent audit at source `5b694047378686aad185aa718429dd3e32a47564`. The 300 measured arrivals produced 120 correct settlements, 120 expected refusals and 60 safe unknown-check stops. Another 300 warmup outcomes verified separately. There were 54 measured two-second deadline misses, no dropped arrivals and no incorrect or unresolved outcomes. Deadline misses are reportable under the frozen [M2 protocol](../../docs/w02-mixed-cloud-protocol.md); correctness, reset, generator and telemetry gates still apply.

| Arm | Starts/s | Correct outcomes | Within 2 s | Late |
| --- | ---: | ---: | ---: | ---: |
| sequential | 0.5 | 50/50 | 39/50 | 11 |
| parallel | 0.5 | 50/50 | 48/50 | 2 |
| parallel | 1 | 100/100 | 83/100 | 17 |
| sequential | 1 | 100/100 | 76/100 | 24 |

These are single qualification runs, not the five-pair comparison. Only verified settlements count toward settlement goodput. The [summary](summary.json) retains separate settlement, refusal and blocked denominators; pooling these outcomes must not imply payment throughput. The highest common qualified rate was 1/s.

The independent audit reconstructed raw events and checked the original persisted stores: 600 registrations, 240 effects, 1,800 receipts and 120 exercised one-time faults. Functional checks passed all 20 cases, 120 repeated calls, 20 premature-effect refusals and two cross-process replay probes. All 331 exported files passed hash verification; generated credentials were excluded. Controller headroom and engine/backend/worker/SQL telemetry passed; this does not measure managed-database CPU or full distributed traces.

## Interrupted continuation and cleanup

A workstation SSH status read reset. The original observer cancelled on that one transport error, although all four qualification runs completed on the controller. **No paired measurement started and no comparative improvement is claimed.** The reason for the connection reset and the delayed cleanup observation is unresolved. The failed continuation, original evidence and cleanup logs remain retained privately.

The owned database was confirmed deleted at 2026-10-28T03:55:41.825655+00:00. Original services, verifier, database access and non-meter data passed preservation checks; only routine meter rows appended. Owned controller containers were removed and temporary sleep inhibition was released.

The recovery change bounds read-only status retries and SSH timeouts. Returned workload failures and ownership errors are not retried, and payment submissions/workloads are never resubmitted by this observer. All 382 repository tests pass with zero skips. A new target must pass the same frozen qualification before the ten measured arms.

The updated estimate (retained in the private research archive) reserves the first attempt's full lifetime plus a fresh six-hour allowance: **$210.30 under $300**, with reference coverage through 28 October 12:00 UTC and the retained $100 uncertainty allowance. This is a planning estimate, not complete billing or a provider spending cap.

Research and OCI implementation: Nicholas Cusato. Fluxnova software: FINOS contributors.
