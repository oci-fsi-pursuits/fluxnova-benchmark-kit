# Choose where Fluxnova runs

Provisioning creates infrastructure. Connecting reuses an engine that already exists.

| Path | What it does | Validation |
| --- | --- | --- |
| [Local](local/README.md) | Starts an isolated Java/H2 engine and clickable lab | Real engine and browser smoke tests |
| [OCI](oci/README.md) | Terraform creates one private VM, a new VCN, NAT and Bastion; cloud-init installs Fluxnova 3.0.1/H2 | Terraform validated; fresh cloud apply/bootstrap remains |
| [Existing engine](existing/README.md) | Connects through HTTPS or SSH; creates no infrastructure | Tested with an independently started local Fluxnova 3.0.1 engine |
| AWS, Azure, GCP, on premises | Existing-engine mode when a compatible engine is already running | No dedicated provisioning modules or cross-cloud qualification yet |

The single-VM OCI lab is for learning and small functional tests. Controlled cloud studies use the existing [four-role Compute/PostgreSQL stack](../infra/oci-reference) and [runtime deployment guide](../docs/remote-deployment.md). This provider directory does not move that stack's state or change its resources.

To add another provider, contribute `providers/<name>/` with pinned Terraform providers, credential-free input examples, network/access instructions, health checks and teardown. Supply a reachable endpoint using the common `existing-engine` connection file. Qualify provision, bootstrap, manual workflow, export and cleanup before claiming support. BPMN workers, Java delegates, secrets, model endpoints and external integrations still need their own adapters; Terraform alone does not make those integrations portable.
