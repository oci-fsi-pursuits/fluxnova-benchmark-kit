# W02: sequential versus parallel payment checks

Status: **five-pair matched OCI comparison complete and independently audited**. [Results](../validation/oci-20261027-w02-comparison/README.md): median paired observed p95 fell **32.1%**, from median run p95 **1341.8 ms sequential to 910.7 ms parallel**. All **6,000 measured payments** were correct and within two seconds. Both delivered 1/s deadline goodput; this is a latency result at a fixed load. The earlier cloud qualification (retained in the private research archive) separately verifies negative/replay controls. The dependencies are deterministic; this is not a live-agent performance experiment.

## What changed

The control runs KYC screening, then an independent risk check, then verification and synthetic settlement. The candidate forks the two checks and joins before the same verification and settlement step. Both use the same implementation, two worker slots, original payment inputs, dependency schedule and completion observer. Only the BPMN dependency graph changes.

| Control | Candidate |
| --- | --- |
| Payment -> KYC -> risk -> verify -> outcome | Payment -> KYC and risk concurrently -> join -> verify -> outcome |
| Each check has its own receipt and result variable | Each check has its own receipt and result variable |
| Exactly one original effect for an approved case; zero otherwise | Exactly the same effect rule |

The checks are independent **by construction**: risk uses the original amount, limit and market flag, not the KYC result. A real process where risk requires KYC output cannot use this transformation unchanged. These are deterministic research fixtures, not real KYC, a bank ledger or a representative production case distribution. There are no model calls or human reviewer measurements.

## Implemented and exercised

- [BPMN control](../workloads/w02/sequential.bpmn) and [candidate](../workloads/w02/parallel.bpmn), including diagram coordinates.
- [Ten prespecified cases](../workloads/w02/cases.json): approval, sanctions rejection, risk rejection, missing authority, review required, either unknown check, failure before a receipt, ambiguous response after a receipt, and simultaneous branch completion.
- [Independent backend](../services/workflow_checks.py): a separate registration identity binds immutable input to its actual process ID. Workers can request only a registered operation. Settlement checks both bound receipts and original authority; it atomically records one exact synthetic effect and its receipt.
- [Two-slot worker](../services/workflow_checks_worker.py): each branch writes its own result variable. An ambiguous dependency response is reconciled against its receipt before retry. A different process cannot reuse the original case binding.
- [Owned local runner](../scripts/run_w02_functional.py): starts a fresh Fluxnova/H2 process, verifies durable history and independent effects, retains raw observations, checks replay behavior and stops its services.
- [Saved evidence view](../benchlib/workflow_checks_report.py): diagrams, arm selection, recorded outcomes and links to actual evidence. It has no live launch control or invented performance counters. The saved viewers now pass Playwright browser and visual review independently of the desktop automation connection.

The qualification record (retained in the private research archive) records **20/20 functional cases**, eight expected effects, 120 repeat-operation calls that preserved receipts/effects, 20 premature-settlement denials and two blocked cross-process replay probes. Parallel execution overlapped in the designated checks. Unknown cases stopped without effects; review-required cases were refused settlement, without running a human review flow.

This was Windows, Java 21 and embedded H2. Workers started separately for each functional case. These observations are not steady-state latency, sustainable throughput, an OCI result or a measured speedup. The earlier P01/P02 references remain separate experiments.

## Reproduce the functional qualification

Use Python 3.12 or newer, the retained official Fluxnova 3.0.1 ZIP and its unmodified extracted distribution, and a compatible Java 21 executable. The runner compares every loaded JAR with the pinned archive. The archive must be at `.tools/fluxnova-3.0.1.zip` with SHA-256 `ced4311c541faf1a7248d88e37ba53bd8c659fefe8387d2f78c21c30a117d7cf`.

From this repository, using the prepared local runtime:

```powershell
python scripts/run_w02_functional.py --output dist/w02-functional-NEW
```

For another prepared runtime location, supply `--runtime PATH --java PATH`. The Windows defaults point to `.tools/fluxnova-3.0.1` and `.tools/java21/jdk-21.0.12.1+1-jre/bin/java.exe`. These runtimes are ignored, not distributed in Git. The script does not download or install them. It requires a fresh output directory and an unused loopback port, default 28180. It never accepts a remote engine address or uses OCI credentials.

