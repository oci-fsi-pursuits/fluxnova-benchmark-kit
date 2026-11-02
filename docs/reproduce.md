# Reproduce the workflow benchmark

Start here from the presentation QR. Repository: [oci-fsi-pursuits/fluxnova-benchmark-kit](https://github.com/oci-fsi-pursuits/fluxnova-benchmark-kit).

**Same workload, declared environment, independently checked outcomes.** Reusing the build on another platform does not make its results directly comparable to the OCI reference. Keep model calls and reviewer wait separate from deterministic load.

## 1. Try the reports in two minutes

Git and Python 3.12+ are enough. From the repository root:

```bash
git clone https://github.com/oci-fsi-pursuits/fluxnova-benchmark-kit.git
cd fluxnova-benchmark-kit
python -m benchlib.cli demo
```

Open the printed HTML report. This creates labelled synthetic harness data and demonstrates reporting. It does not start Fluxnova or measure its performance.

## 2. Build the runtime without an OCI account

Use a Linux container host with Docker, Git, Python 3.12+ and access to the public artifact registries. A Linux VM in another cloud or on premises can provide the host. Match CPU architecture to the resolved images. Windows/macOS need a Linux container runtime.

```bash
python -m benchlib.cli lock --source maven-distribution --config configs/local-functional.example.json --output config.portable.local.json
python -m benchlib.cli build --config config.portable.local.json
```

These commands resolve and record artifact hashes and build the local images `fluxnova-benchmark-runtime:3.0.1` and `fluxnova-benchmark-services:local`. They do not provision cloud resources or require publishing to OCIR. Keep the generated lock and build receipts. A fresh resolution may select different base-image digests from the historical OCI campaign; archive the exact lock used for each new experiment. `--publish` currently targets OCIR and is not needed for a local build. Push/tag/export through your chosen registry separately, retaining the resulting digest.

For an isolated functional lab, start the runtime with a loopback-only port and the distribution's embedded H2 database:

```bash
docker run --rm --name fluxnova-functional -p 127.0.0.1:18080:8080 -e JAVA_OPTS="-Xms512m -Xmx1g" fluxnova-benchmark-runtime:3.0.1
```

This is an ephemeral demo configuration, with the distribution's `demo` / `demo` application login. It uses a different heap/database from the OCI reference. The deployment is reachable at `http://127.0.0.1:18080/fluxnova/app/tasklist/default/`. Wait for startup before using REST. Use distinct ports/names if a benchmark cell is already forwarded to this machine.

In a second terminal from the repo, start the synthetic tools:

```bash
python -m services.mock_service --host 127.0.0.1 --port 18081 --database runs/portable-support.sqlite
```

In a third terminal, set `BENCH_USERNAME=demo` and `BENCH_PASSWORD=demo` in that process environment and run:

```bash
python -m services.worker
```

Set those same two environment variables in the terminal used for deployment and checks. PowerShell uses `$env:BENCH_USERNAME='demo'`; Bash uses `export BENCH_USERNAME=demo`. Then:

```bash
python -m benchlib.cli seed --config config.portable.local.json --local
python -m benchlib.cli golden --config config.portable.local.json --local --output runs/portable-golden-001
```

Use a fresh golden output path. These are functional checks, not performance or production database evidence. `Ctrl+C` stops the foreground worker/support/runtime processes; the `--rm` runtime discards its container/H2 state. The support SQLite file remains under ignored `runs/`. The new Docker walkthrough is a documented reuse path, not a newly executed cross-cloud validation. Existing local Java/H2 and OCI/PostgreSQL evidence remain separately labelled.

## 3. Bring the workload to your platform

| Portable components | Platform work you supply |
| --- | --- |
| Fluxnova runtime container, Python external-task worker and synthetic tool service | Container/VM orchestration, private network paths, certificates and registry access |
| BPMN workloads and REST adapter | A compatible Fluxnova endpoint, authentication, database schema and supported persistence configuration |
| k6 workload, event ledger, correctness checks and offline reports | Generator headroom, application/database telemetry and matching resource allocations |
| Repeated-run measurement contract | State restore, declared durability, complete cost allocation and provider-specific failure procedures |

The repository's automated infrastructure provisioning and cloud integration are **OCI-specific**. There is no implemented AWS/Azure/on-prem provisioning switch. H2 demonstrations do not substitute for validating PostgreSQL or another production database. Vault-backed payment authority/recovery also requires platform-specific secret and identity integration.

For a comparable performance study, keep the BPMN/data, observation interval, application images, resource limits, state reset, instrumentation and database durability fixed except for the declared treatment. Record host/container OS, JDK, Spring/Fluxnova versions and topology. Change one factor, retain failures and compare repeated matched runs. See the [measurement contract](measurement-contract.md), experiment register (retained in the private research archive) and [OCI reference procedure](campaign-runbook.md).

The existing non-OCI CLI `--local` path labels runs as local functional validation. A measured non-OCI campaign needs an appropriate environment/telemetry/cost adapter and evidence qualification. Do not remove that label to create a cloud performance claim.

## Presentation and demo

The [15-minute agenda and talk track](talk-15-minutes.md) identify the three comparisons and their limits. The Tasklist demo guide (retained in the private research archive) provides a visible scripted approval and an optional browser recording. That richer payment demo uses the already prepared OCI cell and its verifier; it is separate from the portable H2 functional lab above.
