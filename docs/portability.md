# Portability and supported paths

Reuse the workload and measurement design across environments, then implement and validate the chosen platform's integrations. This does not eliminate every provider dependency or all migration work.

| Component | Current support | Evidence or remaining work |
| --- | --- | --- |
| Offline reporting and correctness | Python 3.12+ from a checkout | Tests and saved evidence audits |
| BPMN, workers and synthetic services | Reusable source and containers | Local Java/H2 and OCI executions |
| Local Docker walkthrough | Documented functional path | Not a new cross-cloud performance validation |
| [Live lab](interactive-lab.md) | Real local engine or existing REST endpoint; BPMN upload, manual tasks, small runs | Local Java/H2/browser checks; not a formal benchmark |
| [OCI quick lab](../providers/oci/README.md) | One private VM/H2, new VCN/NAT and Bastion | Terraform validated; fresh cloud bootstrap remains |
| OCI reference | Terraform, build/deploy, private access, PostgreSQL and evidence storage | Four-host reference plus separate isolated studies |
| Isolated W01/W02 studies | Public runtime, protocols and auditors | Operator supplies owned database, identity/source manifest, access, reset and cleanup; not single-command provisioning |
| Other clouds and on premises | Portable application components | Supply provisioning, database/TLS, secrets, identity, telemetry, resets and cost adapters |
| Model and review integration | Separate constrained functional scenarios | No production/live-agent throughput or human productivity claim |

Match artifacts, inputs, worker counts, resource limits, polling, starting state and durability when comparing one change. Qualify generator, telemetry, correctness and resets in each environment. Different topologies remain separate studies until controls support a comparison.

OCI integrations include IAM, Vault, Bastion, Resource Manager/Terraform, Object Storage and usage attribution. Other providers' equivalents need adapters and validation; they are not currently interchangeable cloud implementations.
