# W03: review where the policy requires it

**Completed in its synthetic functional/task-count scope:** the same ten original payment cases produced the same expected outcomes in both arms. The all-review control created ten reviewer tasks; policy routing created four, preserving all four mandatory reviews. Four payments settled exactly once in each arm. [Evidence and limits](../validation/local-20261027-w03/README.md).

This is deterministic workflow routing. It is not an engine latency result, an agent-versus-rules result or measured human productivity. The all-review arm is an explicitly constrained demonstration, not a claimed customer operating baseline. Its six additional reviews are visible by construction; the experiment verifies that the executable routing, required review actors and final effects agree with the declared policy.

## Fixed policy and corpus

Both arms use the same immutable registered inputs, process binding and policy:

- Reject missing authorization or a sanctions match. A reviewer cannot override these checks.
- Block unknown checks or stale market information. Review approval cannot make those inputs current or known.
- Require a separately authenticated reviewer for an amount above 1,000 minor units or a high-risk payment.
- Otherwise permit direct processing after verification. All-review deliberately adds a reviewer to these cases too.

The ten cases include two eligible payments, two over-limit decisions, two high-risk decisions, unauthorized and sanctioned payments, unknown checks and stale information. Both approve and reject decisions are exercised on the required-review paths. The [frozen corpus](../workloads/w03/cases.json) declares expected outcomes independently of the backend.

Actual task histories and authenticated completion operations establish review counts and actors. A forged `reviewerIdentity` variable is insufficient. Four real engine permission denials, two actual completions by the wrong actor, premature-settlement probes, 120 repeated operation calls and 20 cross-process replay denials passed. The independent audit reconciled all eight exact effects from SQLite with original payment amount, beneficiary and identity. The local engine and temporary principals were removed afterwards.

## Reproduce and inspect

With the pinned local Fluxnova 3.0.1 distribution and Java 21 runtime from the existing local-validation setup:

```powershell
python scripts/run_w03_functional.py --output dist/w03-functional-NEW
python scripts/build_w03_demo.py `
  --source dist/w03-functional-NEW --output dist/w03-demo-NEW
```

Open `dist/w03-demo-NEW/index.html`. Choose a case and switch the control/candidate buttons. The page shows recorded executions and links the original evidence and BPMN source. It starts no new payment and needs no cloud, model, sign-in or server. Copy the adjacent ZIP to share the viewer.

Automate the saved viewer with an installed Python Playwright environment and Chrome:

```powershell
python scripts/rehearse_w03_demo.py `
  --bundle dist/w03-demo-NEW --output dist/w03-browser-NEW
```

On the prepared workstation, use the OCI Python 3.13 environment and `--playwright-path dist/browser-review-tools`. Optional `--headed` displays the isolated browser. Use fresh output names so failures and source evidence remain retained.

The 27 October viewer passed all 20 selection combinations, actual JSON-link navigation, keyboard operation, screenshot review and the 1366×768 layout check. It is ready for a short saved-data walkthrough; check the actual presentation display before the talk. Show an eligible case with no candidate review, then an over-limit case where review remains, then an unknown case with no effect.

## What still needs research

The 300 ms scripted pause only demonstrates waiting. Human active handling time and time saved are unknown. A representative workload needs a customer-approved policy and case distribution; a valid handling-time study needs actual reviewers. A later live-model comparison must justify discretionary tasks and hold these payment safeguards fixed. Neither the original deployed verifier nor its Vault-held signing authority was changed for W03.

Research and OCI implementation: **Nicholas Cusato**. Fluxnova software: **FINOS contributors**.
