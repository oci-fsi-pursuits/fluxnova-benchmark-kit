# FNV-06: how does delivery time grow with the number of waiting workflows?

This study changes the intended recipient population from **10 to 100 to 1,000**, while keeping the engine, database and client limits fixed. It measures when the entire intended group finishes correctly. It analyzes three delivery patterns separately because they have different routing semantics.

**All 45 planned cells completed with independently verified outcomes and resource evidence; 2 cells did not pass the predeclared generator headroom gate. Affected timing comparisons remain flagged.** The original matrix has five repeats of each pattern/population combination. The [complete aggregate](summary.json) reports all 45 cells, including failures, constraints and unattempted entries.

## What was held fixed

- Fluxnova 3.0.1, pinned artifacts, one 2-vCPU/4-GiB engine container, and one 1-vCPU/512-MiB generator/observer container on the existing OCI controller.
- A separate single-node OCI PostgreSQL database with 2 OCPUs and 32 GB RAM. No autoscaling and no change to the reference deployment.
- Four message clients; one signal or batch request per burst. No model calls or payment backend effects.
- A fresh owned schema, the same 100-recipient warmup for that pattern, two warmup controls and two measured controls in every cell. Warmup workflow state was independently matched; physical cache and JIT state were not claimed to be identical.
- A 100 ms completion-observation interval, 30-second HTTP timeout and the declared drain/bookkeeping limits. Pattern and population order were fixed before execution.

The [frozen design](../../workloads/fnv06/population-scaling-design.json) and [execution contract](../../workloads/fnv06/population-scaling-execution.json) identify the population and controls. The design file preserves its historical pre-execution status; the separate execution contract activated the campaign.

## Measured outcomes and cohort completion

| Pattern | Recipients per burst | Audited repeats | Headroom-qualified repeats | Verified / planned recipients | Correct by two seconds, bounded count | Qualified median cohort-completion interval, seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Keyed messages | 10 | 5/5 | 5/5 | 50/50 | 50 | 0.000–0.103 |
| Keyed messages | 100 | 5/5 | 5/5 | 500/500 | 500 | 0.751–0.872 |
| Keyed messages | 1,000 | 5/5 | 5/5 | 5,000/5,000 | 1,176–1,244 | 8.161–8.284 |
| Broadcast signal | 10 | 5/5 | 5/5 | 50/50 | 50 | 0.079–0.199 |
| Broadcast signal | 100 | 5/5 | 5/5 | 500/500 | 500 | 1.191–1.309 |
| Broadcast signal | 1,000 | 5/5 | 5/5 | 5,000/5,000 | 0 | 11.485–11.604 |
| Asynchronous batch | 10 | 5/5 | 5/5 | 50/50 | 50 | 0.075–0.202 |
| Asynchronous batch | 100 | 5/5 | 5/5 | 500/500 | 500 | 0.453–0.612 |
| Asynchronous batch | 1,000 | 5/5 | 3/5 | 5,000/5,000 | 2,319–2,493 | Not qualified |

The intervals preserve the observer's polling uncertainty and are rounded outward. The median interval bounds the median of five cohort observations; it is not a confidence interval or a per-workflow p95/p99. The two-second threshold is synthetic, not a customer service commitment. Counts in incomplete groups refer only to independently verified work, with the full planned population retained alongside it.

Across the audited cells, **16,650 measured recipients** and **4,500 warmup recipients** completed correctly; **180 controls** stayed unchanged. Measured requests: 5,580, with 0 failures retained. Warmup requests: 1,530, with 0 failures retained. These counts describe test correctness, not payment throughput.

The measured observations confirm at least **11,263 recipients finished after two seconds**; **242** have deadline uncertainty from polling. A correct final outcome does not erase a missed deadline or a failed HTTP request.

## Population comparisons within each pattern

Each larger population is paired with the 10-recipient control from the same pattern and repeat. The number below is the larger cohort's completion time divided by its paired control's completion time. It describes population scaling; it is not a comparison between message, signal and batch semantics.

| Pattern | Population comparison | Resource-qualified pairs | Median paired completion-time ratio, interval |
| --- | ---: | ---: | ---: |
| Keyed messages | 100 vs 10 | 5/5 | Not available |
| Keyed messages | 1,000 vs 10 | 5/5 | Not available |
| Broadcast signal | 100 vs 10 | 5/5 | 5.95–16.55 |
| Broadcast signal | 1,000 vs 10 | 5/5 | 57.85–146.63 |
| Asynchronous batch | 100 vs 10 | 5/5 | 2.26–8.14 |
| Asynchronous batch | 1,000 vs 10 | 3/5 | Not available |

The aggregate retains every individual pair and all five original completion intervals per group. It also retains a descriptive median over all five timings when available, including any constrained cell; that descriptive value does not override the resource gate. Five repeats do not establish a reliable tail percentile, sustainable arrival-rate capacity, or a production guarantee.

## Three clocks that must stay separate

1. **HTTP acknowledgment:** when a request returns to its caller.
2. **Recipient completion:** when all intended workflows have reached their verified final states. This is the primary timing result.
3. **Batch bookkeeping:** when the asynchronous batch's own terminal record is observed. It can occur later than recipient completion and is retained separately.

## Evidence, resources and cleanup

Every audited cell retained original native-k6 requests, original observer polls, counted REST inventories and a read-only nine-table SQL reconciliation before cleanup. Resource samples were bound to the original work clocks and owned processes. 2 audited cells did not pass the generator/observer headroom gate; the affected population comparisons are not qualified. The generator includes the Python observer. Database activity/counters include observer overhead and do not establish database-host CPU or memory headroom.


### Retained headroom flags

| Cell | Pattern / population | Window | Failed check | Maximum sampled host CPU | Generator CPU throttling, microseconds |
| --- | --- | --- | --- | ---: | ---: |
| C019 | Asynchronous batch / 1,000 | test | sampled_host_cpu_headroom_low | 82.5% | 0 |
| C028 | Asynchronous batch / 1,000 | warmup | sampled_host_cpu_headroom_low | 80.0% | 0 |

The frozen host CPU check requires samples below 80%; reaching 80% fails that gate. Host CPU covers the shared machine, while the separate throttling counter covers the generator/observer container. A host CPU flag does not establish that the generator itself was saturated. Both warmup and measurement must pass for a cell to qualify. The original timings remain retained, and no threshold was relaxed or cell replaced.


The temporary database, owned containers and all campaign Bastion sessions are closed. Reference services, queues and 48 non-metering tables were preserved; routine meter rows were verified as append-only. Both repositories remain private. Raw logs, connection identifiers and SQL rows remain in the private archive; the public-ready aggregate retains source, seal, audit and archive hashes.

The measured source was `c1ca2b645c5fc87c6f59d8207109c0dcd59ce5a7`. Its 584 local tests passed with native k6 and zero skips; matching Windows/Linux CI and secret scans passed before allocation. This is separate from earlier qualification attempts, including the retained uncontrolled signal timeout and deliberate lock/timeout tests. Their failures were not relabeled or replaced.

See the [reproduction and analysis guide](../../docs/fnv06-population-scaling.md). The remaining original research catalogue includes cluster contention and operator/authoring journeys; this finite-population study does not complete those questions.
