# FNV-06 native Linux resource-reader qualification

A disposable container with no network access, a 0.25-CPU limit and 128 MiB memory limit supplied an idle control and a deliberately busy control. The reader retained 119 timestamped samples. Independent re-analysis of the sealed raw counters passed the idle control and detected CPU throttling in the busy control. The exact owned container was stopped and removed; no database was allocated for this probe.

The [sanitized aggregate](summary.json) contains the results and source/evidence digests. Raw samples retain PID start identity, cgroup identity, CPU and memory limits, ancestor counters, host counters and both wall and monotonic timestamps. Raw records and container identifiers remain private.

This qualifies the reader and its rejection behavior on the Linux host. It **does not establish headroom for the Fluxnova load generator**. Integrating sampling with workload intervals, calibrating the generator and qualifying database observations remain required before the repeated comparison.

Counter meanings follow the [Linux cgroup-v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html) and [proc documentation](https://docs.kernel.org/filesystems/proc.html). The audit checks ancestor throttling as well as the selected container and rejects missing coverage, changed limits, counter regressions and clock disagreement. The thresholds are declared engineering checks, not production service levels or proof of peak CPU capacity.
