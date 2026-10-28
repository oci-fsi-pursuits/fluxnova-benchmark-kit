# A 60–90 second workflow comparison demo

Show **the same payment and controls, with a different order for independent checks**. The portable viewer displays recorded Fluxnova executions: original payment inputs, the actual sequential/parallel BPMN graphs, check receipts and verified outcomes. Switching the workflow selects another saved execution. It does not start work.

The 27 October bundle reuses the 20-case local functional qualification (retained in the private research archive). It contains eight settled outcomes, eight rejections and four blocked outcomes. Every settled payment has exactly one expected synthetic effect; the other cases have none. The full corpus also verifies 120 repeated calls and two blocked cross-process replays. This is separate from the [completed matched OCI performance study](../validation/oci-20261027-w02-comparison/README.md), which found 32.1% lower median paired observed p95 at 1/s. That percentage comes from the ten-arm campaign, not these saved local timelines.

## Build and open

From the repository root, using Python 3.12 or newer:

```powershell
python scripts/build_w02_demo.py `
  --source dist/w02-functional-20261026-005 `
  --output dist/w02-demo-NEW
```

Use a fresh output name. The example source is the retained local run on the prepared workstation; private run evidence is not in Git. On another machine, first [produce the functional qualification](w02-independent-checks.md#reproduce-the-functional-qualification) and supply its output directory as `--source`.

Open `dist/w02-demo-NEW/index.html` in a browser. To move the walkthrough to another machine, copy the adjacent `w02-demo-NEW.zip`, extract it and open `index.html`. Keep its adjacent `assets` and `evidence` folders. No server, sign-in, cloud resource or live model is needed. Viewing the records generates no new payment.

The builder verifies the complete original checksum inventory, rechecks the original payment outcomes and receipt overlap, and copies selected evidence without changing its bytes. The bundle includes its own hashes and the original inventory; files omitted from the smaller bundle are still listed in that original inventory. Engine configuration, credentials and databases are excluded. Local HTML evidence links may display JSON or download it, depending on the browser.

## Talk track

| Time | Action | Say |
| --- | --- | --- |
| 0–20 seconds | Select **Approved payment / Sequential checks**. | “The original payment, KYC and risk inputs, and two worker slots are fixed. Both checks must complete before settlement.” |
| 20–45 seconds | Switch to **Parallel checks**. Point to the join and overlapping receipts. | “These checks are independent, so this graph can overlap them. The join still waits for both. This is one saved functional execution. The separate five-pair OCI study measured 32.1% lower observed p95, with every measured payment still correct and on time.” |
| 45–65 seconds | Select **Unauthorized payment**, then **Unknown KYC result** if time allows. | “Faster is useful only when the outcome is correct. Missing authority is rejected. An unknown result blocks payment. Both produce zero effects.” |
| 65–90 seconds | Point to repeated-call evidence, then return to the talk. | “Repeating a completed operation preserves the original result. Our research measures time to a correct, authorized outcome, not just request speed.” |

Both timelines use the same axis within a case. Receipt durations are local per-execution observations, not p95, throughput, a speedup estimate or production KYC latency. The dependency waits are synthetic. Review-required cases refuse settlement here; this walkthrough does not run a human approval flow.

This can occupy the existing demo slot in the 15-minute agenda; do not add another slide or extend the talk. The earlier Tasklist approval demo (retained in the private research archive) remains a separate option for illustrating a reviewer.

## Review status

Data and packaging checks pass (retained in the private research archive). The 27 October automated browser rehearsal (retained in the private research archive) also passed all 20 case/workflow selections, keyboard controls and opened evidence links. A 75-second recording was captured, played back and visually reviewed. This uses Playwright independently of the desktop automation connection; it does not require a signed-in browser.

With Python Playwright and its browser/recording dependencies installed:

```powershell
python scripts/rehearse_w02_demo.py `
  --bundle dist/w02-demo-NEW `
  --output dist/w02-browser-rehearsal-NEW --record
```

On the prepared workstation, use the OCI Python 3.13 environment with `--playwright-path dist/browser-review-tools --browser-cache dist/playwright-browser-deps`. Run `--help` for the optional headed mode. Dependencies must match the Python interpreter. The script writes a fresh output directory, verifies the original bundle before and after, and never starts an engine workload. Recording requires Playwright's FFmpeg dependency.

Before presenting, open the extracted bundle on the presentation laptop, follow the sequence above, confirm that both workflow buttons and every case selector update the visible outcome, and check text size at the actual display resolution. Use a rehearsed recording or the existing labelled screenshot if that review cannot be completed.

Research and OCI implementation: **Nicholas Cusato**. Fluxnova software: **FINOS contributors**. The project [source and reproduction guide](https://github.com/oci-fsi-pursuits/fluxnova-benchmarking) credits the software separately from this research.
