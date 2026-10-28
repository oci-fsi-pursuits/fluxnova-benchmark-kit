# Persistent payment intent authority and verifier

The Phoenix support VM now runs `fluxnova-bound-verifier.service` as the dedicated `fluxnova-verifier` Linux user. It is enabled for startup and restarts after failure. Cloud checks (retained in the private research archive) passed a pending-review restart, two actual SIGKILL boundaries, rejection, tampered amount and a separately bounded native-model smoke. Existing benchmark containers and their images were not replaced.

Following the verified scheduled rollout (retained in the private research archive), the service fetches **Vault version 2** of `fluxnova-bench-bound-verifier` at startup using the VM's instance principal. Version 2 is CURRENT/LATEST and version 1 remains PREVIOUS. The secret contains the HMAC key ring with an explicit legacy verification mapping, management bearer token, dedicated engine username/password and existing simulator token. Loaded values stay in the service's memory; the installed JSON configuration contains references/endpoints only. No workstation OCI profile was copied to the VM. Version pinning uses the [Secret Retrieval API](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/secrets/client/oci.secrets.SecretsClient.html).

The service identity has two engine definition grants: read/history/variable access plus `UPDATE_INSTANCE` on `bench_bound_payment_approval`, and read/history/variable access on `bench_agent_screen`. It has no administrator-group membership or authorization-management grant. The verifier and simulator credentials remain trusted. The VM's existing secret-read policy was sufficient; no OCI IAM policy or new compute resource was created.

The source release is root-owned under `/opt/fluxnova-bound-verifier/releases/`, with `current` pointing to the deployed release. State lives in `/var/lib/fluxnova-bound-verifier` with mode **0700**. An OS lock permits one registry owner. The [systemd unit](../deploy/fluxnova-bound-verifier.service) applies a private temporary directory, read-only system paths, no new privileges, restrictive umask and disabled core dumps. The installed service has no recovery-test arguments or override.

The key-lifecycle code (retained in the private research archive) is now deployed and independently verified (retained in the private research archive) after local tests and an isolated OCI rehearsal. This code-only rollout preserves Vault version 2, real signing keys, key-policy generation 1, configuration bytes and the systemd unit; no real key is revoked or retired. All 30 original issuance identities replay identically before activation, after activation and after another restart. Two valid synthetic payments settle once and one tampered token is rejected without an effect; their screening/review workflows produce six completed histories. The final registry has 33 MAC-verified records, with all 30 original rows preserved. Referenced legacy material remains required by current records and backups.

## Connect and operate

Keep the existing Bastion-to-controller tunnel open. Forward through the **support** host so the authority remains bound only to its loopback interface:

```powershell
ssh -F dist/setup-20261013/ssh.local.conf -N -T `
  -L 127.0.0.1:19080:10.78.0.28:8080 `
  -L 127.0.0.1:19081:127.0.0.1:8090 `
  -L 127.0.0.1:19082:127.0.0.1:8092 fluxnova-support
