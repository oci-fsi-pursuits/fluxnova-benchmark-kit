# Benchmarking Fluxnova for Financial and Agentic Payment Workflows

Nicholas Cusato | Research and OCI benchmark implementation | 28 October 2026 UTC

Measure how quickly and reliably financial and agentic workflows reach correct, authorized outcomes.

Fluxnova is the FINOS open-source Java engine for BPMN workflows and DMN decisions.[1] This project adds repeatable workloads, correctness checks and retained evidence so teams can compare a declared change before adopting it. Models can propose actions; workflow policy, review and backend verification control the synthetic payment effect.

## Completed results

| Comparison | Result | Interpretation |
| --- | --- | --- |
| W01 Worker pacing | Payments within two seconds: 600/1,200 to 1,200/1,200. All 2,400 payments correct.[2] | Five matched OCI burst pairs; reconstructed 100 ms success delay versus idle-only backoff. |
| W02 Parallel checks | 32.1% lower observed p95 at the median paired change. All 6,000 payments correct and on time.[3] | Five matched OCI pairs at 1 start/s. Deadline goodput stayed 1/s; higher capacity was not established. |
| W03 Review routing | Review tasks 10 to 4; all four mandatory reviews retained. 20/20 expected outcomes.[4] | Local synthetic functional comparison with scripted reviews. No measured human time saving. |

Mixed-outcome follow-up. Five OCI pairs, sequential to parallel at 1/s: timely settlements rose from 880 to 1,037 of 1,200; timely refusals fell from 780 to 669 of 1,200. All 6,000 outcomes were correct across five dependency profiles. Median paired settlement change: +0.0517/s. All 1,648 deadline misses are retained. [5]

These are separate synthetic studies. Live-model and human-review time, customer traffic, maximum capacity and complete cost per outcome remain outside these findings.

## OCI value

Compute-led adoption. The reference uses four fixed VMs and PostgreSQL, without autoscaling. Flexible CPU and memory sizing supports explicit resource budgets; infrastructure consumption is the commercial opportunity.[6]

Portable application core. Open source, BPMN, containers and PostgreSQL reduce workflow-vendor dependence. OCI provisioning, IAM, Vault and storage integrations need replacement and validation on another cloud or on premises.

Controlled AI integration. OCI offers IAM, Bastion and private model endpoints. Private model connectivity and regulated production readiness were not validated by these tests.[7]

## Adoption

Clone the source. Try python -m benchlib.cli demo for an offline report. Reuse workloads and outcome checks, pin artifacts, record the environment and measure one change. The demo generates sample data; it does not run Fluxnova.

Next: validate a customer-specific workload and complete cost attribution. Reuse the Apache 2.0 kit and contribute another controlled comparison. The benchmark protocol is a community proposal, not an adopted FINOS standard.

## Credit and sources

Nicholas Cusato, Performance-Testing POV for FINOS Fluxnova on OCI, v1.1 (2026), and the benchmark implementation. Results through 28 October 2026 UTC.

Fluxnova software and original logo: FINOS community. Research and OCI implementation: Nicholas Cusato. Code: Apache 2.0; logos retain their terms. No OCI price or performance superiority is claimed.

1. [FINOS Fluxnova](https://fluxnova.finos.org/engine)
2. [W01 evidence](https://github.com/oci-fsi-pursuits/fluxnova-benchmark-kit/blob/ffe7f29317fe357d8720e0f268b3c4e1fb5ba9e2/validation/oci-20261027-w01-comparison/README.md)
3. [W02 evidence](https://github.com/oci-fsi-pursuits/fluxnova-benchmark-kit/blob/ffe7f29317fe357d8720e0f268b3c4e1fb5ba9e2/validation/oci-20261027-w02-comparison/README.md)
4. [W03 evidence](https://github.com/oci-fsi-pursuits/fluxnova-benchmark-kit/blob/ffe7f29317fe357d8720e0f268b3c4e1fb5ba9e2/validation/local-20261027-w03/README.md)
5. [Mixed study evidence](https://github.com/oci-fsi-pursuits/fluxnova-benchmark-kit/blob/main/validation/oci-20261028-w02-mixed-comparison/README.md)
6. [OCI Compute](https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm)
7. [Private model access](https://docs.oracle.com/en-us/iaas/Content/generative-ai/private-endpoint.htm)
