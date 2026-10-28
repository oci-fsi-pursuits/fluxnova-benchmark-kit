# Benchmarking Fluxnova for Financial and Agentic Payment Workflows

Nicholas Cusato | Research and OCI benchmark implementation | 28 October 2026 UTC

Measure how quickly and reliably financial and agentic workflows reach correct, authorized outcomes.

Fluxnova is the FINOS open-source Java engine for BPMN workflows and DMN decisions.[1] This project adds repeatable workloads, correctness checks and retained evidence so teams can compare a declared change before adopting it. Models can propose actions; workflow policy, review and backend verification control the synthetic payment effect.

## Completed results

| Comparison | Result | Interpretation |
| --- | --- | --- |
| W01 Worker pacing | Payments within two seconds: 600/1,200 to 1,200/1,200. All 2,400 payments correct.[2] | Five matched OCI burst pairs. Removing the success delay improved timely completion in this workload. |
| W02 Parallel checks | 32.1% lower observed p95 at the median paired change. All 6,000 payments correct and on time.[3] | Five matched OCI pairs at 1 start/s. Deadline goodput stayed 1/s; higher capacity was not established. |
| W03 Review routing | Review tasks 10 to 4; all four mandatory reviews retained. 20/20 expected outcomes.[4] | Local synthetic functional comparison with scripted reviews. No measured human time saving. |

New research. Mixed traffic now covers approvals, refusals, unknown checks and one-time faults across five dependency profiles. The latest OCI qualification verified 300/300 measured outcomes, retaining 69 deadline misses. The five-pair mixed comparison is running; its effect is not yet established.[5]

These are separate synthetic studies. Live-model and human-review time, customer traffic, maximum capacity and complete cost per outcome remain outside these findings.

## OCI value

Compute-led adoption. The reference uses four fixed VMs and PostgreSQL, without autoscaling. Flexible CPU and memory sizing supports explicit resource budgets; infrastructure consumption is the commercial opportunity.[6]

Portable application core. Open source, BPMN, containers and PostgreSQL reduce workflow-vendor dependence. OCI provisioning, IAM, Vault and storage integrations need replacement and validation on another cloud or on premises.

Controlled AI integration. OCI offers IAM, Bastion and private model endpoints. Private model connectivity and regulated production readiness were not validated by these tests.[7]

## Adoption

Clone the source. Try python -m benchlib.cli demo for an offline report. Reuse workloads and outcome checks, pin artifacts, record the environment and measure one change. The demo generates sample data; it does not run Fluxnova.

Next: finish the mixed study and cost attribution, then validate a customer-specific workload. The contribution-ready package is a proposed reusable benchmark protocol, not an adopted FINOS standard. Public release is under review.

## Credit and sources

Nicholas Cusato, Performance-Testing POV for FINOS Fluxnova on OCI, v1.1 (2026), and the benchmark implementation. Results through 28 October 2026 UTC.

Fluxnova software and original logo: FINOS community. Research and OCI implementation: Nicholas Cusato. Source repository currently requires access. No OCI price or performance superiority is claimed.

1. [FINOS Fluxnova](https://fluxnova.finos.org/engine)
2. [W01 evidence](https://github.com/oci-fsi-pursuits/fluxnova-benchmarking/blob/6b54359/validation/oci-20261027-w01-comparison/README.md)
3. [W02 evidence](https://github.com/oci-fsi-pursuits/fluxnova-benchmarking/blob/6b54359/validation/oci-20261027-w02-comparison/README.md)
4. [W03 evidence](https://github.com/oci-fsi-pursuits/fluxnova-benchmarking/blob/6b54359/validation/local-20261027-w03/README.md)
5. [Mixed study status](https://github.com/oci-fsi-pursuits/fluxnova-benchmarking/blob/main/docs/status.md)
6. [OCI Compute](https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm)
7. [Private model access](https://docs.oracle.com/en-us/iaas/Content/generative-ai/private-endpoint.htm)