```

The operator CLI reads the management token from the pinned Vault version into memory. Health is available without that token; issuance, binding and intent status require it:

```powershell
python scripts/manage_verifier.py --config dist/setup-20261013/persistent-verifier-config.local.json health
python scripts/manage_verifier.py --config dist/setup-20261013/persistent-verifier-config.local.json --profile BENCHMARK issue --request-id UNIQUE_PAYMENT_PLAN_ID --payment workloads/payment-approval.json --reviewer REVIEWER_USERNAME --output dist/intent-001.local.json
```

Use a stable request ID for one trusted payment plan. Repeating it with the same payment/reviewer/TTL returns the original intent and expiry, including after restart. A changed request under that ID is rejected. A lost issuance response must be reconciled using that same ID; do not generate a replacement identity. The CLI refuses to overwrite an existing intent file.

Run the agent as the restricted **agent identity** with the existing application environment:

```powershell
python -m services.approval_agent --output dist/agent-persistent-001 --case over_limit --bound-intent dist/intent-001.local.json
```

For the native model, use `services.live_recoverable_agent` with the same `--bound-intent`, `--case over_limit`, model/region/compartment/profile arguments and the declared `--read-timeout-s 40 --wall-s 90`. The model session remains on the workstation. The agent returns `pending_review` after screening.

Before a reviewer decides, the trusted operator must bind the actual process identities. Read them from the agent journals, which contain no signing key:

```powershell
$agentJournal = Get-Content dist/agent-persistent-001/checkpoint.json | ConvertFrom-Json
$reviewJournal = Get-Content dist/agent-persistent-001/review/bound-review.json | ConvertFrom-Json
$intent = Get-Content dist/intent-001.local.json | ConvertFrom-Json
python scripts/manage_verifier.py --config dist/setup-20261013/persistent-verifier-config.local.json bind --key $intent.key --process-id $reviewJournal.process_id --screening-id $agentJournal.workflows.kyc.process_instance_id --screening-key ($agentJournal.request_id + ':kyc')
```

Only after binding, authenticate as the registered **reviewer identity** and run:

```powershell
python -m services.review_payment --review-directory dist/agent-persistent-001/review --bound-intent dist/intent-001.local.json --decision approve
# Or choose --decision reject.
```

The service independently verifies the signed original payment, actual review actor, process binding and screening evidence. It then processes the task without the operator running a local worker. Resume the original agent command with `--resume` to reconcile its outcome; cached decisions do not make another paid model call. See the [role policy](approval-agent.md) when provisioning interactive agent/reviewer users. The validation users were temporary and have been removed; the verifier's dedicated identity remains.

Check or restart the service with the existing private SSH access:

```powershell
ssh -F dist/setup-20261013/ssh.local.conf fluxnova-support sudo systemctl status fluxnova-bound-verifier --no-pager
ssh -F dist/setup-20261013/ssh.local.conf fluxnova-support sudo journalctl -u fluxnova-bound-verifier -n 30 --no-pager -o cat
ssh -F dist/setup-20261013/ssh.local.conf fluxnova-support sudo systemctl restart fluxnova-bound-verifier
```

Logs contain bounded event/outcome/error-class metadata, not credentials. A killed worker's engine task can remain locked for up to 60 seconds; allow the lease to expire. The restarted worker validates the original signature, process, actor and payload, then reconciles a matching committed receipt even after intent expiry. An expired intent with no receipt cannot authorize a new send. Do not unlock or restart a payment blindly.

## Backup, rollout and limits

Create a new consistent registry backup over SSH:

```powershell
python scripts/backup_verifier.py --ssh-config dist/setup-20261013/ssh.local.conf --output dist/verifier-registry-001.sqlite
```

The backup includes intent/issuance/binding data and MACs, but no signing key or service password. The current post-code-rollout registry and off-host backup contain 33 MAC-verified records and require the matching pinned version-2 bundle. All 30 pre-code-rollout intent/issuance records, including the earlier 28, are preserved. Earlier backups retain their own numbered Vault references. The separately retained coordinated recovery backup predates these rollouts and has twenty-eight intents; its checksum and all MACs were verified using the pinned Vault key. Earlier six-, twelve-, fourteen-, eighteen-, twenty-two-, twenty-four- and twenty-six-intent snapshots are retained separately. Keep it private and retain the Vault version reference. For restoration, stop the service, restore the verified registry with ownership `fluxnova-verifier:fluxnova-verifier` and restrictive permissions, keep the matching Vault key version, then restart. A different key fails startup before serving requests. Coordinated engine/ledger/registry recovery (retained in the private research archive) now also passes in an isolated restored stack, with an independent SQL re-restore; the original managed database and history were preserved. Controlled restore onto another existing VM and failback (retained in the private research archive) now passes, using the guarded restore CLI. Isolated verifier-VM hard-stop recovery (retained in the private research archive) now also passes with an unreachable source and fresh OCI STOPPED checks; the engine and ledger stayed online.

For another installation, stage the repository's service modules, workloads and [unit](../deploy/fluxnova-bound-verifier.service) in a root-owned release under `/opt/fluxnova-bound-verifier/releases/`. Create the dedicated Vault bundle with the fields checked in [the loader](../services/verifier_service.py); use generated 32-byte signing material and a separate high-entropy management token. Fill the [reference-only configuration](../deploy/verifier.config.example.json), confirm the VM can read that exact secret version, and run [the installer](../deploy/install-bound-verifier.sh) as root with the release/config paths. The current deployment reused the host's existing OCI SDK environment. Drain active work before a code/key rollout. The repository now includes opt-in scheduled key rotation (retained in the private research archive) with local, isolated OCI and persistent benchmark rollout validation; the installed service now uses the tested key ring. Do not replace a key under a live registry or silently migrate on startup.

The verifier now owns `bench-bound-review`. Stop it and drain tasks before running the earlier ephemeral-registry harness, which uses that same topic. Pause it before coordinated engine/ledger checkpoint restores or controlled baselines; its polling is an additional runtime factor. Existing sealed performance reports retain their earlier configuration.

This remains a single-active service with boot-volume state, not automatic infrastructure HA. A controlled cross-VM restoration (retained in the private research archive) passed pending-review recovery and expired committed-receipt reconciliation, followed by failback. A hard stop and reboot of the isolated verifier VM now pass, with the stale copy disabled, intact unit/release files and recovery from an off-host backup. The installer flushes writes before activation. Browser approval/rejection (retained in the private research archive) passes, and the [dedicated payment form](payment-review-form.md) is now deployed as definition version 2. Host/boot-volume loss, real identity assurance, human usability and a real payment rail remain open. Revocation and unused-key retirement pass local and isolated OCI gates, and their code is deployed; no real compromise response has been activated. Scoped SQL/internal-identity recovery (retained in the private research archive) now passes on disposable state; external IdP/OCI IAM recovery, global policy rollback protection and retirement of referenced archival material remain separate work. Scheduled key rotation passes both an isolated OCI rehearsal (retained in the private research archive) and the persistent benchmark rollout linked above. The VM and users with access to its instance principal remain trusted; Oracle describes this [instance-level permission boundary](https://docs.oracle.com/en-us/iaas/Content/Identity/Tasks/callingservicesfrominstances.htm). The authority secret is outside the original Terraform reference state; preserve its version and the registry backup before cell teardown, and handle retirement through an explicit retention decision. The historical $90 whole-cell estimate was within the then-approved $100 through 17 October 00:30 UTC. The current planning ceiling is $300, with a conservative $209.18 cumulative reservation through 23 October 01:00 UTC; the deployed budget alert has not been applied to that new value. Partial posted resource matches are documented in the host-outage evidence; complete campaign and per-payment costs remain unavailable.
