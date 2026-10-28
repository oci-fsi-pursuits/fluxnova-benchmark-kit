# W03 review routing — 27 October 2026

**Passed** the frozen ten-case corpus in both actual Fluxnova workflows: 20/20 expected outcomes, eight exact synthetic payment effects and zero outstanding instances, external tasks or incidents. The local Fluxnova 3.0.1/Java 21/H2 runtime stopped, and temporary engine users/grants were removed.

| Same ten cases | All-review control | Policy routing |
| --- | ---: | ---: |
| Expected outcomes | 10/10 | 10/10 |
| Actual completed review tasks | 10 | 4 |
| Mandatory reviews preserved | 4/4 | 4/4 |
| Settled payments / exact effects | 4 / 4 | 4 / 4 |
| Reviewer rejections | 2 | 2 |
| Authority/sanctions rejections | 2 | 2 |
| Unknown/stale cases blocked | 2 | 2 |

The independent re-audit verifies 62 original file hashes, histories, authenticated review actors and every persisted effect against the original payment. All 120 repeated calls preserved receipts/effects; all 20 cross-process replay probes were refused. Four actual HTTP 403 permission checks passed. Two separate processes completed their review using the wrong authenticated actor despite a forged reviewer-identity variable; the backend refused both effects, and the owned probe processes were explicitly removed.

The separately built saved viewer passed automated Chrome checks for all 20 case/arm combinations, keyboard controls, original JSON evidence navigation, no external requests or script errors, and no horizontal overflow at laptop width. Five representative screenshots were visually reviewed. No new engine execution occurred during browser testing.

**Interpretation:** six fewer actual review tasks on this constructed corpus, with identical expected outcomes and every required review retained. This does not establish a representative customer reduction, human time saved, engine performance improvement or a live-agent advantage. The all-review arm is explicitly a constrained demonstration. The 300 ms review pause is scripted, and active human handling time remains unknown.

Private evidence: `dist/w03-functional-20261027-002/`; saved viewer: `dist/w03-demo-20261027-001/`; browser checks: `dist/w03-browser-rehearsal-20261027-001/`. The first local attempt failed before any payment because of an adapter attribute mismatch; its failed status and successful owned cleanup are retained in the preceding `-001` folder.

See [sanitized summary](summary.json), [protocol and runnable commands](../../docs/w03-review-routing.md) and [current completion register](../../docs/workload-completion.md).
