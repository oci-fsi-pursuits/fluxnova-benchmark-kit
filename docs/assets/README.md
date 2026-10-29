# Research handout, workflow diagrams and branding

`fluxnova-research-brief-20261028.pdf` is a new research handout by Nicholas Cusato, rendered from `presentations/fluxnova-team-brief-20261028.json`. It distinguishes completed findings, current research and public-release status. It is separate from earlier internally marked PowerPoint sources.

`fluxnova-logo.png` is the original horizontal color logo from the [FINOS branding repository](https://github.com/finos/branding/tree/master/project-logos/active-project-logos/Fluxnova%20Logo/Horizontal). Its bytes and colors are unchanged; use is attribution, not endorsement. The code license does not relicense logos, trademarks or third-party assets. The final-document inventory pins reviewed binary assets by SHA-256; a changed artifact requires a new visual and content review.

## Workflow diagrams

The nine final PNGs in `workflows/` were created for the [README walkthrough](../../README.md#three-changes-you-can-see). Diagram content, research and OCI benchmark implementation are by **Nicholas Cusato**. Fluxnova software is by **FINOS contributors**. The diagrams were generated with the built-in image generation tool and individually checked against the workload files and study documentation on 28 October 2026.

They use blue, cyan and teal from the [FINOS official branding resources](https://github.com/finos/branding), with Montserrat-style lettering. The PNGs contain no generated FINOS logo or wordmark. Branding does not imply that these experiments are an adopted FINOS standard or a certification.

| Image | Technical source | Meaning |
| --- | --- | --- |
| [Worker pacing](workflows/01-w01-worker-pacing.png) | [W01](../w01-worker-pacing.md) | Same workflow, changed delay after successful work |
| [Independent checks](workflows/02-w02-parallel-checks.png) | [W02](../w02-independent-checks.md) | Independent KYC/risk checks overlap; both must finish |
| [Review routing](workflows/03-w03-review-routing.png) | [W03](../w03-review-routing.md) | Keep required reviews and the same final verification |
| [Minimal](workflows/04-basic-minimal.png) | [BPMN](../../workloads/minimal.bpmn) | Start/end with disclosed input data |
| [Variables](workflows/05-basic-variables.png) | [BPMN](../../workloads/variables.bpmn) | Branching and persistence across an async boundary |
| [Async](workflows/06-basic-async.png) | [BPMN](../../workloads/async.bpmn) | One external task, expanded to explain worker/tool execution |
| [KYC](workflows/07-basic-kyc.png) | [BPMN](../../workloads/kyc.bpmn) | Approve, reject or wait for simulated reviewer completion |
| [Risk](workflows/08-basic-risk.png) | [BPMN](../../workloads/risk.bpmn) | Synthetic limit and freshness decisions |
| [Settlement](workflows/09-basic-settlement.png) | [BPMN](../../workloads/settlement.bpmn) | Policy-controlled synthetic ledger effects and replay checks |

These are conceptual flowcharts, not exact BPMN exports. Explanatory boundary boxes, worker detail and audit annotations do not add executable tasks. The six basic types and three comparison diagrams have different evidence scopes; they are not nine completed performance comparisons. The mixed-outcome follow-up reuses W02. See the [workload guide](../workloads.md) for additional agent, approval and recovery lanes.
