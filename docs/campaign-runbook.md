# Campaign command reference

Start with the [project README](../README.md) for the architecture and first-time setup. Commands below run from the repository root unless another host is specified.

> [!IMPORTANT]
> The Python adapter's `config_file` method uses API-key authentication. It does not currently construct a browser-session security-token signer. A successful `oci session authenticate` supports OCI CLI commands with `--auth security_token`, but is not sufficient for this runbook's Python cloud commands. Use a supported API-key profile or an appropriately authorized instance/resource principal. The CLI reads campaign JSON, not local `terraform.tfvars` files.

A portable evidence pipeline for Fluxnova, starting with OCI Compute and PostgreSQL. The command line provisions via OCI Resource Manager, builds versioned artifacts, deploys BPMN workloads, runs open-arrival k6 tests, verifies outcomes, and regenerates reports from archived evidence.

**Read implementation status (retained in the private research archive) before making performance claims. The first OCI reference cell was deployed with local Terraform on 12 October 2026; application deployment and PostgreSQL-backed golden/smoke validation completed on 13 October.** A local functional run or synthetic fixture is never an OCI capacity result.

See the local validation record (retained in the private research archive) for real engine correctness and smoke evidence. The Compute/PostgreSQL implementation is the first stage. OKE, Autonomous, live agent orchestration, infrastructure failover and browser tests remain subsequent implementation stages.

## Resume the existing local deployment

The Phoenix cell already has four bootstrapped private VMs, an ACTIVE PostgreSQL 17.6 database, OCI Bastion and a private container repository. Its existing `infra` evidence bucket is versioned and managed outside the reference Terraform state. Do not run `provision --apply` to resume this environment: that command uses Resource Manager and would create a separate stack.

