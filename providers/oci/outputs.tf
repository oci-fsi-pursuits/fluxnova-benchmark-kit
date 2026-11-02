output "environment_contract" {
  description = "Private inventory compatible with scripts/bastion_forward.py. No passwords."
  value = {
    schema_version = "1.0"
    cloud          = "oci"
    topology       = "single-vm-functional-lab"
    region         = var.region
    bastion = {
      id                  = oci_bastion_bastion.lab.id
      private_endpoint_ip = oci_bastion_bastion.lab.private_endpoint_ip_address
      client_cidrs        = sort(tolist(var.bastion_client_cidrs))
      max_session_ttl     = 3600
      target_port         = 22
    }
    roles = {
      controller = { private_ip = oci_core_instance.app.private_ip, user = "ubuntu", ocpus = var.ocpus, memory_gb = var.memory_gb }
    }
    engine_port      = 8080
    bootstrap_status = "Check cloud-init status --wait and systemctl status fluxnova-lab; Terraform completion is not runtime readiness."
  }
}
