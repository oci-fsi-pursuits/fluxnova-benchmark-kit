# W01: payment bursts and worker pacing

Question: does removing an artificial delay after successful work improve correct, on-time payment completions during bursts? The control reconstructs the historical 100 ms success delay. The candidate is the current worker default: retry/idle backoff remains 100 ms, with no success delay. This is a research control, not a recommended default or an assertion that current Fluxnova adds this delay.

Both arms use the same settlement BPMN, approved synthetic amount of 125 minor units, beneficiary, one worker slot, full engine history, 100 ms completion polling and 2-second deadline. Every payment must have one exact effect and the expected original policy decision. Rejections and faults are qualified separately. Raw observations include the poll delay and are not engine-only timings.

The driver offers 20 payments over one second, every ten seconds: 2 starts/s averaged over the full interval. It retains each scheduled arrival, dropped iteration, accepted process, completed outcome and exact side effect. A deadline miss is a result, not a reason to discard a run. Invalid evidence, wrong or unknown effects, incomplete draining, an invalid checkpoint reset or an overloaded generator stops the study.

## Local qualification

```powershell
python scripts/qualify_w01_pacing.py --help
```

Use the pinned Fluxnova distribution and k6 installation described by the command. The 27 October local qualification (retained in the private research archive) passed exact accounting and effects for 120 payments. It is not an OCI comparison, capacity estimate or presentation improvement claim.

## Frozen OCI comparison

The isolated runtime in `scripts/run_w01_cloud.py` requires a private prepared identity, an explicitly owned fresh PostgreSQL database and a frozen source manifest. It refuses to run as a general local launcher. Provisioning and cleanup must independently verify the exact target resource and W01 ownership tags. The original reference application is preserved.

Before measurement: qualify six approval/rejection/fault cases in both arms with three repeated tool calls each, calibrate loopback HTTP at 600 requests/s, and run four short arms in ABBA order. The measured study then freezes five matched pairs, with a reproducible balanced arm order, 120 seconds of warmup and 120 seconds of measurement per arm. Each arm has 240 measured arrivals and 240 separate warmup arrivals. Total measured denominator: 2,400.

Restore and verify the same engine/backend checkpoint before every arm. Keep images, resources, database, JVM, worker identity, inputs and instrumentation fixed. Only the declared success-delay argument changes. Sample Linux host, Docker and PostgreSQL counters; require coverage, no container failures, host CPU p95 below 80%, available memory at least 20%, and calibrated HTTP rate at least twice the observed peak one-second request bucket. The generator has a 4 GiB limit on the existing controller; no VM scale-out is part of the comparison.

Primary outcome: paired difference in **correct on-time payments per measurement second**, using the full 120-second denominator including quiet time. Also report p95/p99, lateness, original outcomes, dropped starts, observed backlog and resource samples. These are short synthetic burst comparisons. Do not compare their absolute latency with P01's four-host baseline or W02's different workflow. Do not infer maximum capacity, real financial-network latency or an LLM benefit.

The current planning window and existing USD 300 reservation must cover creation through confirmed deletion. Retire owned containers/network and delete only the exact owned temporary database; preserve evidence and verify the original reference cell afterwards.

## Completed evidence and saved viewer

[W01 matched burst study](../validation/oci-20261027-w01-comparison/README.md) completed five valid matched pairs. 600/1,200 control and 1,200/1,200 candidate payments correct within two seconds; median paired deadline-goodput change +1.000/s. All 2,400 measured payments correct.

Build the saved viewer from the retained archive, then run the [browser checks](browser-automation.md):

```powershell
python scripts/build_w01_demo.py --source dist/setup-20261013/w01-cloud-20261027-001/campaign-snapshot --output dist/w01-demo-new
```

The builder independently re-audits the full campaign before accepting it. The viewer exposes all 60 paired bursts and 2,400 measured payment observations on a fixed axis. Its original-event links and checksums remain available offline. No workloads run when the page opens.
