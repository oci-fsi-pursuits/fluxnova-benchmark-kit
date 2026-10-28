# W02: parallel payment checks reduce observed latency

**Five matched pairs completed. Parallel checks lowered observed p95 by 32.1% at the median paired change.** Every pair showed a reduction, ranging from 31.8% to 32.2%. Across both workflows, all 6,000 measured payments completed correctly within the two-second test deadline.

This is a controlled synthetic workflow result on OCI: independent 400 ms KYC and 600 ms risk dependencies, two worker slots and one offered start per second. It demonstrates a lower time to a correct, authorized outcome for this dependency pattern. It does not establish production KYC latency, live-model performance, maximum capacity or a cloud-vendor advantage.

## What changed and what stayed fixed

Sequential KYC then risk is the control. The candidate forks the independent checks and joins both receipts before the same verification and synthetic settlement. Both arms use identical original approved-payment inputs, worker implementation, two slots, fixed resources, images, database durability, checkpoint and 100 ms completion polling. Only the BPMN ordering changes.

The frozen design includes five randomized matched pairs, 120 seconds warmup, 600 seconds measurement and 30 seconds drain per arm. Each arm received 600 measured and 120 warmup starts. All ten arms are valid. There were no measured drops, unknown outcomes, wrong effects or deadline misses. Warmup is excluded from all comparison denominators.

The test used existing controller compute for the engine, generator, backend and workers, plus a new single-node OCI PostgreSQL system with 2 OCPUs and 32 GB memory. The engine had two logical CPUs, a 4 GiB container limit, a 2 GiB heap and pool size 10. Java instrumentation was disabled in both arms. This differs from the original four-host P01 topology, so do not compare their absolute latency figures.

## Results

| Metric | Sequential | Parallel |
| --- | ---: | ---: |
| Median of five run p95 values | 1341.8 ms | 910.7 ms |
| Run p95 range | 1339.4–1342.1 ms | 909.9–915.5 ms |
| Median of five run p99 values | 1345.1 ms | 1012.2 ms |
| Correct measured outcomes / offered | 3,000 / 3,000 | 3,000 / 3,000 |
| Correct measured outcomes within 2 seconds | 3,000 | 3,000 |
| Correct on-time outcomes per second | 1.000 | 1.000 |
| Timed cases with overlapping KYC and risk receipts | 0 / 3,000 | 3,000 / 3,000 |

The median paired p95 reduction is about 430 ms. Percentages are computed within each pair before taking their median. The percentile columns summarize run percentiles, not pooled request percentiles. The range across five pairs is descriptive, not a confidence interval or a statistical significance claim.

| Pair | Sequential p95, ms | Parallel p95, ms | Parallel change |
| --- | ---: | ---: | ---: |
| 1 | 1342.1 | 915.5 | -31.79% |
| 2 | 1341.8 | 910.2 | -32.17% |
| 3 | 1342.0 | 912.3 | -32.02% |
| 4 | 1339.4 | 909.9 | -32.07% |
| 5 | 1340.6 | 910.7 | -32.07% |

Completion latency includes the 100 ms polling observer. Both arms sustained the same offered rate, so deadline goodput remained 1/s. The result supports lower observed latency at that rate; it does not demonstrate a throughput increase.

## Correctness, resources and retained evidence

The independent offline audit reproduced the frozen analyzer's results and checked all ten SQLite stores. All **7,200 unique executions**, including 1,200 warmup cases, match the original payment, exact effect and all three receipts: **21,600 receipts** in total. All stores pass integrity checks. Every arm restored the same checkpoint and ended with zero active instances, external tasks and incidents.

All **725 exported file hashes** verify before and after the store audit. The credential-excluded archive, exact source, input plans, raw event streams, SQL/Docker/Linux telemetry and earlier setup/qualification failures remain retained. The prepared functional qualification separately passed the 20-case negative/replay corpus; those cases are not included in the timed approved-payment denominator.

Across the ten arms, host CPU p95 ranged 7.3–8.8% and measured arrival-lag p99 ranged 1.5–2.3 ms. SQL samples recorded no lock waiters or increase in the deadlock counter. CPU and SQL samples include warmup and use the shared controller's Linux/Docker/SQL view; arrival-lag values cover the measured cohort. Managed-database host CPU and full distributed tracing were not collected. Sampled headroom does not establish maximum sustainable load.

The first additional store-audit attempt used the wrong event-status literal, expecting `success` where the frozen format records `correct`. The audit assertion was corrected and the full audit passed against unchanged archived evidence. This was an analysis-harness error, not a rerun or exclusion of a measured arm.

Executed source: `de00889d6d78ada55eea63427b1b0d7b475831c7`. Frozen plan SHA-256: `7ec9f0a0e3414d945623378dd7549b794b26f81a0f1ae10c27c6bf0d81b7952a`. Archive SHA-256: `59c58c769792af9ccb064ade9baaef88be45b8be537d8d22a57ae44c39017b6e`. Machine-readable results and audit details: [summary.json](summary.json).

## Cleanup and use in the talk

The campaign finished at 19:40 UTC on 27 October. The owned workload containers, Docker network and temporary access rule were removed. OCI reported the temporary database **DELETED** at 19:42 UTC. Temporary idle-sleep prevention was released. Existing reference resources remain allocated and billable.

Use this sentence: **“In five matched synthetic payment pairs, running independent KYC and risk checks in parallel reduced observed p95 by 32.1%, while all 6,000 measured payments remained correct and within two seconds.”**

This is the measured W02 comparison for the 15-minute talk. The [saved functional demo](../../docs/w02-saved-demo.md) explains the workflow and correctness controls. A representative payment mix, sourced dependency distributions, burst load, broader capacity, live-agent benefit and complete cost per correct outcome remain future research.

Protocol: [frozen study](../../docs/w02-cloud-protocol.md). Qualification: cloud checks (retained in the private research archive). Thesis and other cohorts: experiment register (retained in the private research archive).

Research and OCI implementation: **Nicholas Cusato**. Fluxnova software: **FINOS contributors**.
