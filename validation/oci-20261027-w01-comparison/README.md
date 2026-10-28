# W01: worker pacing during payment bursts

Status: **complete**; 10/10 campaign arms.

| Worker pacing | Correct / offered | Within 2 s | Late | Deadline goodput | Median run p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| success_delay_100ms | 1200 / 1200 | 600 | 600 | 1.000/s | 3574.0 ms |
| idle_only | 1200 / 1200 | 1200 | 0 | 2.000/s | 1761.7 ms |

Median paired change in correct on-time payments: **+1.000/s**. Median paired observed-p95 change: **-50.6%**. Deadline misses are retained.

Warmup is excluded from measured denominators. Median run percentiles are not pooled request percentiles.

- Two-minute measured burst intervals; not a sustained-load or maximum-capacity estimate.
- Observed latency includes the unchanged 100 ms completion observer.
- Shared controller plus fresh single-node OCI PostgreSQL; not comparable absolute timings to P01 or W02.
- Synthetic payments and exact effects, no live model or financial network.
- Host/Docker/SQL telemetry, not managed DB host CPU or full distributed traces.
- Actual billed cost is not measured.

Executed source: `dba00228be711c71927ab0f31d3274348794c113`. Independently rechecked raw events, exact persisted effects/receipts, reset fingerprints and resource samples.

## Fixed comparison

Each arm has 120 seconds of warmup and 120 seconds of measurement. Twenty payments arrive over one second, every ten seconds, for a mean of two starts/second. Five matched pairs contain 2,400 measured payments and 2,400 separate warmup payments. The original approved payment, exact effect, runtime, single worker slot, resource limits, database checkpoint and 100 ms observation polling stay fixed. Only the post-success delay changes. Current worker defaults already use idle-only backoff.

| Pair | Control on time | Candidate on time | Goodput change /s | Control p95 ms | Candidate p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 120/240 | 240/240 | +1.000 | 3574.0 | 1761.7 |
| 2 | 118/240 | 240/240 | +1.017 | 3667.3 | 1758.7 |
| 3 | 122/240 | 240/240 | +0.983 | 3555.5 | 1764.8 |
| 4 | 122/240 | 240/240 | +0.983 | 3554.2 | 1756.7 |
| 5 | 118/240 | 240/240 | +1.017 | 3674.2 | 1874.0 |

The primary result is the median within-pair goodput change, not a capacity estimate. The p95 range is descriptive, not a confidence interval. Every late but correct outcome is retained. All 4,800 persisted original measurement/warmup effects and receipts were checked directly in read-only SQLite archives. SQL dump bytes were checksum-verified; they were not independently restored for this report.

All ten checkpoints and telemetry gates passed. Maximum arm host-CPU p95 was 20.1%; minimum sampled available memory was 87.2%. These are shared-controller observations, not managed database host CPU. The generator calibration and original arrival accounting passed for every arm. All 492 exported file hashes verify. Generated credentials are excluded from the retained evidence archive.

## Browser demo and closeout

The saved W01 viewer passed all 60 pair/burst selections, with all 2,400 plotted payments checked against the original events. Keyboard use, evidence navigation, laptop width and visual review pass. No new payments or external requests occur in the viewer. See [browser automation](../../docs/browser-automation.md) and the [W01 protocol](../../docs/w01-worker-pacing.md).

The owned containers/network are removed and the exact temporary database is confirmed deleted. The original reference services, database schema/access and non-meter data are unchanged, with empty workload queues. Normal engine metering continued. Temporary access and sleep-prevention requests were closed. Original reference resources remain allocated and billable. The cumulative $298.89 planning reservation under the authorized $300 limit covers the existing reference window through 28 October 00:00 UTC; it is not actual billing.

Local/OCI qualification and all earlier preparation evidence remain retained. This completed current workflow set comprises W01, [W02](../oci-20261027-w02-comparison/README.md) and [W03](../local-20261027-w03/README.md). Representative customer mixes, sustained capacity, runtime/platform/database comparisons, live-agent benefit and complete unit cost remain separate research.
