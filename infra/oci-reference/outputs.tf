output "environment_contract" {
  description = "Non-secret portable environment inventory. Addresses alone do not prove runtime readiness."
  value = {
    schema_version   = "1.0"
    cloud            = "oci"
    region           = var.region
    compartment_id   = var.compartment_id
    access_method    = "instance_principal"
    topology         = "compute-reference"
    budget_limit_usd = var.budget_limit_usd
    bastion = local.bastion_enabled ? {
      id                  = oci_bastion_bastion.controller[0].id
      private_endpoint_ip = oci_bastion_bastion.controller[0].private_endpoint_ip_address
      client_cidrs        = sort(tolist(var.bastion_client_cidrs))
      max_session_ttl     = oci_bastion_bastion.controller[0].max_session_ttl_in_seconds
      target_port         = 22
    } : null
    resource_ids = {
      vcn        = oci_core_vcn.bench.id
      instances  = { for role, instance in oci_core_instance.role : role => instance.id }
      database   = oci_psql_db_system.bench.id
      repository = oci_artifacts_container_repository.bench.id
    }
    roles = { for role, instance in oci_core_instance.role : role => {
      private_ip = instance.private_ip
      user       = "ubuntu"
      ocpus      = var.role_resources[role].ocpus
      memory_gb  = var.role_resources[role].memory_gb
    } }
    database = {
      engine          = "postgresql"
      major_version   = "17"
      host            = oci_psql_db_system.bench.network_details[0].primary_db_endpoint_private_ip
      port            = 5432
      admin_username  = var.db_admin_username
      admin_secret_id = var.db_admin_secret_id
      secret_version  = var.db_admin_secret_version
      database        = "postgres"
      tls_mode        = "verify-full"
    }
    artifacts = {
      namespace                  = data.oci_objectstorage_namespace.account.namespace
      bucket                     = data.oci_objectstorage_bucket.evidence.name
      retained_in_separate_stack = true
    }
    registry = {
      host       = "ocir.${var.region}.oci.oraclecloud.com"
      namespace  = data.oci_objectstorage_namespace.account.namespace
      repository = oci_artifacts_container_repository.bench.display_name
    }
    base_url         = "http://${oci_core_instance.role["app"].private_ip}:8080/engine-rest"
    support_url      = "http://${oci_core_instance.role["support"].private_ip}:8090"
    bootstrap_status = "Check /var/lib/cloud/instance/boot-finished and /opt/fluxnova-bench/bootstrap-complete on every role."
  }
}
