# OCI quick functional lab

Terraform creates **one private Ubuntu 24.04 x86 VM**, a **new VCN**, two private subnets, NAT for downloads, and OCI Bastion. Cloud-init installs checksum-pinned Fluxnova 3.0.1 on Java 21/H2, listening only on VM loopback port 8080. There is no public IP, managed database, Vault or autoscaling.

This new template has been Terraform-validated, not freshly applied in OCI. Cloud bootstrap remains to be qualified. It is a functional demonstration environment, separate from the Compute/PostgreSQL benchmark topology.

## Deploy

Use Terraform 1.5+, an existing OCI identity authorized for Compute/VCN/NAT/Bastion, and regional identifiers for a supported shape, Ubuntu image and AD. Copy and fill the example:

```powershell
Copy-Item providers/oci/terraform.example.tfvars providers/oci/terraform.tfvars
terraform -chdir=providers/oci init
terraform -chdir=providers/oci plan -out=lab.tfplan
terraform -chdir=providers/oci apply lab.tfplan
terraform -chdir=providers/oci output -json environment_contract | Set-Content -Encoding utf8 providers/oci/environment.local.json
```

Use your current public IPv4 `/32` in `bastion_client_cidrs`. `auth = "SecurityToken"` and `config_file_profile` select an **existing dedicated profile**; nothing edits `~/.oci/config`. See [Oracle's authentication documentation](https://docs.oracle.com/en-us/iaas/Content/dev/terraform/configuring.htm). Never place private keys or tokens in tfvars.

Default sizing is 1 OCPU/8 GB and a 50 GB boot disk. Regional availability/shape limits apply. Idle resources can incur charges; stopping a test is not teardown and there is no automatic billing cutoff.

## Reach the engine

With Python, the OCI SDK and OpenSSH, from the checkout root:

```powershell
python scripts/bastion_forward.py --contract providers/oci/environment.local.json --profile YOUR_PROFILE --ttl 3600
```

Use the printed SSH command, then check on the VM:

```bash
sudo cloud-init status --wait
sudo systemctl status fluxnova-lab --no-pager
curl --fail http://127.0.0.1:8080/engine-rest/version
```

The admin ID is `lab`. Retrieve its generated password over the trusted SSH session; it never enters Terraform state/output:

```bash
sudo python3 -c 'import json; print(json.load(open("/etc/fluxnova-lab/engine.yml"))["fluxnova.bpm"]["admin-user"]["password"])'
```

Keep it out of transcripts/Git. In another local terminal, reuse the helper's inner SSH command with the same key, port and HostKeyAlias, adding `-N -T -L 127.0.0.1:18080:127.0.0.1:8080` before `ubuntu@127.0.0.1`. Keep both tunnels open. Follow [existing-engine setup](../existing/README.md) with `http://127.0.0.1:18080/engine-rest`, then launch with `--allow-deploy`.

The quick stack's output supports the Bastion helper only, not formal four-role configure/deploy commands. Terraform completion is not proof of runtime readiness; check cloud-init and the engine explicitly.

## Remove the lab

Export results, close the UI/tunnels, then review and apply a destroy plan from this root/state:

```powershell
terraform -chdir=providers/oci plan -destroy -out=destroy.tfplan
terraform -chdir=providers/oci apply destroy.tfplan
```

H2 on the VM's disk is disposable. This stack has independent state; never point it at the research stack's state. Durable controlled studies use [infra/oci-reference](../../infra/oci-reference) and the [campaign runbook](../../docs/campaign-runbook.md).
