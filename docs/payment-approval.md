# Durable synthetic payment review

For the subsequent native-model path and tested protection against changed payment details during completion, use the [separate signed-intent lane](bound-payment.md). The standalone BPMN below remains the original lifecycle fixture and does not acquire those stronger controls automatically.

The separate `bench_payment_approval` process pauses at `payment_review` before any settlement task exists. The [driver](../services/payment_approval.py) accepts trusted over-limit payment inputs, saves their identity and hash in an atomic journal, and starts that process once. A reviewer explicitly chooses `approve` or `reject`. The engine stores the decision, simulated reviewer label and acknowledged payment hash in history. Approval reaches the existing settlement worker; rejection ends without invoking the ledger. Authorization and sanctions checks still apply after approval.

The original standalone validation used a **simulated reviewer through the existing engine administrator account**. It establishes a durable review lifecycle, not separate reviewer permissions, maker/checker enforcement or production payment authorization. The BPMN candidate group is a label, not evidence of enforced access control. An administrator can modify variables or complete tasks outside this driver. Engine and ledger credentials remain trusted. The existing payment agent and baseline settlement workload are unchanged: the agent's normal over-limit case still rejects rather than automatically entering this new lane. Native live-model routing into review remains a subsequent integration step.

Subsequent [opt-in agent routing and separate engine roles](approval-agent.md) passed six integrated fixture-agent cases and eleven permission/bypass checks. That module routes over-limit payments into this review BPMN and uses a separate reviewer account; the original default drivers and the historical nine-case administrator-review evidence keep their original scope. Use `services.review_payment` for the actual reviewer username label and consult the role documentation for the remaining trusted-reviewer limits.

The worker uses its existing fixed synthetic beneficiary; this lane does not test arbitrary beneficiary changes. No live model calls, new infrastructure or image replacements are required. Recorded cloud checks (retained in the private research archive) cover approval, rejection, backend policy, replay, changed payment detection and three Windows driver terminations.

## Run a review

Deploy only `workloads/payment-approval.bpmn` through the existing authenticated deployment adapter. The test runner below does this automatically. Start the existing localhost engine/support forwards and supply the same `BENCH_BASE_URL`, `BENCH_SUPPORT_URL`, `BENCH_USERNAME`, `BENCH_PASSWORD` and `BENCH_SUPPORT_TOKEN` environment variables used by the payment-agent checks. Retrieve secrets through the established local Vault helper; do not put them in command arguments or commit them.

From the repository root:

```powershell
python -m services.payment_approval --output dist/payment-review-001 --action start --payment workloads/payment-approval.json
python -m services.payment_approval --output dist/payment-review-001 --action status
```

The first command returns `pending` with a task/process ID and zero effects. Keep the journal private on persistent storage. Inspect the trusted payment JSON and journal before deciding. Then run **one** decision:

```powershell
python -m services.payment_approval --output dist/payment-review-001 --action approve
# Or use --action reject instead.
```

Approval can return `settling` briefly; repeat `--action status` to verify the completed history and independent ledger result. This example should settle exactly 2,000 minor units once after approval, or create no payment effect after rejection. An approved unauthorized or sanctioned input must still be rejected by the backend.

The payment snapshot is checked against historic variables before a decision. A changed amount causes `payment_snapshot_changed` with no completion POST. A preset `approved=true` cannot skip the mandatory user task. The driver accepts only the four trusted fields in the sample and only over-limit amounts.

## Recovery and limits

Repeat `--action start` with the **same directory** after a lost start response. An existing start intent is reconciled through exact business key and process definition; a missing or ambiguous match stops without another start. Repeat the same decision after interruption to reconcile its existing task/process. A conflicting decision is rejected. If a completion intent exists and the task is still pending, the driver stops with `unresolved_completion_no_retry`: inspect engine/history rather than blindly resending. Completed replay performs reads and creates no new process or payment.

Each journal has one OS-locked owner and a fixed one-hour decision window. Expiry blocks a new decision through this driver; it is **not** an engine timer, automatic rejection or cleanup. Pending tasks remain until explicitly handled. Existing decisions can be reconciled after expiry. Endpoints, driver/BPMN source hashes and optional supplied payment must match on resume. Authentication identity is not separately pinned or authorized as a reviewer. Losing the journal, workstation storage or engine/database is outside these same-host checks; do not create a replacement directory to recover an uncertain payment.

For the bounded nine-case real-engine matrix, use a fresh private directory:

```powershell
python scripts/run_payment_approval_checks.py --output dist/payment-approval-check-001
```

The runner terminates three separate owner processes after a pending task, a committed start response before journaling its ID, and a committed completion response before journaling its receipt. It also attempts raw completion replay. The tested engine returns HTTP 500 with a missing-task error for that replay; the runner records it and independently rechecks history/ledger. HTTP failure alone is never taken as proof of safe replay. This is a functional/recovery check, not a latency or capacity measurement.
