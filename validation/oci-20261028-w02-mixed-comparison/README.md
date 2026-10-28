# W02-M2: mixed payment outcomes

Status: **complete**. Phase: campaign.

Measured verified outcomes: {'settled': 2400, 'rejected': 2400, 'blocked': 1200}. Warmup: {'settled': 400, 'rejected': 400, 'blocked': 200}.

Settlements, expected refusals and safe unknown-check stops have separate denominators. Only settlements contribute to settlement goodput.

Median paired change in timely settlements: **+0.0517/s** (pair range +0.0467 to +0.0617/s).

| Workflow | Outcome | Verified / expected | Within 2 s | Late | Median run p95 |
| --- | --- | ---: | ---: | ---: | ---: |
| sequential | settled | 1200/1200 | 880 | 320 | 3869.9 ms |
| sequential | rejected | 1200/1200 | 780 | 420 | 3373.5 ms |
| sequential | blocked | 600/600 | 447 | 153 | 2883.8 ms |
| parallel | settled | 1200/1200 | 1037 | 163 | 3527.1 ms |
| parallel | rejected | 1200/1200 | 669 | 531 | 3250.5 ms |
| parallel | blocked | 600/600 | 539 | 61 | 2286.3 ms |

Synthetic seeded grid, five dependency profiles, two fixed workers and 100 ms completion polling. No live model or representative-customer/capacity claim.

Executed source: `2eb03249e65018e226efd21e3e3f50ab24f66ed6`. The audit verified 527 exported files and original SQLite stores.

## Dependency profiles

| Profile | Sequential timely settlements | Parallel timely settlements | Correct settlements, both arms |
| --- | ---: | ---: | ---: |
| fast | 240/240 | 240/240 | 480/480 |
| balanced | 228/240 | 240/240 | 480/480 |
| risk_dominant | 237/240 | 240/240 | 480/480 |
| kyc_dominant | 175/240 | 180/240 | 480/480 |
| both_slow | 0/240 | 137/240 | 480/480 |

Every arm covers the same ten-case policy/fault grid. Refusals and safe stops, their timing and telemetry remain in the [complete aggregate audit](summary.json). All five profiles pass outcome correctness; deadline misses are retained measurements.

## Evidence and retirement

The independently audited source, original input binding, store snapshots, reset fingerprints, resource controls and exported hashes are retained privately. Raw cloud inventory and credentials are not distributed. This aggregate summary and the [saved report](../../docs/results/w02-mixed/report.html) contain no live service connection.

The owned temporary PostgreSQL system and workload were retired. Original reference preservation passed. The original reference remains allocated and billable. Full attributed cost per correct outcome remains open.

The earlier qualification and interrupted handoff remain retained; they are separate from this completed five-pair series. The first handoff interruption started no paired arm. No failed measured arm was replaced to obtain this result.

Research and OCI implementation: Nicholas Cusato. Fluxnova software: FINOS contributors.
