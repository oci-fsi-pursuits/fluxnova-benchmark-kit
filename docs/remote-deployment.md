# Install the private reference cell

`bench deploy --config config.ready.json --all-hosts environment.json` runs from the private controller. It uses the saved Terraform `environment_contract`, explicit private IPv4 addresses and the controller's existing SSH identity. The inventory's region and compartment must match the reviewed campaign. This command completed against the Phoenix OCI cell on 13 October 2026.

For a worker-only experiment, set optional `artifact.worker_image` to the published worker digest. The simulator still uses `artifact.services_image`; the app uses `artifact.image`. Remote preparation validates the optional digest, and the manifest retains all image identities. Existing containers are never implicitly replaced by `deploy_role`: stop/archive an idle worker explicitly and retain a stopped rollback container with automatic restart disabled before deploying its replacement. Revalidate golden cases before load testing a changed worker.

When `artifact.worker_image` is configured, the runner also requires that digest in the pinned golden report (either its explicit worker image or shared services image). A missing or different worker identity fails golden validation. On the prepared load generator, `config.worker-pacing.local.json` and the updated `config.telemetry.local.json` identify the tested worker and its fresh golden evidence. Restart the bounded metrics publisher before another run; it is stopped after testing.

## Continue the prepared Phoenix controller

On 12 October 2026, committed source `d461007` was copied to `/opt/fluxnova-bench/workspace/release-d461007` as a SHA-256-verified archive. The project and pinned cloud/database dependencies were installed in `/opt/fluxnova-bench/venv`; the CLI help command passed. Configuration and the public PostgreSQL CA are under `dist/setup/` in that release directory. `config.environment.local.json` contains the resolved endpoints and registry destination. These deployment-specific files are ignored by Git.

The controller verified the PostgreSQL service certificate and hostname using TLS 1.3, fetched the pinned administrator secret version through its instance principal, and authenticated to PostgreSQL 17.6 with `read committed` isolation. No engine tables existed in the public schema, and no schema was modified. The readiness receipt is `dist/setup/readiness.local.json`; a workstation copy is under `dist/controller-setup-20261012/`.

Progress on 13 October 2026:

- A dedicated controller Ed25519 key now works against app, support and loadgen with verified host keys. Its authorized-key entries restrict use to the controller's private IP and disable forwarding/PTY features. The private key remains on the controller.
- Three application secrets are ACTIVE in the existing Vault. App can retrieve its DB and administrator secrets; support and loadgen can retrieve their required administrator/simulator secrets. These successful access checks are not an audit of every existing IAM grant.
- The `fluxnova_app` PostgreSQL role has no superuser, create-database, create-role or replication privileges. Its default schema is `fluxnova`; the released engine and identity scripts created 49 tables there. The application-principal database check passed schema presence, commit, rollback and fresh connection checks over verified TLS.
- The released engine's compiled `ProcessEngine.VERSION` and SQL resources both use internal schema version `fox`, while REST release evidence is 3.0.1. The SQL scripts were applied unchanged after checking this against the binary and checksum-verified [3.0.1 engine sources](https://repo.maven.apache.org/maven2/org/finos/fluxnova/bpm/fluxnova-engine/3.0.1/fluxnova-engine-3.0.1-sources.jar). Do not rewrite that marker to the release number merely to satisfy a check.
- Both images were published to private OCIR. The publishing build produced controller `dist/build-1791915749233767336/config.ready.json`, with digest-pinned runtime and support images.
- At the operator's explicit request, the old `OIC` auth token was replaced. The `kove` token was preserved. The replacement is retained in Vault as `fluxnova-bench-ocir-token`; Docker authentication succeeded on controller, app and support without writing the token to Git or local helper files.
- The deployment inventory is controller `dist/remote-0535c10bb91142549818ac01c0665ef1/inventory.json`. The app, support and loadgen release directory is `/opt/fluxnova-bench/workspace/release-0535c10bb91142549818ac01c0665ef1`. Fluxnova started against managed PostgreSQL; authenticated readiness and collector health passed, and unauthenticated engine access was rejected.
- The [web UI](web-ui.md) is reachable through a localhost-only controller SSH forward. Follow-up telemetry tests and a bounded async sweep passed 520 measured workflows. The dedicated metrics role, controller publisher, collector export and CSRF-protected web login were verified; see follow-up evidence (retained in the private research archive).
- A conservative USD 50 estimate covers the first 48 hours from 2026-10-13 00:30 UTC, within the existing USD 100 budget. Its public-price components total approximately USD 33.08, with the remainder reserved for overhead/uncertainty. This is a planning estimate, not billing evidence or a hard cap; revise it before retaining the cell beyond 2026-10-15 00:30 UTC. Source prices and storage assumptions are saved in controller `dist/setup/cost-estimate.local.json`, using the [Oracle public USD price feed](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?currencyCode=USD).

