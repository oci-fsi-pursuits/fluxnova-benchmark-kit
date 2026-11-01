# Completed workflow patterns

The thesis is **time to a correct, authorized outcome**. The W01, W02 and W03 studies, the W02 mixed-outcome follow-up, and the PAY-05 FX-quote comparison are complete in their declared scopes. There are no measured arms left in these studies. The broader original research catalogue remains open. Each study has its own control, population and limits.

| Study | One changed factor | Audited result | Boundary |
| --- | --- | --- | --- |
| [W01: worker pacing](../validation/oci-20261027-w01-comparison/README.md) | Reconstructed 100 ms success delay versus idle-only backoff | Timely payments 600/1,200 to 1,200/1,200; all 2,400 correct | Five matched synthetic burst pairs; not maximum capacity |
| [W02: approved payments](../validation/oci-20261027-w02-comparison/README.md) | Sequential versus parallel independent KYC/risk | Median paired observed p95 reduced 32.1%; 6,000/6,000 correct and on time; deadline goodput unchanged at 1/s | Fixed 400/600 ms dependencies, two workers in both arms |
| [W03: review routing](../validation/local-20261027-w03/README.md) | All-review control versus fixed-policy routing | Review tasks 10 to 4; all four mandatory reviews preserved; 20/20 expected outcomes | Local synthetic cases and scripted review; no human time-saving claim |
| [W02-M2: mixed outcomes](../validation/oci-20261028-w02-mixed-comparison/README.md) | Sequential versus parallel checks over the same mixed corpus | Timely settlements 880 to 1,037 of 1,200; timely refusals 780 to 669 of 1,200; all 6,000 outcomes correct | Five paired OCI measurements at 1/s; five dependency profiles; latency includes 100 ms polling |
| [PAY-05: FX quote selection](../validation/oci-20261031-pay05-comparison/README.md) | Sequential versus parallel quote requests before the same selector | Timely settlements 668/1,530 to 1,530/1,530; all 3,960 measured outcomes correct | Five paired OCI measurements at 0.5/s; three synthetic delay profiles; 910 measured cached backend replays in parallel, zero sequentially |

The mixed study improved settlement timeliness and worsened refusal timeliness. Its median paired change was **+0.0517 timely settlements/s**, with a pair range of +0.0467 to +0.0617/s. Safe stops within two seconds increased from 447 to 539 of 600. All **1,648 deadline misses** remain reported. Refusals and safe stops do not count as payment throughput; paired ranges are not confidence intervals.

## Additional pattern: FX quote selection

The separate [PAY-05 FX quote pattern](pay05-fx-quotes.md) has now passed local and [OCI/PostgreSQL functional qualification](../validation/oci-20261031-pay05-qualification/README.md): 22 scenarios in each of two BPMN models, 34 expected synthetic effects and ten expected no-effect outcomes. Earlier short concurrency-probe failures are retained, and corpus revision 2 uses a longer declared overlap probe in both arms. The subsequent [four-lane offered-load/reset qualification](../validation/oci-20261031-pay05-load-qualification/README.md) also passed: 264/264 correct outcomes, 204 expected effects and four verified resets, with seven deadline misses retained. The subsequent [five-pair delay-profile comparison](../validation/oci-20261031-pay05-comparison/README.md) is complete and independently audited: all 3,960 measured outcomes were correct, and timely settlements rose from 668 to 1,530 of 1,530. The median paired change was +0.2184 timely settlements/s, with a range of +0.2109 to +0.2285/s. All 1,106 measured deadline misses remain retained. Parallel work also produced 910 cached backend replays during measurement, without duplicate payment effects. These are fixed synthetic profiles, separate from the W01/W02/W03 populations and live-agent behavior.

## W02 mixed study: all five dependency profiles completed

The frozen corpus crosses ten policy/fault cases with five delay profiles. Both arms retain two workers, original-input binding and exact-effect checks.

