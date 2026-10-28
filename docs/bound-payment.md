# Native agent review bound to an original payment

The subsequent [persistent verifier](persistent-verifier.md) is now deployed with Vault-held signing material, supervised restart recovery and a native smoke. The historical ephemeral runner below must run in isolation while that service is stopped; both use the same topic. Its discarded keys are not adopted by the new authority.

The opt-in signed-intent lane connects the native OCI model driver to a separately authenticated reviewer and a verifier worker. Cloud validation (retained in the private research archive) passed thirteen fixture-agent cases, one additional cross-process replay probe and two native-model cases. Reviewer decisions were scripted through real engine accounts. No real payment system is involved; effects are in the existing synthetic SQLite ledger.

The trusted operator issues an HMAC-SHA256 token containing the original payment, beneficiary, expected reviewer, expiry, unique payment key and workflow definition. A separate SQLite registry binds that key once to the actual review process and completed screening process. Agent/reviewer children receive the token but not the signing key, registry authority or administrator credentials. The original payment fields remain trusted operator input; the model cannot grant payment authority.

After a review task completes, the worker checks:

1. The signature, registered token, expiry and actual engine business key/process ID.
2. The original amount, authorization/sanctions flags, beneficiary, payment key/hash and acknowledgment against workflow variables.
3. Exactly one completed review task and the actual completing username in engine history, rather than the submitted display label.
4. A completed fixed-operation screening process whose independent ledger fingerprint matches the same original payment.

Only then does it build the settlement request from the registered original payment. It reads any existing ledger receipt before sending, checks its canonical fingerprint and verifies the outcome/effect count afterward. A mismatch completes the workflow as `integrity_rejected` without invoking settlement. Thus the reviewer can submit changed fields through task completion, but those tested changes produce no payment effect. The registry rejects rebinding an intent to another process; a copied token in a second process is rejected even when its engine business key is reused. The original payment's ledger receipt remains unchanged.

The worker uses Python's [HMAC implementation and constant-time digest comparison](https://docs.python.org/3/library/hmac.html). This provides message authentication between the trusted issuer and verifier. It is not a public-key signature or an independently trusted external approval service.

## Reproduce the complete test lane

Use the existing private engine/support forwards, administrator setup environment and local OCI `BENCHMARK` session. The runner provisions three temporary engine identities, issues intents, starts agent children with restricted credentials, binds their actual process IDs and runs a short-lived verifier on the operator workstation. It handles only the new `bench-bound-review` topic. Existing containers, images and baseline workloads remain unchanged.

```powershell
python scripts/run_bound_payment_checks.py --lane fixtures --output dist/bound-fixtures-001
```

Run the native lane separately after the fixture lane finishes:

```powershell
python scripts/run_bound_payment_checks.py --lane live --output dist/bound-live-001 --model-id MODEL_OCID --model-region us-chicago-1 --compartment-id COMPARTMENT_OCID --profile BENCHMARK
```

This native lane declares at most four model requests across approval and rejection, two per journal, 40-second read/90-second action profiles and 256 output tokens per call. It stops on a failed or uncertain trial without retry. Completed replay uses cached model decisions; it makes no additional paid calls. The model uses the local operator's OCI session identity, while workflow actions use the separate restricted engine agent account. No operator OCI credentials are sent to a VM.

The native driver option is `--bound-intent OPERATOR_ISSUED_FILE` on `services.live_recoverable_agent --case over_limit`. The fixture driver accepts the same option. The explicit review command is:

```powershell
python -m services.review_payment --review-directory REVIEW_DIRECTORY --bound-intent OPERATOR_ISSUED_FILE --decision approve
```

That command requires the registered reviewer's engine identity. These raw options require an active trusted registry/verifier and a registered process binding; use the complete runner for the tested setup. Supplying an intent file alone is not a service deployment. The ordinary drivers retain their previous routing when the option is absent.

## Operational limits

The tested authority and verifier are short-lived, single-operator components. The signing key exists only in runner memory and is deliberately discarded afterward; archived tokens cannot then be cryptographically revalidated or resumed with a new random key. Registry bindings, source snapshots and completed checks remain retained, and artifact checksums can be verified. Persistent Vault-backed signing-key custody/rotation, a deployed verifier, service restart/HA, worker transport-failure recovery, independent payment rails, real identity assurance and browser review entry remain unfinished. Do not run concurrent independent registries/verifiers on the same topic.

The issuer, registry storage, verifier, engine administrator and simulator token remain trusted. This lane prevents the tested reviewer completion-field attacks; it does not establish production maker/checker certification or prevent a compromised trusted operator from issuing a false payment intent. Decisions are scripted, not a human usability test. Expiry is checked before effects by the verifier; an unattended pending task still has no automatic engine rejection timer.

The cross-process replay probe intentionally leaves two completed histories with one business key. The agent then fails closed on nonunique history; the probe demonstrates no additional effect, not seamless recovery from that deliberate ambiguity. Native provider tail latency remains unresolved despite the successful bounded checks. Server token usage is not a complete invoice or campaign cost allocation.
