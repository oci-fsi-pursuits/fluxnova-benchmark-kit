# FNV-06: give every comparison the same starting conditions

The planned study asks how long it takes to finish 10, 100 or 1,000 intended workflows. Before comparing those populations, each run needs the same preparation and complete evidence.

The fixed qualification uses six cells: two populations (10 and 1,000) for each of messages, signals and batches. Every cell starts fresh, runs 100 warmup recipients with two waiting controls, then runs its test burst with two additional controls. Warmup and test event names differ. The auditor checks every expected history and variable, verifies the waiting controls, and compares warmup signatures within each pattern. Those signatures describe equivalent declared workflow state, not identical physical cache or JIT state.

The [local six-cell qualification](../validation/local-20261101-fnv06-repeat/README.md) passed. Reproduce it with verified Fluxnova, Java and k6 installations from the [driver guide](fnv06-load-driver.md):

```powershell
python scripts/run_fnv06_repeat_qualification.py --output dist/fnv06-repeat-NEW
```

Use fresh output paths. The command retains each cell, independently audits it before starting the next, and stops on failure. It starts six local engines in sequence and closes each one. It does not run the 45 performance cells or allocate cloud resources.

## Guarded PostgreSQL lifecycle

The cloud runner now accepts the same fixed qualification cells. An operator must supply a fresh signed OCI inventory, retain the preceding cell and its passing independent audit, and bind the evidence seal before requesting a reset. It rejects the reference database, changed ownership, active sessions, remaining work, foreign process history and stale guards. The role and schema names are fixed by the owned trial identity.

The reset runs in one transaction and checks catalogs outside the trial schema before committing. PostgreSQL documents that [DROP SCHEMA CASCADE can affect dependencies in other schemas](https://www.postgresql.org/docs/current/sql-dropschema.html); an outside-catalog change or an expired window causes rollback. Prior evidence stays sealed, and the next cell receives a fresh engine schema and role. The [cloud attempt](../validation/oci-20261101-fnv06-repeat/README.md) passed its first three cells and three resets, including a direct cross-schema rollback control. It stopped when the 1,000-recipient signal request timed out; final outcome evidence for that cell is incomplete, and both batch cells remain unattempted. The complete six-cell gate remains open. The earlier [one-time reset rehearsal](../validation/oci-20261101-fnv06-scale/README.md) remains separate evidence.

## Resource checks for short bursts

The [native Linux probe](../validation/native-20261101-fnv06-resources/README.md) passed both an idle control and deliberate throttling detection. The reader keeps raw cgroup-v2 and host counters. Its independent auditor requires samples on both sides of the work interval, checks stable process identity and limits, and examines ancestor throttling too. Sample gaps, clock disagreement and counter resets cannot be converted into a pass.

This is a reader qualification, not yet a workload-headroom result. Before the [declared 45-cell comparison](fnv06-population-scaling.md), qualify the new failure-state/SQL capture path, complete the cloud reset/warmup checks, integrate resource sampling with the actual generator and engine, add database observations, calibrate the generator and retain failures and deadline misses in the final accounting. Keep the frozen populations, client counts and separate pattern semantics unchanged.
