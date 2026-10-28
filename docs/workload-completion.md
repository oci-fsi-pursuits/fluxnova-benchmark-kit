# Completed workflow studies and next research

The thesis is **time to a correct, authorized payment outcome**. The current three-study workload set is complete. Each finding has its own control, input population and limit.

| Study | Comparison | Result | Boundary |
| --- | --- | --- | --- |
| W01: worker pacing | Reconstructed 100 ms success delay versus current idle-only backoff | 600/1,200 control and 1,200/1,200 candidate payments correct within two seconds; median paired deadline-goodput change +1.000/s. All 2,400 measured payments correct | Five matched synthetic burst pairs, not maximum capacity |
| W02: independent checks | Sequential versus parallel KYC/risk, same inputs and two worker slots | 32.1% lower median paired observed p95; 6,000/6,000 measured payments correct and on time at 1/s | Fixed synthetic 400/600 ms dependencies; deadline goodput unchanged |
| W03: review routing | Explicit all-review control versus fixed-policy routing | Review tasks 10 to 4; all four mandatory reviews preserved; 20/20 expected outcomes | Constructed ten-case corpus; scripted review, no human time-saving claim |

Evidence: [W01](../validation/oci-20261027-w01-comparison/README.md), [W02](../validation/oci-20261027-w02-comparison/README.md), [W03](../validation/local-20261027-w03/README.md).

All three saved viewers passed browser automation and visual review. W02 also has a reviewed 75-second recording. The offline presenter package (retained in the private research archive) combines the talk, handout, viewers and recording in one folder. See [commands and scope](browser-automation.md). These pages show retained executions; they start no new payments. A live Tasklist rehearsal and the actual presentation display check are separate.

The temporary W01/W02 databases and owned workloads are retired. The original reference remains allocated. The cost record is a planning reservation, not complete actual billing.

The 15-minute talk (retained in the private research archive) and team brief (retained in the private research archive) now include these audited results, preserving the original title and thesis. The architecture stays honest about the four-host reference, colocated W01/W02 trials and local W03 study.

Next research, in order: qualify a representative customer input mix and policy; measure one declared change with full outcome and resource accounting; finish cost per correct outcome; then run separately controlled runtime/platform/database or agent-versus-rules studies. Do not relabel the current synthetic set as production or live-agent capacity. Historical failures and the original narrow tuning studies remain retained.

The first follow-up qualification is complete: [W02-S1 dependency sensitivity](w02-dependency-sensitivity.md) passed 100 executions across five synthetic delay profiles, with 40 exact effects and 600 safe repeated calls. This tests more conditions while the customer scenario/mix remains to be sourced. It is not an additional performance percentage or an expansion of the 15-minute talk.

The next local gate also passes: [W02-M1 mixed steady-state load](w02-mixed-load.md) verified 300 measured outcomes across four runs, separating 120 settlements, 120 refusals and 60 safe stops. It retains 58 deadline misses, with zero drops or incorrect outcomes. The audit verifies 360 original registrations and 1,080 receipts including warmup; all four engines are stopped and the saved report passes browser and visual checks.

[W02-M2's first cloud qualification](../validation/oci-20261028-w02-mixed-qualification/README.md) passed all four runs and independent audit: 300 correct measured outcomes and 54 deadline misses. A workstation status connection reset interrupted continuation before any paired measurement. That qualification and interruption remain retained; cleanup and original-reference preservation passed.

The [fresh qualification](../validation/oci-20261028-w02-mixed-requalification/README.md) also passed: 300 correct measured outcomes, 69 reportable deadline misses and no drops or incorrect outcomes. Five matched pairs are running from frozen source `2eb03249e65018e226efd21e3e3f50ab24f66ed6`. The planned result requires all ten arms, independent audit, original-reference preservation and owned cleanup. Qualification alone establishes no comparative benefit. The $210.30 planning estimate covers this attempt and the original reference through 28 October 12:00 UTC under the authorized $300 ceiling; it is not complete billing.

## Pattern coverage for the current release

W01, approved-only W02 and local scripted W03 are complete in their stated scopes. The remaining measured pattern is W02-M2. Its fixed corpus crosses ten policy/fault cases with these five dependency profiles:

| Profile | KYC delay | Risk delay | Local functional gate | Mixed OCI paired result |
| --- | --- | --- | --- | --- |
| Fast | 50 ms | 70 ms | Passed | Pending final audit |
| Balanced | 400 ms | 400 ms | Passed | Pending final audit |
| Risk dominates | 100 ms | 900 ms | Passed | Pending final audit |
| KYC dominates | 900 ms | 100 ms | Passed | Pending final audit |
| Both slow | 900 ms | 1,100 ms | Passed | Pending final audit |

The ten cases cover approval, sanctions refusal, risk refusal, unauthorized action, required review, unknown KYC, unknown risk, retry before a receipt, reconciliation after a receipt and concurrent completion. Both BPMNs use the same original inputs and two worker slots. Every profile must retain correct effects, refusals, safe stops, replay checks and deadline misses. Equal case coverage is a synthetic test design, not a customer frequency distribution.

Completing this release does not establish representative customer traffic, live-model advantage, human productivity, cross-cloud parity, maximum capacity or a Java/Spring upgrade benefit. Those are separately designed future studies. Full cost per correct outcome also remains open until billing attribution is complete.
