# FNV-06 local load-driver qualification

The fixed small qualification passed on 1 November 2026 UTC, using the real checksum-verified k6 v2.3.0 executable and a fresh Fluxnova 3.0.1 / Java 21 / H2 engine.

- Nine observations: individual messages, signal broadcast and asynchronous message batch, each with 1, 4 and 16 intended recipients.
- 27 native k6 requests across 81 workflow instances.
- All 63 intended recipients completed exactly as expected; all 18 waiting controls stayed unchanged.
- Three asynchronous batches reached their terminal state. No unintended transition was observed.
- Owned k6 processes and the local engine stopped; workflow and subscription cleanup passed.

The [summary](summary.json) is produced from a separate saved-evidence audit. It checks raw k6 logs, request identities, complete REST inventories, recipient completion observations, batch job evidence and cleanup. Failed HTTP requests cannot pass merely because k6 exits successfully. The raw inputs and sealed execution evidence remain private.

This is driver and observer qualification only. One observation per cell, local H2 and no performance warmup do not establish a speedup, p99, maximum capacity or production readiness. A separate PostgreSQL qualification and a frozen performance design are the next gates. See the [load-driver guide](../../docs/fnv06-load-driver.md).
