# Benchmarking Fluxnova for Financial and Agentic Payment Workflows

**13 main slides, 13 minutes planned and 2 minutes reserve.** The 14 appendix slides are hidden from the main PowerPoint slideshow. The [final presentation](presentation.md) uses the supplied OSFF New York 2026 template.

**Thesis:** Use repeatable tests to find out whether a workflow or runtime change improves performance while preserving correct, authorized outcomes.

The intended user is a workflow developer or platform engineer. A **pattern** is what runs. A **benchmark type** is the question we test. A **study** is one fixed experiment with declared inputs, controls and limits. Agentic payments are a workload category; deterministic workflow timing does not measure live-agent benefit.

| Slide | Topic | Time | Suggested talk track |
| --- | --- | ---: | --- |
| 1 | Title | 0:15 | “How do we know that changing a workflow actually helps? That is the question behind this project.” |
| 2 | Introduction | 0:30 | “I am Nicholas Cusato, a Senior Cloud Engineer at Oracle Cloud Infrastructure, aligned with Financial Services and Insurance. Much of my work involves benchmarks: helping customers answer price and performance questions with evidence.” |
| 3 | Customer problem | 1:15 | “A customer moving from E-Business Suite to Fusion asked about using AI with existing process automation. Before choosing a technology, I would ask: what should we keep, where could AI help, and how will we know it works better? Those questions also apply when we change a workflow without moving clouds.” |
| 4 | Quote | 0:15 | “Speed is only part of the answer. A fast agent that duplicates a payment has failed. We need to check the outcome as well as the stopwatch.” |
| 5 | Thesis | 0:45 | “Establish a baseline, change one factor, and check both performance and correctness. Fluxnova's portability makes it a useful place to develop a repeatable method. Each deployment still needs its own validation.” |
| 6 | Architecture | 1:30 | “BPMN describes the flow. Workers perform the external tasks. The load runner supplies repeatable cases. We observe durable completion and independently verify decisions and synthetic payment effects. The benchmark is the workload plus the controls, clock and outcome checks.” |
| 7 | Research map | 0:45 | “This is the whole research map: workflow design, runtime, reference load, coordination, agent controls and measurement. The smaller diagrams are entry points into the repository. These are 17 study records, not 17 completed performance comparisons.” |
| 8 | Three examples | 1:00 | “First, remove an unnecessary worker pause. Second, overlap independent checks while still waiting for both. Third, send only policy-required cases to review. These change different things, so each needs its own baseline and scorecard.” |
| 9 | Outcomes | 2:00 | “Parallel W02 checks reduced observed p95 by a median paired 32.1% at one start per second. But mixed traffic improved timely settlements and worsened timely refusals. Scripted review routing kept all required reviews. Runtime upgrades showed small, mixed differences. The eight-hour run completed its fixed load; maximum capacity remains unqualified. Event, recovery and agent tests have their own bounded findings.” |
| 10 | Standardization | 1:30 | “Think of a recipe and a scorecard. Name the baseline and change, keep the other conditions steady, define correct outcomes, then publish the result with its environment and limits. Another team should be able to rerun it and understand a different result. Protocol 0.2 is our community proposal, not an adopted FINOS standard.” |
| 11 | Future work | 0:45 | “Next: representative customer workloads and live-agent studies, qualified capacity and replica scaling, other deployment and database choices, and complete cost per correct outcome. The current evidence is a starting point for those questions.” |
| 12 | Get started and demo | 1:45 | “Start with the illustrated guide and a saved example. This 75-second recording shows real functional executions: sequential checks, parallel checks, then blocked payment cases. It is a saved workflow walkthrough, not an installation recording or the cloud p95 result. The repository separates the offline demo, real engine testing and OCI Terraform.” |
| 13 | Conclusion and feedback | 0:45 | “Take one workflow, declare one change and verify the outcome. Use the kit, reproduce a study, or suggest the next workload through this QR code. We want a method that another team can challenge and improve.” |
| — | Questions and transitions | 2:00 | Keep the feedback QR visible. |

## The customer story

The E-Business Suite to Fusion conversation motivates the research. It is **not a validated migration**, a measured AI improvement, or evidence that this customer's automations run on Fluxnova. No process count or customer identity is included. Start with the application's native capabilities, then assess whether a separate cross-system orchestration engine fits the requirement. The stock photograph is illustrative.

## Demo and fallback

Play the reviewed 75-second recording on slide 12, or open the portable W02 viewer. The [demo guide](w02-saved-demo.md) explains the sequence: approved sequential, approved parallel, unauthorized, unknown KYC. The viewer reads saved evidence and initiates no payment. A fresh functional run can rebuild it using the [supported commands](benchmark-standard.md).

Before the talk, open the local presenter pack and recording page. Switch from PowerPoint to the browser on slide 12, then return to slide 13. If playback fails, use the saved viewer or the screenshot on the slide. Check the venue screen once connected. The installation and Terraform guides are linked from [getting started](getting-started.md); do not describe the walkthrough as an installation demo.

## Appendix and likely questions

Slides 14–22 explain the nine pattern diagrams. Slides 23–25 retain the W02, PAY-05 and runtime/release details. Slides 26–27 show the original OCI reference deployment and synthetic payment authority path. Later studies declare their own environments; the original four-host diagram does not describe every experiment.

**“Is it realistic?”** These are controlled synthetic mechanisms. They support comparisons within their declared inputs. Customer sizing needs representative traffic, dependencies and operating conditions.

**“Did parallel always win?”** No. Mixed W02 improved timely settlements but reduced timely refusals. PAY-05 increased cached backend replays without duplicate effects. Keep the tradeoffs visible.

**“Does OCI make it faster?”** These studies used OCI as a measured reference. They do not compare cloud providers or establish OCI price/performance superiority. The application core is portable; provisioning and integrations need replacement and validation elsewhere.

**“Is everything finished?”** The bounded studies are usable now. Higher capacity, resource-qualified replica scaling, live-agent benefit, alternative platforms and databases, customer traffic and complete cost attribution remain open.

**“Is this an official standard?”** No. Protocol 0.2 is a community proposal with a checked catalogue, consistent result fields and documented run paths. It is not an adopted FINOS standard or product certification.

The source QR points to the [clean-history benchmark kit](https://github.com/oci-fsi-pursuits/fluxnova-benchmark-kit). The closing QR opens its [feedback form](https://github.com/oci-fsi-pursuits/fluxnova-benchmark-kit/issues/new?template=benchmark-feedback.yml). Audience access must be checked after the owner changes visibility. Public feedback must not contain credentials, customer information or private logs.

Research, diagrams and OCI implementation: **Nicholas Cusato**. Fluxnova software: **FINOS contributors**. See [presentation credits](presentation.md#credits).
