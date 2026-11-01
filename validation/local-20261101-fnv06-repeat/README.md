# FNV-06 local warmup and fresh-cell qualification

Six fresh Fluxnova 3.0.1 / Java 21 / H2 engines passed the fixed qualification on 1 November 2026 UTC. Each cell completed the same 100-recipient warmup before a separate 10- or 1,000-recipient burst, with individual messages, signal broadcast and asynchronous batches tested separately.

Independent audits reconciled 3,654 workflows: 600 warmup completions, 3,030 test-phase completions and 24 unchanged waiting controls. Within each pattern, both populations had matching audited warmup signatures: complete histories, variables, subscriptions, batch state, control identities and value-length rules were checked. All six owned engines stopped. The [aggregate](summary.json) retains the signatures and the private evidence seal.

This is a local workflow-state qualification. Each engine used a fresh H2 path; it does not qualify PostgreSQL reset, database cache equivalence, steady-state JIT behavior, integrated Linux resource headroom or the 45-cell performance study. Diagnostic timings are not reported as performance results.

Use the [repeated-cell guide](../../docs/fnv06-repeated-cells.md) to reproduce the fixed sequence. Raw requests, engine files and logs remain private.
