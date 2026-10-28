# Completed workflow patterns

The thesis is **time to a correct, authorized outcome**. The three workflow patterns and their mixed-outcome follow-up are complete in their declared scopes. There are no measured arms left in this phase. Each study has its own control, population and limits.

| Study | One changed factor | Audited result | Boundary |
| --- | --- | --- | --- |
| [W01: worker pacing](../validation/oci-20261027-w01-comparison/README.md) | Reconstructed 100 ms success delay versus idle-only backoff | Timely payments 600/1,200 to 1,200/1,200; all 2,400 correct | Five matched synthetic burst pairs; not maximum capacity |
| [W02: approved payments](../validation/oci-20261027-w02-comparison/README.md) | Sequential versus parallel independent KYC/risk | Median paired observed p95 reduced 32.1%; 6,000/6,000 correct and on time; deadline goodput unchanged at 1/s | Fixed 400/600 ms dependencies, two workers in both arms |
| [W03: review routing](../validation/local-20261027-w03/README.md) | All-review control versus fixed-policy routing | Review tasks 10 to 4; all four mandatory reviews preserved; 20/20 expected outcomes | Local synthetic cases and scripted review; no human time-saving claim |
| [W02-M2: mixed outcomes](../validation/oci-20261028-w02-mixed-comparison/README.md) | Sequential versus parallel checks over the same mixed corpus | Timely settlements 880 to 1,037 of 1,200; timely refusals 780 to 669 of 1,200; all 6,000 outcomes correct | Five paired OCI measurements at 1/s; five dependency profiles; latency includes 100 ms polling |

The mixed study improved settlement timeliness and worsened refusal timeliness. Its median paired change was **+0.0517 timely settlements/s**, with a pair range of +0.0467 to +0.0617/s. Safe stops within two seconds increased from 447 to 539 of 600. All **1,648 deadline misses** remain reported. Refusals and safe stops do not count as payment throughput; paired ranges are not confidence intervals.

## All five dependency profiles completed

The frozen corpus crosses ten policy/fault cases with five delay profiles. Both arms retain two workers, original-input binding and exact-effect checks.

| Profile | KYC / risk delay | Sequential timely settlements | Parallel timely settlements | Correct settlements, both arms |
| --- | --- | ---: | ---: | ---: |
| Fast | 50 / 70 ms | 240/240 | 240/240 | 480/480 |
| Balanced | 400 / 400 ms | 228/240 | 240/240 | 480/480 |
| Risk dominates | 100 / 900 ms | 237/240 | 240/240 | 480/480 |
| KYC dominates | 900 / 100 ms | 175/240 | 180/240 | 480/480 |
| Both slow | 900 / 1,100 ms | 0/240 | 137/240 | 480/480 |

The ten cases cover approval, sanctions refusal, risk refusal, unauthorized action, required review, unknown KYC, unknown risk, retry before a receipt, reconciliation after a receipt and concurrent completion. Equal coverage is a synthetic test design, not a customer frequency distribution. The [full aggregate audit](../validation/oci-20261028-w02-mixed-comparison/summary.json) retains all outcomes, per-pair changes, profiles and telemetry.

## Evidence and cleanup

All ten mixed arms passed independent audit. The audit checked 527 exported files and original stores, including 7,000 cases with warmup, source/control hashes, reset evidence and exact effects. Browser checks passed six outcome rows, fifteen paired rows and fifty profile rows; the saved report passed visual review.

The owned W01, W02 and mixed-study databases/workloads are retired. Original-reference preservation passed. The original reference remains allocated and billable; full attributed cost per correct outcome is still open.

The [first mixed qualification and interrupted handoff](../validation/oci-20261028-w02-mixed-qualification/README.md) are retained. No paired measurement started before that interruption. A fresh target [requalified independently](../validation/oci-20261028-w02-mixed-requalification/README.md) before the completed series. No failed measured arm was replaced to obtain a favorable result. Earlier [functional sensitivity](w02-dependency-sensitivity.md) and [local mixed qualification](w02-mixed-load.md) remain separate evidence.

## Read the result or reproduce your own

Open the [saved report](results/w02-mixed/report.html) from a local checkout:

```bash
python scripts/open_report.py --path docs/results/w02-mixed/report.html
```

The page shows retained audited executions and starts no payments. Use the [one-page brief](assets/fluxnova-research-brief-20261028.pdf), [getting-started guide](getting-started.md), [benchmark protocol](benchmark-protocol.md) and [portability guide](portability.md) to reuse the work.

Next research requires representative customer inputs and policies, complete cost attribution, then separately controlled runtime/platform/database or agent-versus-rules studies. Current results do not establish customer capacity, live-model advantage, human productivity, cross-cloud parity or a Java/Spring upgrade benefit.
