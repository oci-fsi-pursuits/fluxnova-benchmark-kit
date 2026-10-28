# Mixed payment outcomes under steady load

W02-M1 qualifies the load driver for the [W02-S1 sensitivity corpus](w02-dependency-sensitivity.md). It combines approved payments, expected refusals, unknown-check safe stops and one-time dependency faults under continuous arrivals. This is local Windows/Java 21/H2 qualification; the completed OCI latency study retains its original approved-only population and result.

**Completed 28 October 2026 UTC:** all 300 measured outcomes verified, comprising 120 settlements, 120 expected refusals and 60 safe stops. There were 58 deadline misses, zero dropped starts and no incorrect or unresolved outcomes. All 60 warmup outcomes also verified. The [independent audit and browser review](../validation/local-20261028-w02-mixed-load/README.md) pass; all four owned engines are stopped. These are diagnostic local counts, not a new paired performance result.

## Frozen inputs and accounting

The [protocol](../workloads/w02/mixed-load-protocol.json) crosses all ten existing W02 cases with five dependency-delay profiles. Each measured cycle contains 50 scenarios: 20 settlements, 20 refusals and 10 safe stops. This equal coverage is synthetic, not a sourced customer distribution.

The seeded grid repeats by arrival ordinal, with unique payment and case identities in each phase. Both arms receive the same original inputs. Workers remain running across warmup and measurement. Each run owns a fresh engine, H2 database and SQLite backend.

| Order | Workflow | Offered starts/s | Warmup | Measurement | Measured arrivals |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | Sequential | 0.5 | 20 s | 100 s | 50 |
| 2 | Parallel | 0.5 | 20 s | 100 s | 50 |
| 3 | Parallel | 1 | 20 s | 100 s | 100 |
| 4 | Sequential | 1 | 20 s | 100 s | 100 |

Both arms retain two workers, 16 fixed VUs per scenario, 100 ms completion polling, a two-second deadline and a 15-second drain allowance. The warmup and measurement scenarios have separate VU pools; unfinished warmup requests can overlap the next phase. Warmup counts remain separate. Measurement covers whole grid cycles. Fixed order and one pass qualify the driver; they do not support a matched performance percentage or capacity conclusion.

The report separates:

- **Settled:** exact original amount and beneficiary, authorized checks, one effect.
- **Rejected:** the prespecified refusal, with no payment effect.
- **Blocked:** an unknown check stops settlement, with no payment effect.
- **Invalid or unresolved:** missing, conflicting or incorrect evidence; never counted as a verified outcome.

Each category reports verified and within-deadline counts. Only timely settlements contribute to settlement goodput. A safe blocked result is a correct functional result, not a completed payment. Deadline misses are reported rather than invalidating this qualification: the slowest sequential dependency profile already totals two seconds before workflow overhead. This policy was frozen before execution; correctness, zero drops, generator scheduling, bounded backlog and clean final state remain required.

## Reproduce and inspect

With the [prepared pinned local tools](w02-load-driver.md#run-the-local-qualification), run from the repository root:

```powershell
python scripts/qualify_w02_load.py `
  --mixed-protocol workloads/w02/mixed-load-protocol.json `
  --output dist/w02-mixed-load-NEW --port 28284

python scripts/audit_w02_mixed.py `
  --source dist/w02-mixed-load-NEW --output dist/w02-mixed-audit-NEW
```

Use fresh output directories and an unused loopback port. Retain failed attempts. Omitting `--mixed-protocol` keeps the existing approved-only driver qualification and its deadline gate. Supplying conflicting phase lengths with a mixed protocol fails before runtime startup.

The runner archives the input grid, controls and source files before calibration. The k6 driver verifies the corpus SHA-256 before execution. The Python oracle independently resolves each arrival's original input, checks lifecycle and receipt clocks, and reconciles exact effects and final state. Scheduled timestamps record the declared schedule; started timestamps measure actual dispatch lag. Dropped arrivals remain in the offered denominator and fail qualification.

The offline auditor rechecks the recursive seal, reconstructs events from raw k6 logs, recomputes assessments, and opens each original SQLite database read-only. It verifies registrations, receipts, effects, fault records and audit events against each observed outcome. It also checks engine cleanup and confirms that auditing leaves the source unchanged. Open the audit directory's `report.html` for a saved-results view; `summary.json` retains full diagnostic distributions. No new workload starts from this page.

On the prepared workstation, browser automation uses the existing Playwright dependencies:

```powershell
& "$env:USERPROFILE\oci\Scripts\python.exe" scripts/rehearse_w02_mixed.py `
  --source dist/w02-mixed-load-NEW --report dist/w02-mixed-audit-NEW `
  --output dist/w02-mixed-browser-NEW --playwright-path dist/browser-review-tools
```

The check compares all four run rows and twenty profile rows with the independent audit, opens the JSON evidence link, exercises keyboard disclosure and captures desktop/laptop screenshots for visual review. It blocks external network requests.

## Next measurement gate

Before a new OCI comparison, refresh authentication and receipt-reconciled cost coverage; qualify the mixed driver, state reset and resource telemetry on the actual cloud setup; then freeze repeated paired measurements and the outcome-specific primary metric. Keep settlements, expected refusals and safe stops distinct. A representative customer study additionally requires sourced input proportions, policy, dependency/retry behavior and deadlines. A live agent-versus-rules comparison requires a task where model discretion is useful under the same authority and review constraints.

Research and OCI implementation: Nicholas Cusato. Fluxnova software: FINOS contributors.