Open `report.html` in that output directory after the command succeeds. The page works with its adjacent JSON files. Retain the complete directory: manifest, archived source and workload inputs, engine history, independent receipts, worker events, database, log and checksums. The generated engine password is redacted after shutdown. Failed attempts are retained separately; do not overwrite or relabel them.

Repository regression checks:

```powershell
python -m unittest discover -s tests
```

## Completed OCI trial and frozen design

The [OCI protocol](w02-cloud-protocol.md) was frozen after qualification at 1 start/second. All five matched pairs now pass independent audit. The sequence below records the completed design; failed preparation and qualification attempts remain retained. Negative cases ran during qualification, while every timed approved payment was independently outcome-checked. Negative cases were not mixed into the measured arms.

1. **Prepare an isolated OCI lane.** Pin Fluxnova/JDK, PostgreSQL, worker and support images, host/container resources, database durability, telemetry and a reproducible state reset. First repeat the functional cases on that actual stack. Qualify the SQLite research fixture and its receipt access at the intended load; any backend replacement is held identical in both arms. Update the cost reservation and resource inventory before cloud execution.
2. **Qualify the implemented steady-state driver on OCI.** The [dedicated k6 driver and independent audit](w02-load-driver.md) now pass the local qualification. Two worker slots remain running throughout each arm, with no post-success sleep. Original inputs, lifecycle timestamps and exact effects are checked independently, with 100 ms completion polling. The functional runner's per-case worker startup is unsuitable for this trial; use the new load driver.
3. **Calibrate without selecting a winner.** For the first synthetic latency study, use approved inputs with fixed 400 ms KYC and 600 ms risk waits. Test candidate offered rates of 0.5 and 1 start/second and select a rate both arms and the generator can sustain with stable backlog and no dropped starts. Those waits consume about one worker-second per payment before overhead; two slots therefore do not justify copying P01's 10/s rate. Calibration observations stay separate from measured results.
4. **Freeze the controls and order.** The accepted design uses five matched pairs, randomized arm order recorded before measurement, 120 seconds warmup, 600 seconds measurement and 30 seconds drain per arm, with a two-second outcome deadline. Reset from the same qualified state before every arm and use unique run identities. The complete negative corpus is qualified before the series; repeated negative-case stress within each measured arm is outside this cohort.
5. **Run all declared pairs and show the outcome.** Report absolute p95/p99 and their per-pair differences, correct on-time settlements per second, the fraction of all offered starts meeting the deadline, backlog, branch overlap, worker/engine/DB resource use, failures, unknowns and duplicate or unauthorized effects. Treat an unknown blocked case as a safe stop, not a successful settled payment. Retain failed arms and follow a declared interruption rule; do not stop at a favorable percentage.

The assumed 400/600 ms waits illustrate a 1,000 ms sequential dependency path versus a 600 ms ideal parallel path before overhead. That arithmetic does not establish a measured 40% improvement. Results could be smaller, absent or worse under contention. A later representative workload needs a sourced delay distribution, case mix and burst trace; it should be a separate declared cohort.

## Presentation and demo use

The later [dependency sensitivity qualification](w02-dependency-sensitivity.md) passed 100 local functional executions across five delay profiles. It extends behavior coverage without changing the original OCI latency result or establishing a representative customer distribution.

The [portable saved-evidence demo](w02-saved-demo.md) shows the two workflow graphs, an approval with one exact effect, an unknown check with zero effects, and replay evidence. Its bundle has verified records and a 60–90 second talk track; automated browser review and a 75-second recording now pass. Label this a saved functional demonstration. It is separate from the earlier wireframe and the existing Tasklist approval demo (retained in the private research archive). The 27 October talk now includes the audited 32.1% result chart and the unchanged 6,000/6,000 correct-on-time outcome count. The saved functional timelines are separate executions and must not be used as the source of that performance percentage.

Source and measurement rules: experiment register (retained in the private research archive), comparison examples (retained in the private research archive), [measurement contract](measurement-contract.md).
