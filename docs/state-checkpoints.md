# Repeatable benchmark state

`scripts/state_checkpoint.py` captures and restores the dedicated `fluxnova` PostgreSQL schema together with the simulator's SQLite ledger. Run it on the prepared Linux controller, which already has verified SSH targets, database TLS trust and Vault access. The load generator does not need database credentials.

A checkpoint preserves the history and ledger present at capture time. It is not an empty database, a whole managed-database backup or a reset of VM caches, JVM state, billing records or telemetry. Restores restart the application, simulator and worker; use the same warmup policy for every measured repetition.

The prepared checkpoint command now checks the persistent verifier before stopping other writers. An installed verifier must be inactive, have no worker process and be persistently masked; an active or merely disabled service blocks the operation. This pair format excludes its signed-intent registry. Use coordinated three-store recovery (retained in the private research archive) for payment recovery, and do not resume a verifier against an unrelated historical pair reset. The guarded source and previous controller version are retained.

## Capture, rehearse, restore

Use the controller's actual configuration, environment and completed deployment inventory. The following paths describe the existing reference cell. Choose new snapshot and output names for each capture or receipt; the tool refuses to overwrite them.

```bash
cd /opt/fluxnova-bench/workspace/release-d461007
PY=/opt/fluxnova-bench/venv/bin/python
PG_IMAGE=postgres@sha256:2d2b8998d31037bf721cfdf764d76ba74171b4fab3431b7f72c27c56ddbdf9e3
COMMON=(--config config.locked.local.json
  --environment dist/setup/environment.controller.local.json
  --deployment dist/remote-0535c10bb91142549818ac01c0665ef1/inventory.json
  --postgres-image "$PG_IMAGE"
  --snapshot dist/checkpoints/baseline-20261014-v2)

# The reference checkpoint already exists. To capture another, change the
# snapshot above and supply a new output receipt path.
"$PY" scripts/state_checkpoint.py capture "${COMMON[@]}" \
  --output dist/setup/checkpoint-capture.NEW.local.json

# Required before the first live restore of that checkpoint.
"$PY" scripts/state_checkpoint.py rehearse "${COMMON[@]}" \
  --output dist/setup/checkpoint-rehearse.NEW.local.json

"$PY" scripts/state_checkpoint.py restore "${COMMON[@]}" \
  --run-id restored-run-UNIQUE \
  --output dist/setup/checkpoint-restore.UNIQUE.local.json
```

The pinned tools image contains PostgreSQL 17.11 utilities; the reference managed database uses PostgreSQL 17.6. Pull the pinned image on the controller first. The isolated rehearsal needs Docker, roughly 4 GiB of memory and a local Unix socket; it exposes no TCP listener and has no network access.