| Profile | KYC / risk delay | Sequential timely settlements | Parallel timely settlements | Correct settlements, both arms |
| --- | --- | ---: | ---: | ---: |
| Fast | 50 / 70 ms | 240/240 | 240/240 | 480/480 |
| Balanced | 400 / 400 ms | 228/240 | 240/240 | 480/480 |
| Risk dominates | 100 / 900 ms | 237/240 | 240/240 | 480/480 |
| KYC dominates | 900 / 100 ms | 175/240 | 180/240 | 480/480 |
| Both slow | 900 / 1,100 ms | 0/240 | 137/240 | 480/480 |

The ten cases cover approval, sanctions refusal, risk refusal, unauthorized action, required review, unknown KYC, unknown risk, retry before a receipt, reconciliation after a receipt and concurrent completion. Equal coverage is a synthetic test design, not a customer frequency distribution. The [full aggregate audit](../validation/oci-20261028-w02-mixed-comparison/summary.json) retains all outcomes, per-pair changes, profiles and telemetry.

## Local event and batch qualification

The separate [FNV-06 pattern](fnv06-events-and-batches.md) passed twelve local case families across 42 workflow instances, with fourteen operations and three completed asynchronous batches. There were no unintended transitions or duplicate end events. The same cases also passed a separate [OCI/PostgreSQL qualification](../validation/oci-20261101-fnv06/README.md), with final durable-state SQL reconciliation and owned cleanup. Burst/fan-in, batch-size and recovery testing remain open. The [small local load-driver qualification](fnv06-load-driver.md) also passed: 81 workflows, 63 intended completions and 18 unchanged controls. The separate [PostgreSQL driver qualification](../validation/oci-20261101-fnv06-load/README.md) passed the same observations with final SQL reconciliation and owned cleanup; the [larger 10/100/1,000-recipient qualification and owned reset rehearsal](../validation/oci-20261101-fnv06-scale/README.md) also passed. The [45-cell comparison design](fnv06-population-scaling.md) is declared; the [repeated cloud attempt](../validation/oci-20261101-fnv06-repeat/README.md) passed three cells and stopped on a large signal timeout, with two batch cells unattempted. The separate [controlled cloud timeout check](../validation/oci-20261101-fnv06-timeout-capture/README.md) verified final REST/SQL capture after four deliberately timed-out requests; all 110 intended outcomes were correct and four controls stayed waiting. The original signal failure remains unchanged. Complete warmup/reset qualification, general failure/deadline accounting, workload telemetry and repeated performance results remain open.

## Evidence and cleanup

All ten mixed arms passed independent audit. The audit checked 527 exported files and original stores, including 7,000 cases with warmup, source/control hashes, reset evidence and exact effects. Browser checks passed six outcome rows, fifteen paired rows and fifty profile rows; the saved report passed visual review.

The owned W01, W02, mixed-study and PAY-05 databases/workloads are retired. Original-reference preservation passed. The original reference remains allocated and billable; full attributed cost per correct outcome is still open.

The [first mixed qualification and interrupted handoff](../validation/oci-20261028-w02-mixed-qualification/README.md) are retained. No paired measurement started before that interruption. A fresh target [requalified independently](../validation/oci-20261028-w02-mixed-requalification/README.md) before the completed series. No failed measured arm was replaced to obtain a favorable result. Earlier [functional sensitivity](w02-dependency-sensitivity.md) and [local mixed qualification](w02-mixed-load.md) remain separate evidence.

## Read the result or reproduce your own

Open the [saved report](results/w02-mixed/report.html) from a local checkout:

```bash
python scripts/open_report.py --path docs/results/w02-mixed/report.html
```

The page shows retained audited executions and starts no payments. Use the [one-page brief](assets/fluxnova-research-brief-20261028.pdf), [getting-started guide](getting-started.md), [benchmark protocol](benchmark-protocol.md) and [portability guide](portability.md) to reuse the work.

Next research requires representative customer inputs and policies, complete cost attribution, then separately controlled runtime/platform/database or agent-versus-rules studies. Current results do not establish customer capacity, live-model advantage, human productivity, cross-cloud parity or a Java/Spring upgrade benefit.
