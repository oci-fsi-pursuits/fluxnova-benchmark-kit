# Benchmarking Fluxnova for Financial and Agentic Payment Workflows

Eight main slides, **12 minutes 45 seconds planned plus 2 minutes 15 seconds reserve**. The nine diagram reference slides follow the closing slide and are hidden from the main slideshow. The supplied OSFF New York 2026 template remains the visual system.

**Thesis:** Measure how workflow and runtime changes affect time to a correct, authorized outcome.

The end user is a workflow developer or platform engineer asking whether a change helps their application. A pattern is what runs. A benchmark type is how we test it. A study is one frozen experiment. Agentic payments are a workload category; deterministic workflow timing does not measure live-agent benefit.

| Slide | Topic | Time | Say |
| --- | --- | ---: | --- |
| 1 | Title and thesis | 0:45 | “A workflow can look right and still be slow, send too much work to review, or produce the wrong effect. We measure time to the outcome we actually wanted.” |
| 2 | How the benchmark works | 1:30 | “Choose a BPMN flow and known inputs. Start repeatable work. Run the real engine and workers. Independently check the final state and payment effect. Change one declared factor and repeat.” |
| 3 | What we compared | 1:30 | “These experiments ask different questions. Pacing and quote concurrency test deadlines. Parallel checks test latency. Review routing counts tasks. Runtime and release comparisons test a fixed async reference.” |
| 4 | Independent checks | 2:00 | “KYC and risk are independent here. Running both together reduced observed p95 by a median paired 32.1% at 1/s. Both still had to pass before payment. All 6,000 outcomes were correct and on time.” |
| 5 | Parallel FX quotes | 2:00 | “Three independent quote waits overlap. Timely settlements rose from 668 to 1,530 out of 1,530 expected settlements per arm. All outcomes were correct. Parallel completion also caused 910 cached replays, so durable retry handling matters.” |
| 6 | Runtime and release | 1:30 | “The small latency differences changed sign. Both comparisons met the same fixed target. We did not establish a broad speedup from Java 25 or Fluxnova 3.1.1. A useful benchmark can tell you a change made little difference.” |
| 7 | Saved workflow demo | 1:30 | “This is a saved real functional execution. Watch the same checks run sequentially or overlap. Then see unauthorized and unknown inputs stop without a payment effect. These individual times are separate from the cloud statistics.” |
| 8 | Summary and source QR | 2:00 | “Reuse the pattern and its outcome checks. Establish your own baseline. Change one thing and retain the result, including failures. OCI is our measured reference; other deployment adapters still need validation.” |
| — | Questions / transitions | 2:15 | Keep the closing QR visible. |

## Demo and fallback

Use the existing reviewed 75-second recording or the portable W02 viewer. The [demo guide](w02-saved-demo.md) gives the sequence: approved sequential, approved parallel, unauthorized, unknown KYC. The viewer loads saved evidence and never initiates a payment. A fresh functional run can rebuild it using the [supported commands](benchmark-standard.md).

On the presentation laptop, open the local presenter pack before starting PowerPoint. Keep the video page open in a browser, switch to it on slide seven and return to the closing slide. If playback fails, use the saved viewer or the screenshot already on the slide. Check the venue screen once connected.

## Reference slides and questions

The appendix explains each of the nine existing diagrams: W01 pacing, W02 parallel checks, W03 review routing, minimal start/end, variable persistence, external work, KYC/review, risk/freshness and settlement/replay. Each has its own meaning and evidence scope. The [full catalogue](benchmark-guide.md) also covers soak, capacity, population scaling, job faults, agent boundaries, recovery and observer diagnostics.

“Is it realistic?” These are intentionally controlled synthetic mechanisms. They are useful comparisons within their declared inputs; a customer must supply representative traffic and dependencies before using them for sizing.

“Did parallel always win?” No. The mixed W02 study improved timely settlements but reduced timely refusals. PAY05 increased cached retries. Keep both tradeoffs in the discussion.

“Is everything finished?” The bounded studies are usable now. Higher capacity, resource-qualified replica scaling, richer live-agent studies, platform/database alternatives, customer traffic and complete cost per correct outcome remain separate research.

“Is this an official standard?” Protocol 0.2 is a community proposal with a checked catalogue, consistent result fields and documented run paths. It is not an adopted FINOS standard or a product certification.

QR destination: [clean-history benchmark kit](https://github.com/oci-fsi-pursuits/fluxnova-benchmark-kit). The repository must be made accessible to the intended audience before the public talk. A QR image alone does not prove anonymous access. Source and reproduction materials credit **Nicholas Cusato** for research/OCI implementation and **FINOS contributors** for Fluxnova software.
