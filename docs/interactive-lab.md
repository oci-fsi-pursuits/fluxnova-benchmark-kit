# Run a workflow you can see

The **live lab** executes work in Fluxnova. The older `demo` command generates sample report data, and the saved W02 viewer displays recorded evidence.

The optional integration check, `python scripts/check_lab_live.py --output runs/lab-check-001`, creates an owned engine, checks the connection adapter, starter flows, manual decisions, deadline/stop handling, and cleanup isolation. It needs the same cached runtime and Java as the lab, and makes no cloud calls.

```bash
python -m benchlib.cli lab --download-runtime
```

With Python 3.12+ and Java 21, this opens **http://127.0.0.1:8765** using an isolated H2 engine. [Provider choices](../providers/README.md) cover OCI deployment and existing engines.

## A three-minute walkthrough

1. Choose **Sequential checks**. Leave one instance, 500 ms check delay and 250 ms observer interval. Click **Run workflow**. The graph highlights steps from engine history; the result records the end event and observed duration.
2. Choose **Parallel checks** with the same settings. Two simulator slots let KYC and risk overlap. Compare individual durations in the run history. This illustrates independent work, without claiming a new benchmark result.
3. Choose **Manual approval**, select `Approved` as the expected end event, and run. Click **Approve** on the active task. Repeat with `Rejected` and **Reject**. These are real engine user tasks.
4. **Upload a .bpmn file**, set scalar input variables, choose an expected end and run. User/external tasks expose a variable editor and manual completion. Files are validated before deployment.
5. **Download session JSON**, then **Close lab**. Local output includes deployed BPMN, run records and cleanup receipts. The owned local engine stops; an existing engine stays running.

## What the numbers mean

Each run requests 1–20 instances **one at a time**. Delay applies to each starter check using two worker slots; their simulated work overlaps, while completion acknowledgements are serialized to avoid concurrent updates to the same engine join. This demo scheduler does not alter uploaded external services. Observation intervals are 100–1,000 ms and deadlines 1–600 seconds per instance. An unfinished instance is cancelled at the deadline or on stop. A completion first observed after the deadline is recorded as late.

Observed time includes REST start, engine/simulation work, observation requests and manual wait. It is neither isolated engine latency nor capacity. The expected-end check confirms an end event, not an independently verified payment, AI decision or downstream effect. JSON also records whether supplied input values remained unchanged; intentional task updates may change them.

For a benchmark, declare baseline/treatment, inputs, expected business effects, fixed resources, offered load and repeat rules. Add an independent verifier and retain failures. Use the [protocol](benchmark-protocol.md) and established workload runners.

## Upload profile

The first lab profile accepts acyclic flows only. Loops are rejected before deployment so a synchronous cycle cannot continue inside an attached engine after the client's request times out.

One process, 2–50 nodes, 256 KB XML: plain start/end events, sequence flows, exclusive/parallel gateways, user/manual tasks and external service tasks. A BPMN `manualTask` passes through automatically; use `userTask` to pause for a person. Conditions compare one scalar to a literal, such as `${approved == true}`. Variables support strings, finite numbers, booleans and null.

Scripts, delegates, listeners, connectors, uploaded forms, arbitrary expressions, subprocesses, timers/messages/signals and DMN are rejected. BPMN is executable code; this first UI supports a limited profile. The graph preview lays out control flow automatically and is not a full modeler. Original source hashes and namespaced deployed copies are retained.

## Isolation and limits

The UI binds to `127.0.0.1`, checks Host/Origin and requires a per-session action token. Engine credentials stay on the Python server. Remote engines require verified HTTPS or a loopback tunnel. Unique keys/topics and exact definition/instance/task IDs isolate lab actions from shared task queues.

One active run, at most 12 models and 50 runs per session. Closing deletes only this session's deployments and engine history, retaining local/exported results. Ownership/cleanup receipts identify leftovers after failures. Supplied data may appear in exports; use synthetic input and review before sharing.
