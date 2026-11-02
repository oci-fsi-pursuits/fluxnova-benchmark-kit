# A common contract for every benchmark

Protocol **0.2** standardizes how this project names, explains and publishes experiments. It is a community proposal, not an adopted FINOS standard or product certification. Earlier frozen experiments retain their original protocols; this catalogue does not retroactively change their acceptance rules.

The thesis is: **measure how workflow and runtime changes affect time to a correct, authorized outcome.** The intended user is a workflow developer or platform engineer deciding whether a particular change helps their workload.

## The terms we use

| Term | Meaning | Example |
| --- | --- | --- |
| Pattern | Executable workflow structure and its input/expected-outcome definitions | Independent KYC and risk checks |
| Benchmark type | The question and measurement method | Repeated latency comparison |
| Study | Frozen population, environment, control, change and analysis | W02, five sequential/parallel pairs at 1/s |
| Baseline | The control for that particular comparison | Sequential checks for W02; Java 21 for JVM |
| Result | Observation plus the interpretation its evidence supports | Lower observed latency at that load; capacity remains unmeasured |

“Agentic” describes work that uses an agent. The timed W01, W02 and PAY05 studies use deterministic synthetic dependencies. They do not establish live-model quality or an agent-versus-rules benefit.

The [illustrated study guide](benchmark-guide.md) explains all nine diagrams and the 17 study/coverage records. Open [the browser guide](benchmark-guide.html) locally for searchable explanations and evidence links. Records include completed, functional, partial and diagnostic evidence; their count is not a research completion percentage.

## Supported paths

| What you want | Requirements and command | What it establishes |
| --- | --- | --- |
| Understand a pattern or result | Source checkout, Python 3.12+: `python -m benchlib.cli catalog` then `python -m benchlib.cli explain W02` | A read-only experiment explanation |
| Validate the published register | `python -m benchlib.cli catalog --check` and `python scripts/build_benchmark_guide.py --check` | Required fields, category limits, source links/hashes and generated-guide consistency |
| Try the reporting format | `python -m benchlib.cli demo` | Labelled sample harness data; it does not execute Fluxnova |
| Execute the W02 example locally | Java 21, verified Fluxnova 3.0.1 distribution and Python; command below | Real local engine execution, 20 case/arm outcomes and replay checks |
| Show the same example in a browser | Build the saved viewer from that local run; command below | A portable walkthrough of recorded executions; switching cases does not start work |
| Run another supported local family | W03, PAY05, FNV06 or FNV07 public functional runners and their family guides | The declared functional subset, with H2/local limitations |
| Measure a cloud change | Frozen protocol, pinned runtime, isolated resources, reset evidence, telemetry/headroom and independent audit | Only the result justified by that qualified campaign |

From the repository root, substitute paths to your verified artifacts and use a new output directory:

```bash
python scripts/run_w02_functional.py --runtime PATH_TO_FLUXNOVA_3_0_1 --java PATH_TO_JAVA_21 --distribution-archive PATH_TO_FLUXNOVA_3_0_1_ZIP --output runs/w02-functional-001
python scripts/build_w02_demo.py --source runs/w02-functional-001 --output runs/w02-demo-001
python scripts/open_report.py --path runs/w02-demo-001/index.html
```

The first command starts its own loopback engine and synthetic backend, runs both BPMN graphs, checks effects and replays, stops its owned services and seals the evidence. It requires a complete distribution, not just a JAR. Use the artifact-pinning procedure in [reproduce](reproduce.md), and the detailed [W02 guide](w02-independent-checks.md). Paths can refer to verified cached artifacts; record their hashes. A source checkout is the supported distribution; a standalone wheel does not carry all workload files.

The [W03](w03-review-routing.md), [PAY05](pay05-fx-quotes.md), [FNV06](fnv06-events-and-batches.md) and [FNV07](fnv07-cluster-jobs.md) guides declare their additional prerequisites. FNV07 needs the built Java delegate. Each runner's `--help` describes its own accepted arguments. Local functional results do not reproduce OCI timing.

The cloud lifecycle is documented in the [OCI runbook](campaign-runbook.md). Historical JVM/release and soak results are indexed in the [sanitized register](../validation/research-register-20261101/README.md); their original operator automation and raw private archives are not a one-command public reproduction path. A new operator must declare and qualify their own campaign. This is an explicit distribution boundary, not evidence that those studies were rerun from a fresh public clone.

## Every submitted study uses the same explanation

Each record in [benchmark-catalog.json](../workloads/benchmark-catalog.json) must include:

1. Stable ID, benchmark type, evidence level and completion status.
2. Question, control, exactly what changes and what stays fixed.
3. Input population, expected outcomes, environment and observation/repeat design.
4. Primary metric and clock boundary, including polling and warmup/drain treatment.
5. Result, plain-English meaning and explicit limits.
6. Reproduction guide and SHA-256-bound evidence sources.

The frozen execution protocol must also retain artifact/input hashes, state reset, order of runs, deadlines, resource limits, stop rules and the independent outcome oracle. See the [benchmark protocol](benchmark-protocol.md) and [measurement contract](measurement-contract.md). A catalogue check validates publication structure and file integrity; it does not replace reanalysis of raw events or certify an experiment.

For paired timing studies, report every pair and the declared aggregate. For mixed traffic, keep settlements, refusals and safe stops separate. For a finite burst, preserve observer intervals instead of turning uncertain completion times into precise ratios. For functional studies, report outcomes and task counts without inventing performance savings. Keep unsuccessful attempts in the record.

## Contribution and publication checks

Add a study record only after its source evidence is reviewable. Update the final-document inventory when adding public documentation. Regenerate the guide and run:

```bash
python -m benchlib.cli catalog --check
python scripts/build_benchmark_guide.py
python scripts/build_benchmark_guide.py --check
python -m unittest discover -s tests -v
```

CI checks the catalogue and guide as well as the harness. A changed evidence file requires deliberate review and a new hash in the catalogue. Do not silently modify archived measurements to make a new narrative fit. Source text is normalized to LF in the repository so these hashes remain consistent across Windows and Linux.

Public evidence contains sanitized aggregates, source identifiers and limits. Credentials, deployed resource IDs, raw private SQL rows, recovery payloads and drafts remain outside the release inventory. The clean-history companion repository is the intended reuse destination. Its visibility remains a separate publication step.

## What remains research work

The talk can use completed bounded studies now. Customer-representative traffic, a completed higher-capacity confirmation, resource-qualified replica scaling, richer live-agent comparisons, platform/database alternatives and complete cost per correct outcome remain separate work. Control Center/Modeler journeys and several original financial/agent quality families also remain unevidenced.

The application workloads, containers and outcome checks are reusable. Infrastructure provisioning, identity, private access, evidence storage and cost adapters currently target OCI. Other clouds and on premises need their own adapters and validation. These studies establish no OCI price/performance superiority.

Research and OCI implementation: **Nicholas Cusato**. Fluxnova software: **FINOS contributors**. Code is Apache-2.0; logos and third-party assets retain their own terms.