Capture and restore require an idle cell: no active process instances, external tasks, incidents or running containers on the dedicated load-generator host. The repository's `Cell.require_idle_loadgen()` checks container IDs with `docker ps --quiet`; matching the displayed image name is insufficient because Docker may show a pinned k6 image as only a short ID. Keep other operators and automation from starting work during the operation. The tool serializes checkpoint commands, checks the deployment targets and runtime images, stops the three benchmark writers, and checks the database queues again. It uses a custom PostgreSQL archive and SQLite's online backup API. The SQLite helper needs a writable volume mount for WAL bookkeeping, while opening its backup source read-only. See the [PostgreSQL dump documentation](https://www.postgresql.org/docs/17/app-pgdump.html) and [SQLite backup documentation](https://www.sqlite.org/backup.html).

Deployment note, 15 October 2026: the container-ID guard was deployed to the controller and load generator after the completed baseline (retained in the private research archive) was collected and verified. Its deployed idle-host check passes; unit tests verify that a running container blocks the operation before any writer is stopped. The reporting optimization was deployed at the same stage and reproduces the final sealed run exactly. Original source files were backed up. Use `Cell.require_idle_loadgen()` in subsequent controller preflight checks and continue preventing concurrent workloads during checkpoint operations.

Rehearsal restores into disposable PostgreSQL and SQLite copies, compares data/schema fingerprints, changes the copies, and verifies restoration again. Live restore requires a matching successful rehearsal receipt, checks checkpoint file hashes and deployment identity, and first captures the current pair as a private rollback checkpoint. PostgreSQL restore uses one transaction and a generated archive selection that preserves the existing schema, its grants and schema-specific default privileges. Those controls must already match the snapshot; the application principal owns the tables but need not own the schema. The pair is verified before writers resume, including schema/table ownership and permissions. See [PostgreSQL restore semantics](https://www.postgresql.org/docs/17/app-pgrestore.html).

The supported schema contains ordinary tables, indexes and sequences. Custom functions, non-internal triggers, views and partitioned tables are rejected. External schema dependencies and global database objects are outside scope; successful isolated restoration is required rather than assuming a schema dump is self-contained.

## Failure and evidence handling

If either store fails to restore, the tool first checks whether both stores still match the rollback checkpoint, as can happen after a transactional permission failure. Otherwise it attempts to restore and verify both rollback stores. It resumes writers only after a successful restore or a verified rollback. An unverified rollback leaves the writers stopped and identifies the retained rollback checkpoint in the private operation receipt. Do not restart writers against a partially restored pair. Resolve the failure and recover the retained pair first.

Normal failures and termination signals trigger cleanup; host loss or forced process termination can still interrupt recovery. Inspect the private receipt, checkpoint files and container state after an interrupted operation. Failed command stderr is retained under `dist/checkpoints/diagnostics/` with restricted permissions. Application readiness after restart is checked separately from the fingerprint of the quiesced stores.

Snapshots include engine identity data and workflow history. Keep them private under the ignored `dist/checkpoints/` directory; do not put raw snapshots, manifests or diagnostic logs in Git or public reports. Public validation should contain only aggregate counts, checksums and outcomes. Preserve a separate approved backup before removing any checkpoint.

## Campaign integration

The restore command prints a JSON receipt containing `run_id`, `success`, `dataset_sha256`, `state_fingerprint` and `method`. The runner requires a successful receipt for the exact run, verifies both pinned hashes when `comparison` supplies them, and seals `reset.json` and `reset.log` into the run archive. A nonzero exit, timeout, malformed receipt or mismatched identity stops the workload before calibration/load begins. Existing custom reset hooks must emit this receipt contract; a zero exit code alone is insufficient.

`scripts/checkpoint_broker.py` connects the load-generator hook to the controller without a new listener, network rule, load-generator SSH key or copied database credential. It polls a private request directory on the load generator over the controller's existing strict-host-key SSH connection. Requests contain only run/session identities, checkpoint hashes and a deadline. The controller supplies all restore commands, credentials and paths. This coordinates trusted benchmark hosts; it does not protect against an operator who can alter their files.

Install the matching `benchlib/reset_mailbox.py`, `benchlib/checkpoint.py`, `benchlib/runner.py` and `scripts/reset_mailbox.py` on the load generator, and the complete checkpoint/broker implementation on the controller. Use the completed deployment inventory to select the existing release paths. Start a bounded broker on the controller after successful checkpoint rehearsal:

```bash
"$PY" scripts/checkpoint_broker.py "${COMMON[@]}" \
  --duration-s 1800 --max-resets 2 \
  --output dist/setup/reset-broker.UNIQUE.local.json
```

Keep that process running while the campaign runs on the load generator. Its `ready` output identifies the new mailbox and both checkpoint hashes. Configure the load-generator run with those exact values:

```json
{
  "comparison": {
    "dataset_sha256": "MANIFEST_SHA256_FROM_BROKER",
    "state_fingerprint": "STATE_FINGERPRINT_FROM_BROKER",
    "reset_evidence_file": "reset.json"
  },
  "execution": {
    "reset_timeout_s": 620,
    "reset_command": [
      "/opt/fluxnova-bench/venv/bin/python",
      "/opt/fluxnova-bench/workspace/LOADGEN_RELEASE/scripts/reset_mailbox.py",
      "request", "--mailbox", "MAILBOX_FROM_BROKER",
      "--run-id", "{run_id}", "--timeout-s", "600",
      "--dataset-sha256", "MANIFEST_SHA256_FROM_BROKER",
      "--state-fingerprint", "STATE_FINGERPRINT_FROM_BROKER"
    ]
  }
}
```

Merge these fields into the existing configuration; retain its runtime, telemetry, workload and cost settings. Each reset request is unique and cannot overwrite an earlier receipt. The hook permits one outstanding request, and the broker closes the mailbox after its request limit, expiry or failure. It validates the restore's writer-resume flag before returning success. Session lifetime must cover every reset and workload, including enough remaining time for the final request's full timeout. A five-run baseline requires a fresh session with `--max-resets 5` and a suitably budgeted duration (maximum six hours); the two-request validation session cannot be reused.

The metrics publisher can record expected unavailable-service samples while writers are stopped. Require fresh successful telemetry during the actual workload. Keep reset duration separate from the load window and apply the same warmup after each restart.

If the hook times out or the controller disconnects, no workload starts without a valid matching receipt. A restore already in progress can finish after the client deadline. Inspect its private controller receipt and confirm writers/queues before retrying. Do not force-kill a restore process; normal broker termination forwards a termination signal and waits for restore cleanup. A killed client may leave an active-request lock: preserve that mailbox for diagnosis and start a new bounded session after recovery. Do not reuse its request IDs or manufacture a successful receipt.

Two short reset-controlled repetitions validate this integration; they do not replace instrumentation overhead checks, current cost estimates or the five full baseline repetitions.
