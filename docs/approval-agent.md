# Agent routing with separate engine roles

The later SQL/internal-identity recovery gate (retained in the private research archive) now restores synthetic database roles, engine users/groups/grants and pending review while preserving agent/outsider denials. Its procedure (retained in the private research archive) covers credential rotation and group-membership loss; external SSO/OCI IAM recovery remains open.

The subsequent [signed-intent/native lane](bound-payment.md) tests native model routing and rejects the previously open reviewer completion-field attacks. A [persistent verifier](persistent-verifier.md) now provides Vault custody and supervised process recovery. The historical role checks below retain their original scope; use the bound-intent workflow for the stronger payment binding.

The opt-in `services.approval_agent` driver connects the existing bounded model dispatcher to durable payment review. It uses the deterministic model fixture and real Fluxnova workflows. It does not make native OCI model calls. Cloud validation (retained in the private research archive) passed six integrated cases, eight actual HTTP 403 permission checks and three entry-workflow bypass probes.

The trusted adapter performs screening first. An over-limit payment returns `pending_review` and leaves an engine user task with zero effects. A separately authenticated reviewer approves or rejects it. Resume uses the original agent journal, cached model decisions and existing workflow identities; it does not call the model fixture again or start another payment. A reviewer approval still cannot make the tested unauthorized input pass the settlement backend; sanctions stop at screening before review.

## Engine permissions

Authorization was already enabled in the tested Fluxnova 3.0.1 cell. No engine restart, image change or global policy change was needed. The tested accounts had these explicit grants:

| Identity | Resource | Granted permissions |
|---|---|---|
| Agent | Process instance `*` | `CREATE` |
| Agent | Definitions `bench_agent_screen`, `bench_agent_direct`, `bench_payment_approval` | `READ`, `CREATE_INSTANCE`, `READ_INSTANCE`, `READ_HISTORY`, `READ_TASK`, `READ_INSTANCE_VARIABLE`, `READ_HISTORY_VARIABLE`, `READ_TASK_VARIABLE` |
| Agent | Definitions `bench_kyc`, `bench_settlement` | Metadata `READ` only, to test an explicit creation denial |
| Reviewer | Definition `bench_payment_approval` | Same read permissions above plus `TASK_WORK`; no `CREATE_INSTANCE` |

Do not grant `UPDATE_INSTANCE`, `UPDATE_TASK`, general task `UPDATE`, authorization administration or membership in the administrator group to the agent. The engine denied its task completion, amount update, process modification, generic KYC/settlement starts and authorization escalation. The reviewer could complete the review task; its start and direct process-variable update requests were denied. The engine's user-operation log recorded the actual reviewer username for successful completions, independently of the submitted display label.

The fixed-operation BPMNs are necessary for these roles. The original generic workloads take their worker scenario from caller variables. `bench_agent_screen` fixes it to KYC and fixes `approved=false`; its attempted settlement injection produced no payment. `bench_agent_direct` fixes the settlement operation, `approved=false` and delegated limit **1,000 minor units**, with a BPMN amount gate before the external task. An attempted 2,000-unit start with a forged 999,999-unit limit produced no ledger invocation. The adapter requires the same fixed 1,000-unit delegation and routes larger amounts into mandatory review. These entry definitions supplement the original baseline workloads.

The reviewer is still trusted. `TASK_WORK` permits task completion with submitted variables; denying a standalone variable-update endpoint does **not** prove immutable payment fields against a hostile reviewer. The review CLI submits only decision, username and payment acknowledgment, and the driver checks the snapshot before completion. Signed backend payment intent, hostile reviewer completion-variable injection, cross-payment binding and production maker/checker controls remain unfinished. The model never receives engine/support credentials; the trusted adapter still holds the simulator token. The tested boundary is engine REST authorization, not an independently secured real payment rail.

## Use the opt-in driver

Deploy `workloads/agent-screen.bpmn`, `workloads/agent-direct.bpmn` and `workloads/payment-approval.bpmn` through an administrator. Provision approved non-admin identities using the table above and store their passwords using the existing Vault process. The test runner provisions temporary accounts with generated credentials held only in memory and removes them afterward; its test accounts are no longer available for interactive use. User IDs must satisfy the installed engine's identifier policy; the tested IDs use only letters and digits.

With the existing private forwards and environment setup, run as the **agent identity** (`BENCH_USERNAME`/`BENCH_PASSWORD`), keeping the same engine/support endpoints:

```powershell
python -m services.approval_agent --output dist/agent-review-001 --case over_limit
```

It returns `pending_review`. In a separate operator environment authenticated as the **reviewer identity**, inspect the payment and then explicitly decide:

```powershell
python -m services.review_payment --review-directory dist/agent-review-001/review --decision approve
# Or use --decision reject.
```

Return to the agent identity and resume the same journal:

```powershell
python -m services.approval_agent --output dist/agent-review-001 --case over_limit --resume
```

If settlement is still executing, the status can be `settlement_in_progress`; subsequent resume reconciles the same process. The journal retains the original action deadline and source/endpoint/principal bindings. New starts are prohibited after that deadline, while reconciliation of existing review decisions remains allowed. The review's separate one-hour driver decision limit and [ambiguity rules](payment-approval.md#recovery-and-limits) still apply. Review storage is shared on the same host in these checks; distributed journal ownership and infrastructure failover are not established. The original default fixture/live drivers retain their previous over-limit rejection behavior; this integration is explicitly selected through the new module.

For a bounded repeat with fresh temporary identities, use the existing administrator environment:

```powershell
python scripts/run_approval_role_checks.py --output dist/approval-roles-check-001
```

The runner retains failures and removes its temporary users/grants in `finally`. An interrupted or failed run can leave a pending synthetic review for explicit reconciliation; inspect its journal and engine state rather than deleting histories or repeating uncertain starts. Current successful validation ended with empty queues and removed both users and all seven explicit grants. No credentials were saved in its evidence bundle.