Keep `infra/oci-reference/terraform.tfstate` protected and backed up. The applied inventory is saved locally at `dist/oci-plan-20261012-bastion/environment.local.json`; refresh it from Terraform outputs after infrastructure changes. See the [Bastion connection instructions](oci.md#forward-controller-ssh-through-oci-bastion).

1. Placement, controller SSH, registry login, digest-pinned publication and remote deployment are complete. The operator authorized replacing `OIC`; `kove` was preserved.
2. Database TLS, the dedicated application schema/user, three application secrets and required secret-access checks pass. Fluxnova 3.0.1 is running against PostgreSQL 17.6 with authentication enabled.
3. The corrected worker uses a dedicated Docker network to reach its same-host simulator. All 25 golden cases and 30 smoke workflows pass. Failed pre-fix evidence is preserved separately. See validation results (retained in the private research archive) and the [prepared-controller details](remote-deployment.md#continue-the-prepared-phoenix-controller).
4. Application/database/engine telemetry and a bounded 1/2/5 workflow-per-second async sweep now pass. See follow-up results (retained in the private research archive). Failed golden instances were cancelled with diagnostics and history retained. Those exploratory runs did not reset retained history. Complete actual cost evidence remains outstanding. The refreshed whole-cell estimate (retained in the private research archive) is $75 over 72 hours ending 2026-10-16 00:30 UTC, within the unchanged $100 budget; it replaces the prior $50/48-hour estimate.

5. Five-minute async discovery (retained in the private research archive) passed at 5/s (1,500/1,500), then stopped at 10/s after overload, deadline failures and invalid generator/count evidence. The 20/s step was skipped. Queues drained naturally, all accepted instances later reached completed history state, and a 10/10 low-rate recovery smoke passed. Original invalid/failed evidence is preserved. Isolate the single worker's pacing and completion-polling overhead, and reconcile dropped-arrival counts before repeating higher-rate discovery. The Usage API returned pre-deployment records only; current benchmark charges are not yet evidenced.

6. The worker-pacing follow-up (retained in the private research archive) removed the worker's successful-task delay and passed fresh golden validation plus 3,000/3,000 workflows at 10/s over five minutes. The 20/s trial failed deadlines with invalid generator/count evidence; queues drained naturally and recovery passed 10/10. A network-disabled k6 probe reproduced the count discrepancy. The load generator's active `config.telemetry.local.json` now contains the tested worker digest and fresh golden reference. Three run archives reproduce identical reports offline; all five new evidence archives are uploaded and verified in the versioned evidence bucket.

7. The polling comparison (retained in the private research archive) passed four 120-second runs at 10/s, using 100-500-500-100 ms polling. All 4,800 outcomes met the two-second deadline. Request counters quantify observer traffic; longer polling delays observed completion, so latency changes are not engine execution changes. The main configuration retains 100 ms polling. Start any higher-rate series separately with fresh telemetry and empty queues; no higher-rate trial was part of this comparison.

8. The 15/s intermediate-rate trial (retained in the private research archive) passed 4,500/4,500 workflows over five minutes, keeping 100 ms polling and a two-second deadline. Recovery passed 10/10 with empty queues. Billing now contains partial benchmark-owned rows, but no complete cost allocation.

9. The [checkpoint procedure](state-checkpoints.md) and bounded controller broker now restore identical PostgreSQL/SQLite state before each run. Two short reset-controlled repetitions (retained in the private research archive) passed 600/600 measured workflows and reproduced their reports offline. Use a fresh broker session for each subsequent campaign; closed mailboxes cannot be reused.

10. The Java-agent overhead pilot (retained in the private research archive) passed all four runs (4,800/4,800), with golden cases and export-mode checks in both modes. The subsequent five-repeat baseline (retained in the private research archive) completed on 15 October at 01:45 UTC: all 90,000 measured workflows passed, and all five archives reproduce offline. Final queues are empty, agent-on mode remains active, and broker/publisher sessions are stopped. The container-ID checkpoint guard and analyzer optimization were then deployed and checked. Start fresh helper sessions for another campaign.

The prepared baseline uses the existing five-repetition profile at a fixed **10 workflows/second**: each repetition has 600 seconds of warmup, 1,800 seconds of measurement and a 30-second drain allowance. Keep the Java agent enabled, polling at 100 ms, the two-second deadline, the corrected worker and external telemetry unchanged. Pin the verified checkpoint in the reset hook and run fresh golden cases first. With a 620-second reset timeout and calibration/runner allowances, the five repetitions reserve 16,000 seconds (4 hours 26 minutes 40 seconds). Allow 800 additional seconds for agent verification and golden validation; a five-hour broker/publisher lifetime covers this 4-hour-40-minute planning bound. Stop on the first failed or invalid repetition, retain its evidence, and verify queues before further work. Check that the remaining dated cost window covers the full bound; the configured duration alone does not establish that.

The authenticated PostgreSQL-backed workflow, telemetry smoke and five-repeat 10/s asynchronous baseline milestones are complete. Discovery also includes a passing 15/s observation and a nonpassing 20/s observation; maximum capacity and complete cost conclusions remain unestablished. Restart the bounded controller metrics publisher (retained in the private research archive) and a fresh checkpoint broker before new controlled runs. Use local Terraform to manage or tear down this deployment; the root CLI Resource Manager lifecycle commands do not own its state.

For failed golden incidents, `scripts/cleanup_failed_golden.py --config CONFIG --report FAILED_CORRECTNESS_JSON --output NEW_RECEIPT` performs a dry run. After reviewing its exact IDs and business-key ownership, add `--apply` with a new output path to cancel only those active instances. The tool saves diagnostics first, checks endpoint/image ownership, rechecks each live business key before cancellation and preserves history. It does not reset completed engine history or the simulator ledger; do not use it as the baseline reset hook.

## Deployment and evidence commands

After provisioning, `configure --config config.locked.local.json --environment dist/environment.local.json --output config.environment.local.json` fills verified database TLS, private endpoints and registry destinations. Build/publish that configuration on the OCI controller. The image includes a checksum-pinned Java agent. Supply separate Vault references for application-admin authentication and the simulator token.

From the controller, `deploy --config config.ready.json --all-hosts dist/environment.local.json` installs support/collector, application, worker and generator tooling on the explicit private hosts using verified SSH keys. Follow [remote deployment](remote-deployment.md); single-host role commands below remain available. Select `runtime.allow_insecure_http=true` explicitly for the isolated HTTP lab, or configure HTTPS. Set `runtime.db_user`/`db_password_secret_id` for a separately provisioned application principal; the default isolated lab uses the configured database administrator and must disclose that boundary.

Before performance measurements:

1. Install `requirements-database.txt`; run `database-check --config config.ready.json --output database.json` on the controller or app host. It records actual SQL version, schema presence, commit/rollback and a fresh connection. Set `evidence.database_file` to this report.
2. Deploy BPMNs, then run `golden --config config.ready.json --output runs/golden-CAMPAIGN`. Copy needed evidence to the generator. Freeze the passing report path/hash in `evidence.golden_file`/`golden_sha256`, and copy its exact `scope.database_product`/`database_version` into `comparison.environment`. A changed image/database invalidates the gate. Golden reports bind to the configured database target fingerprint.
3. Configure telemetry sources (retained in the private research archive). The Linux generator performs measured calibration and host sampling; it cannot infer complete app/database telemetry. Use the executable `scripts/k6-container.sh` as `execution.k6_binary` with the pinned k6 image. Keep output under the workspace. Run smoke, discovery, then frozen-rate repetitions.

`preflight --online` checks shapes and `oci.quota_checks` through OCI Limits. Each check specifies `category` (`compute`, `database`, `storage`, `network`), tenancy-specific `service`/`limit`, `scope` (`AD` or `REGION`) and positive `required` quantity. `provision --apply` rejects incomplete categories or insufficient availability. Discover exact limit names in the tenancy; available quota does not reserve capacity.

`cost-import billing.csv --mapping configs/cost-mapping.example.json --output costs/IMPORT_ID` preserves source records and missing fields in a FOCUS 1.3-aligned subset. It does not claim certified conformance. Reconcile all campaign costs before marking actual allocations complete. `report RUN_DIR --parquet` adds Parquet. `archive RUN_DIR --output archives/RUN_ID.zip` creates an immutable local archive; adding `--config config.ready.json` uploads it to the retained bucket and writes a receipt. Flush/archive the separate OTLP files before teardown.

## First useful result

The initial target is one Fluxnova instance, PostgreSQL 17, and three processes: minimal start/end, Boolean/variable handling, and asynchronous service work. Measure API acknowledgement separately from verified workflow completion. Then compare VM/OKE, PostgreSQL/Autonomous, and released/development builds one factor at a time.

The Python CLI requires Python 3.12+. Its core analysis/reporting and unit tests use the standard library. Run from this repository:

```bash
python -m unittest discover -s tests -v
python -m benchlib.cli demo
python -m benchlib.cli preflight --config configs/oci.example.json
```

`demo` creates explicitly synthetic evidence including an unresolved request and generates HTML, Markdown, JSON and CSV. The last command **must fail** until the OCI configuration is filled in. Reports are offline/reproducible; no cloud account is required to inspect evidence.

PowerShell users can set `$env:BENCH_PYTHON` to their Python executable and use `./bench.ps1`. Linux users can use `bash bench.sh`. `python -m benchlib.cli --help` lists the complete command surface.

## OCI bring-up

This sequence creates a new Resource Manager deployment. For the existing local deployment, follow the resume section above and skip new stack provisioning.

1. Copy `configs/oci.example.json` to the ignored `config.local.json`. Fill region, compartment, availability domain, image OCID, a campaign cost estimate and spending limit, the regional PostgreSQL shape, SSH public key and Vault secret references. Follow [OCI infrastructure instructions](oci.md) for prerequisite IAM and private access. Do not put passwords or private keys in configuration.
2. Freeze artifacts. On 2026-09-29 the public Docker tag `finos/fluxnova-bpm-platform:3.0.1` returned HTTP 404, while the Maven Central 3.0.1 distribution was available. Use the **explicit** distribution-packaging path:

   ```bash
   python -m benchlib.cli lock --source maven-distribution --config config.local.json --output config.locked.local.json
   ```

   This records the distribution checksum and base-image digests. The upstream source SHA is a reference, not proof of the published binary's build commit. `--source registry` remains available and fails rather than substituting another release when the requested tag is missing.
3. Validate and package the Resource Manager stack; install the cloud SDK on the controller/bootstrap environment:

   ```bash
   python -m pip install -r requirements-cloud.txt
   python -m benchlib.cli preflight --config config.locked.local.json
   python -m benchlib.cli package
   python -m benchlib.cli provision --config config.locked.local.json
   python -m benchlib.cli provision --config config.locked.local.json --apply
   ```

   `provision` creates a Resource Manager PLAN by default. The second invocation reuses that stack and applies that plan. The ZIP can alternatively be uploaded in the OCI console. Bootstrap preflight accepts a checksum-pinned build recipe; benchmark execution requires the finished container digest. Shape discovery does not guarantee quota/capacity reservation.
4. Place this repository on the private controller. Use instance-principal IAM on Compute; `config_file` is supported for an operator machine and `resource_principal` for environments that actually provide it. Authenticate Docker to the provisioned OCIR repository using your approved credential mechanism. Set `artifact.publish_repository` to that repository and run:

   ```bash
   python -m benchlib.cli build --config config.locked.local.json --publish
   ```

   The build verifies the downloaded distribution, builds runtime/support images, publishes them, and produces `dist/build-*/config.ready.json` containing immutable image references. `--native` additionally runs the pinned source build/test script; it is separately evidenced and not silently substituted for the packaged artifact.
5. Populate the ready configuration's private endpoints and database connection details. On the respective private hosts, use `deploy --runtime --role app`, `--role support`, and `--role worker`. Runtime database credentials are read from OCI Vault. PostgreSQL uses verified TLS; the CA is mounted at `/run/db-ca.pem`, and the JDBC URL must reference it. Application REST credentials use a separate Vault secret. HTTPS termination or explicitly documented isolated-lab HTTP is an environment setting. These commands execute on the host where invoked; they do **not** silently SSH to other machines.
6. On the load-generator host, point the configuration to the private engine REST endpoint, set the credential environment variables, and deploy/seed/run:

   ```bash
   python -m benchlib.cli deploy --config config.ready.json
   python -m benchlib.cli seed --config config.ready.json --count 10
   python -m benchlib.cli campaign --config config.ready.json --profile smoke
   python -m benchlib.cli campaign --config config.ready.json --profile discovery
   ```

   Keep the load generator separate from application/mock/telemetry hosts. Record calibration and application telemetry evidence. Missing evidence makes a run invalid or inconclusive, never a performance pass.
7. Select a fixed arrival rate from discovery and run `--profile baseline` (five repetitions). Supply `execution.reset_command` as an argv array that restores identical seeded state **before every repetition**. The [controller checkpoint procedure](state-checkpoints.md) includes a bounded broker connecting the remote load-generator hook to the verified PostgreSQL and simulator restore. Start a fresh broker session with enough requests and lifetime for the campaign, and pin its checkpoint hashes. The measured rate is not automatically raised after a failure. Cost-estimate duration must cover the full campaign. Soak and acceptance profiles are eight and 24 hours respectively.
8. Regenerate or verify a saved run:

   ```bash
   python -m benchlib.cli report runs/RUN_ID
   python -m benchlib.cli verify runs/RUN_ID
   ```

   JSON/CSV/HTML/Markdown need no extra packages; Parquet is an explicit optional export. Preserve all failed/unknown work and all associated costs. `verify`, `run`, `campaign` and `compare` return nonzero for incomplete or failed evidence. A generated report is not itself a passing result.
9. Archive evidence before teardown. `destroy` previews the exact saved stack; `destroy --apply` targets only that stack. Follow the evidence-retention instructions in the OCI guide. OCI budget alarms are soft limits; bounded run durations and explicit teardown remain necessary.

## Evidence and comparison

Each run stores an immutable manifest, lifecycle events, k6 summary, costs, execution logs, and checksums. Source/workload hashes distinguish changed tests from changed systems. Full IDs belong in evidence and traces, not high-cardinality metric labels. See [measurement contract](measurement-contract.md).

`execution.evidence_command` can invoke a collector with `{run_id}` and `{run_dir}` placeholders after each run. `evidence.generator_validation_file` and `evidence.telemetry_file` accept those placeholders too. Each evidence document must name the exact run and include `valid: true` only after actual validation. Never hand-edit a flag merely to obtain a passing result.

Use `compare --baseline RUN1 ... RUN5 --candidate RUN1 ... RUN5 --output comparisons/NAME` for paired independent runs. Pairing/randomization, reset and dataset identities must be recorded. Differences in durability, HA, topology, CPU generation and price basis must be disclosed; equal CPU counts do not prove hardware equivalence.

## Workload and cloud extensions

See [workload documentation](workloads.md) for executable coverage and golden cases. KYC, risk, settlement, payment simulators and deterministic model behavior are distinct from a live OCI model integration. PAY cases requiring node loss or database failover need the corresponding infrastructure lane. An unsupported capability is not a successful test.

OKE, Oracle database compatibility, browser performance, private model control, and live OCI model evaluation are separate stages with explicit feasibility/coverage gates. The existing ORB checkout is outside this benchmark.
