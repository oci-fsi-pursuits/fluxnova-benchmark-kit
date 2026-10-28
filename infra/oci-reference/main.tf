data "oci_objectstorage_namespace" "account" {
  compartment_id = var.compartment_id
}

data "oci_objectstorage_bucket" "evidence" {
  namespace = data.oci_objectstorage_namespace.account.namespace
  name      = var.artifact_bucket_name
}

resource "oci_artifacts_container_repository" "bench" {
  compartment_id = var.compartment_id
  display_name   = "${var.name_prefix}/runtime"
  is_public      = false
  # OCI rejects isImmutable on creation. Runtime images are pinned by digest.
  freeform_tags = local.tags
}

resource "oci_core_instance" "role" {
  for_each            = var.role_resources
  compartment_id      = var.compartment_id
  availability_domain = var.availability_domain
  display_name        = "${var.name_prefix}-${each.key}"
  shape               = var.compute_shape
  shape_config {
    ocpus         = each.value.ocpus
    memory_in_gbs = each.value.memory_gb
  }
  source_details {
    source_type             = "image"
    source_id               = var.image_ocid
    boot_volume_size_in_gbs = each.key == "controller" ? 100 : 50
  }
  create_vnic_details {
    subnet_id        = oci_core_subnet.private["compute"].id
    assign_public_ip = false
    hostname_label   = each.key
    nsg_ids          = [oci_core_network_security_group.role[each.key].id]
  }
  metadata = {
    ssh_authorized_keys = var.ssh_authorized_key
    user_data = base64encode(templatefile("${path.module}/cloud-init.yaml.tftpl", {
      role          = each.key
      package_specs = [for package in ["python3", "python3-venv", "docker.io", "docker-compose-v2", "git", "chrony"] : contains(keys(var.package_versions), package) ? "${package}=${var.package_versions[package]}" : package]
    }))
  }
  freeform_tags = merge(local.tags, { benchmark_role = each.key })
  depends_on    = [oci_core_network_security_group_security_rule.https_out, oci_core_network_security_group_security_rule.http_packages_out]
}

resource "oci_psql_db_system" "bench" {
  compartment_id              = var.compartment_id
  display_name                = "${var.name_prefix}-pg17"
  db_version                  = "17"
  shape                       = var.db_shape
  instance_count              = 1
  instance_ocpu_count         = var.db_ocpu_count
  instance_memory_size_in_gbs = var.db_memory_gb
  credentials {
    username = var.db_admin_username
    password_details {
      password_type  = "VAULT_SECRET"
      secret_id      = var.db_admin_secret_id
      secret_version = var.db_admin_secret_version
    }
  }
  network_details {
    subnet_id                      = oci_core_subnet.private["database"].id
    nsg_ids                        = [oci_core_network_security_group.role["database"].id]
    primary_db_endpoint_private_ip = cidrhost(oci_core_subnet.private["database"].cidr_block, 10)
  }
  storage_details {
    system_type           = "OCI_OPTIMIZED_STORAGE"
    is_regionally_durable = false
    availability_domain   = var.availability_domain
    iops                  = var.db_storage_iops
  }
  freeform_tags = local.tags
}