The deployment-specific receipts are under workstation `dist/setup-20261013/` and controller `dist/setup/`. The steps below remain the reference procedure for a new cell. This cell already has running containers; do not replay deployment over them. Follow the validation status before advancing to measured campaigns.

### 1. Connect and prepare deployment SSH access

Start the [Bastion forwarder](oci.md#forward-controller-ssh-through-oci-bastion) on the workstation and use its printed SSH command. On the controller:

```bash
cd /opt/fluxnova-bench/workspace/release-d461007
source /opt/fluxnova-bench/venv/bin/activate
umask 077
test -f ~/.ssh/id_ed25519 || ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519 -C fluxnova-controller-deploy
cat ~/.ssh/id_ed25519.pub
```

Add this **public** key to the `ubuntu` user's `~/.ssh/authorized_keys` on app, support and loadgen using the existing workstation administrative path. Preserve existing keys. Verify each host's SSH fingerprint through that trusted path and record the matching private-IP host key in the controller's `~/.ssh/known_hosts`. Use the addresses in `dist/setup/environment.controller.local.json`, then confirm `ssh -o BatchMode=yes -o StrictHostKeyChecking=yes ubuntu@HOST_PRIVATE_IP true` succeeds for all three. Keep the controller private key on the controller; do not copy the workstation private key there.

### 2. Authenticate the private registry

Create or use an OCI auth token for a user permitted to push to the existing runtime repository. On the controller, run `docker login ocir.us-phoenix-1.oci.oraclecloud.com`. Enter the namespace-qualified username and paste the auth token at the password prompt. For federated identities the username includes the identity domain. Configure registry pull credentials on the app and support hosts as well; the deployment command does not distribute Docker credentials. Never put the token in campaign JSON, a command-line password argument or Git. See Oracle's [auth-token instructions](https://docs.oracle.com/en-us/iaas/Content/Registry/Tasks/registrygettingauthtoken.htm) and [Docker login format](https://docs.oracle.com/en-us/iaas/Content/Registry/Tasks/registrypullingimagesusingthedockercli.htm).

### 3. Finish secrets, database and runtime configuration

Create separate Vault secrets for the Fluxnova administrator password, simulator token and application database password. Populate `runtime.admin_password_secret_id`, `runtime.support_token_secret_id`, `runtime.db_password_secret_id` and `runtime.db_user` in `config.environment.local.json`. Create the corresponding PostgreSQL application role and schema using the release's PostgreSQL scripts; the credential reference alone does not create a database user. Keep the database administrator for bootstrap and use the application principal for runtime.

Verify Vault access under each host's instance principal: app needs its database and application-admin secrets; support needs the simulator and application-admin secrets; controller needs the secrets used by deployment health checks and database verification. Arrange application/simulator credentials for the load generator without granting it the database administrator secret. These required secret-access checks passed for the deployed cell.

The controller's `GetConnectionDetails` call currently returns `404 NotAuthorizedOrNotFound`. The operator retrieved the service FQDN/CA, so the prepared configuration does not depend on that call. For future controller-side discovery, grant its narrowly scoped dynamic group `read postgres-db-systems` in the database compartment; Oracle lists `GetConnectionDetails` under that permission in its [PostgreSQL policy reference](https://docs.oracle.com/en-us/iaas/Content/postgresql/policies.htm).

For this private HTTP lab, explicitly set `runtime.allow_insecure_http` to `true`, or prepare HTTPS endpoints and certificates. Also fill the campaign cost estimate and duration before running preflight/campaign commands. Keep the already verified database TLS settings unchanged. These values are populated in this cell's prepared configuration.

### 4. Lock, build and deploy

After completing the prerequisites, run on the controller from the release directory with its virtual environment active:

```bash
python -m benchlib.cli lock --source maven-distribution --config config.environment.local.json --output config.locked.local.json
python -m benchlib.cli build --config config.locked.local.json --publish
```

The build prints the exact `dist/build-*/config.ready.json` it produced. Use that file, not an older build or an ambiguous wildcard:

```bash
python -m benchlib.cli deploy --config dist/build-BUILD_ID/config.ready.json --all-hosts dist/setup/environment.controller.local.json
```

Replace `BUILD_ID` with the printed directory ID. This builds and deploys the existing private cell; do not run Resource Manager provisioning again. Then follow the database-check, golden-suite and smoke sequence below and in the [campaign runbook](campaign-runbook.md#deployment-and-evidence-commands). A successful database login alone is not an application compatibility result.

## Deployment requirements and behavior

Before running it, establish and independently verify each host's SSH fingerprint in the controller's `known_hosts`. The harness requires `BatchMode=yes` and `StrictHostKeyChecking=yes`; it never accepts an unknown host automatically. Private routing and the reviewed NSGs must already allow the controller's SSH access. Authenticate Docker to the private image registry on each applicable host using the approved operator process; this command does not transmit registry passwords.

Populate the configuration with published digest-pinned engine, services, k6 and collector images, the bundled Java-agent receipt, database JDBC endpoint and an explicit public database CA file on the controller. Keep passwords in Vault; configuration carries secret OCIDs and environment-variable names. The database JDBC URL must use verified TLS. Additional explicit public application/support CA files are copied when configured. Runtime URLs are taken from the corresponding inventory role; remote OCI SDK calls use instance principals. Private HTTP requires the explicit isolated-lab flag. Grant each role only the Vault and image access it needs before deployment.

The command checks cloud-init completion and the bootstrap marker, copies a checksum-verified source ZIP, installs the pinned Python requirements in the host's precreated virtual environment, and extracts into a new `/opt/fluxnova-bench/workspace/release-<id>` directory. It deploys support and its collector, waits for their HTTP health endpoints, deploys the app and waits for authenticated REST readiness, then starts the worker on the support host. It prepares the separate load generator and pulls the pinned k6 image. Existing named containers cause deployment to stop; the harness does not replace an active environment.

The support service and worker share the labelled Docker bridge `fluxnova-benchmark-services`. For the built-in HTTP simulator, remote preparation sets `runtime.worker_support_url` to `http://benchmark-support:8090`; the app and load generator retain the support VM's private endpoint. This avoids the OCI host firewall's rejection of container traffic through the same host's published port. Existing unrelated Docker networks are rejected. HTTPS configurations retain their configured certificate hostname.

The command preserves a local `dist/remote-<id>/inventory.json`, its source archive and per-step logs. A failed step stops subsequent deployment while preserving existing hosts, containers and evidence. A retry is a new deployment attempt; inspect the failure and explicitly resolve any existing containers first. Health flags prove bootstrap and endpoint readiness, not workflow correctness, telemetry completeness or benchmark validity.

After deployment, use the saved inventory's load-generator target and release directory. Export `BENCH_K6_IMAGE` to the recorded digest reference and obtain `BENCH_USERNAME`, `BENCH_PASSWORD` and `BENCH_SUPPORT_TOKEN` through the approved secret retrieval process. Run the database compatibility check on the controller, then copy that evidence to the load generator and set the local evidence path in its configuration. Deploy the BPMNs and run the golden suite from the load generator before a campaign. Complete calibration, telemetry collection, reset and cost evidence for comparison runs. The deployment command deliberately does not start a load campaign.

For database and simulator state reset, use the [controller checkpoint procedure and reset broker](state-checkpoints.md). Keep database credentials on the controller and start a bounded broker session before the remote campaign. The hook uses a private request directory reached through the controller's existing SSH access.

Archive each host's bootstrap/version receipts together with the deployment inventory and campaign evidence. For manual execution on the load generator, the pattern is:

```sh
cd /opt/fluxnova-bench/workspace/release-<id>
export BENCH_K6_IMAGE='registry/image@sha256:<digest>'
/opt/fluxnova-bench/venv/bin/python -m benchlib.cli deploy --config config.ready.json
/opt/fluxnova-bench/venv/bin/python -m benchlib.cli golden --config config.ready.json --output runs/golden-<id>
# Pin the successful golden evidence and provide calibration/telemetry/reset inputs first.
/opt/fluxnova-bench/venv/bin/python -m benchlib.cli campaign --config config.ready.json --profile smoke
```
