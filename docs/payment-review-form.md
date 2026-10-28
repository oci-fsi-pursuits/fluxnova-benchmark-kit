# Payment review in Tasklist

The Phoenix cell now serves a dedicated payment form for new `bench_bound_payment_approval` instances, starting at definition **version 2**. Browser checks (retained in the private research archive) passed approval and rejection with a separately authenticated non-admin reviewer. The verifier still runs independently on the support VM.

!Synthetic payment review in Tasklist (retained in the private research archive)

## Review a payment

The trusted operator first issues the intent for the reviewer, runs the agent until `pending_review`, binds the actual review/screening identities, and assigns the task to that reviewer. See [intent operations](persistent-verifier.md#connect-and-operate). Provide the reviewer with Tasklist application access and an assigned-task filter, using the scoped permissions (retained in the private research archive). The validation accounts and filter were temporary and have been removed; no interactive review is waiting after the tests.

With the private support-host forwards open, visit [Tasklist](http://127.0.0.1:19080/fluxnova/app/tasklist/default/) and sign in as the reviewer registered for the payment:

1. Open the assigned **Review signed synthetic payment** task.
2. Read the amount, delegated limit, beneficiary, payment reference, authorization and sanctions flag. Amounts are synthetic minor units; no currency is assumed.
3. Choose **Approve this payment** or **Reject this payment**.
4. Check **I have reviewed the payment details above and confirm my decision.**
5. Select **Complete** to record the decision.

Completion remains disabled until both a decision and acknowledgment are present. There is no manual hash-copying or variable editing. The form does not display the bound-intent token. The authenticated reviewer is recorded in engine operation history. Approval is independently checked before simulated settlement; rejection makes no settlement request.

If a result cannot be confirmed, refresh Tasklist and inspect whether the task remains before deciding again. The form blocks another submission in that same uncertain form instance. Browser refresh, agent resume and completed replay were tested without additional effects; arbitrary network-failure recovery and draft Save behavior were not tested. **Complete**, rather than Save, records the review decision.

## Deploy and validate

The [form](../workloads/forms/payment-review.html) and [BPMN](../workloads/bound-payment-approval.bpmn) are packaged together in the engine deployment. The installed Fluxnova 3.0.1 browser SDK uses `fxn-script` and `fxnForm`; the BPMN parser continues to use its existing Camunda extension namespace. The form uses the SDK's variable lifecycle to fetch display data, retain the original payment hash and submit only `reviewerDecision` plus `reviewedPaymentSha256`. It removes display-only variables from the SDK's submission set. Browser read-only controls and acknowledgment are usability controls; the server's signature, process/screening binding and authenticated-actor checks remain the payment authority.

Using the existing trusted `BENCH_*` environment and private forwards, the focused installer deploys only this definition and form, verifies their hashes, and refuses to change a different current version or an active review definition:

```powershell
python scripts/deploy_payment_review_form.py --expected-current-version 2 --receipt dist/payment-form-deployment-002.local.json
```

With unchanged sources this is a no-op; the repeated cloud deployment stayed at version 2. A changed form requires an idle definition and creates a new version without migrating existing instances. Use a new receipt path. If a deployment response is lost, inspect current definition/resources before trying again. The main `bench deploy` resource collector also includes referenced embedded forms and preserves their deployment-relative paths; focused unit checks cover missing/outside references and multipart resource names. The full deployment command was not run against the live baseline as part of this change.

The [browser runner](../scripts/run_browser_review_checks.py) now defaults to `--form-mode payment`; see runner setup (retained in the private research archive). It checks required choices, read-only display controls, the two-field browser payload, real reviewer history, ledger effects and replay. It makes zero native model calls. The older generic-form mode requires a compatible older deployment; it does not select a historical definition. Earlier evidence retains its original source/BPMN versions, which must be used for source-bound journal replay.

A person can now walk through this form, but human usability, accessibility, browser claim/unclaim, adversarial browser editing and real payment/identity integration remain open. VM/boot-volume/database recovery is a separate next stage. The existing signed-intent worker and baseline containers were unchanged. The form lifecycle follows the [embedded-form SDK pattern](https://docs.camunda.org/manual/7.24/reference/forms/embedded-forms/javascript/lifecycle/), with Fluxnova's installed binding names verified from its distribution.
