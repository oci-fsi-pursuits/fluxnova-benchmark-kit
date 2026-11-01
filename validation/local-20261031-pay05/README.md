# PAY-05 FX quote selection: local functional qualification

**All 22 scenarios passed in both sequential and parallel BPMN: 44 scenario executions.** An independent audit verified 34 expected synthetic effects and ten expected no-effect outcomes, plus 352 repeated operations without extra effects and two blocked cross-process replays. The source is the checksum-verified Fluxnova 3.0.1 distribution on local Java 21 with isolated H2. Both arms use three worker slots. No cloud resources, live models, real FX quotes or payment network were used.

The backend chooses the minimum total source debit, including fees, for a fixed target payment. Cases cover expired/future quotes, unauthorized routes and payments, debit limits, missing quotes and minimum-quote rules, ties, wrong currency/beneficiary/payment/amount bindings, malformed numbers and unexpected authority fields. The original registered intent controls the effect; worker variables cannot override it.

## Retained initial failure

The first attempt completed two correct business outcomes with two expected effects, but failed the requirement that all three parallel quote calls overlap. Requests started 172, 297 and 422 ms after registration; each ran about 188 ms, so only pairs overlapped. Its evidence and failed status remain unchanged. The initiating cause was not isolated.

The fresh attempt preinitialized all client contexts before workers fetched tasks, kept the same corpus, 180 ms fixture delays, three slots and concurrency requirement, and passed the complete 44-execution qualification. The first failure is not pooled with the follow-up or presented as a performance improvement.

## Verification and limits

A later repeat of the 180 ms probe again failed three-way overlap after two correct payment outcomes; it is retained separately. Preinitializing clients was therefore insufficient to establish repeatability. Corpus revision 2 changes only the dedicated concurrency case to 1,000 ms per quote in both arms, retaining the overlap assertion and all expected outcomes. Its fresh local qualification passed 44 executions, 34 expected effects, 352 operation replays and two blocked cross-process replays; the independent auditor verified 115 sealed files. This deliberately longer functional probe is not a latency or speedup result. The original summary and its revision-1 evidence remain distinct from this follow-up.

The auditor checked 114 sealed files, original case bindings, workflow history/end events, quote receipts, exact ledger effects, sequential order and the declared three-way overlap probe. The final engine had zero active instances, external tasks or incidents. Owned local services stopped. All 439 repository tests passed with zero skips and real k6 enabled.

This is functional evidence. No latency advantage, capacity result, signed-intent integration, live-agent advantage, external FX-provider compatibility or host/database HA is established. OCI/PostgreSQL qualification and a prespecified timing study remain open.

See the [machine-readable summary](summary.json), [pattern and reproduction guide](../../docs/pay05-fx-quotes.md), [case corpus](../../workloads/pay05/cases.json), [runner](../../scripts/run_pay05_functional.py) and [independent auditor](../../scripts/audit_pay05_functional.py).
