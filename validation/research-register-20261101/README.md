# Retained research register

This sanitized [summary](summary.json) indexes supporting studies that were previously scattered across private operator closeouts. It extracts selected fields from unchanged retained evidence and records each original source identifier and SHA-256. It does not expose private archives or turn those operators into a public one-command runner.

| Study | Supported result | Interpretation |
| --- | --- | --- |
| P01 async baseline | Five 30-minute measurements at 10/s; 90,000 correct outcomes, all within two seconds; per-run p95 245.7–249.0 ms | Repeatable reference for the async/echo path with 100 ms polling |
| Java 21 / 25 | Five pairs; 180,000 correct/on-time outcomes; median paired p95 difference −0.862 ms, range −1.369 to +0.864 ms | Small mixed-sign differences do not establish a broad advantage or equivalence |
| Fluxnova 3.0.1 / 3.1.1 | Five pairs; 180,000 correct/on-time outcomes; median paired p95 difference +0.126 ms, range −0.552 to +1.055 ms | No meaningful speedup or capacity gain established at this fixed load |
| Eight-hour soak | 288,000 correct/on-time outcomes at 10/s | One accepted endurance observation; no maximum-capacity or leak-free guarantee |
| 16/s confirmation | Three measured passes, fourth observation interrupted during warmup, fifth unattempted | Incomplete series; 16/s is not qualified |
| Agent, recovery and observer studies | Original source hashes retained; qualitative scope in the catalogue | Supporting bounded evidence, not new quantitative comparisons |

Runtime/release studies use the same async external-task/echo path, not live-model or customer-payment traffic. Their primary result is candidate minus control. Java values in the JSON source use seconds; the table converts to milliseconds. The release treatment includes its required schema migration. Prior stopped runtime/release studies remain separate and are not pooled with the completed follow-ups.

The nine diagrams and all result meanings are explained in the [benchmark guide](../../docs/benchmark-guide.md). Follow the [supported-path contract](../../docs/benchmark-standard.md) before reproducing or extending a study. Full outcome audits remain distinct from catalogue integrity checks.

Research and OCI implementation: Nicholas Cusato. Fluxnova software: FINOS contributors.
