# Quick functional lab. Formal studies keep using infra/oci-reference.
locals {
  tags = { managed_by = "fluxnova-benchmark-lab", purpose = "functional-exploration" }
}
resource "oci_core_vcn" "lab" {
  compartment_id = var.compartment_id
  cidr_block     = var.vcn_cidr
  display_name   = var.name_prefix
  dns_label      = "fluxlab"
  freeform_tags  = local.tags
}
resource "oci_core_nat_gateway" "lab" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.lab.id
  display_name   = "${var.name_prefix}-egress"
  freeform_tags  = local.tags
}
resource "oci_core_route_table" "lab" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.lab.id
  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_nat_gateway.lab.id
  }
}
resource "oci_core_security_list" "empty" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.lab.id
  display_name   = "${var.name_prefix}-nsg-only"
}
resource "oci_core_subnet" "app" {
  compartment_id             = var.compartment_id
  vcn_id                     = oci_core_vcn.lab.id
  cidr_block                 = cidrsubnet(var.vcn_cidr, 8, 0)
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.lab.id
  security_list_ids          = [oci_core_security_list.empty.id]
  dns_label                  = "app"
}
resource "oci_core_network_security_group" "app" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.lab.id
  display_name   = "${var.name_prefix}-app"
}
resource "oci_core_network_security_group_security_rule" "packages" {
  for_each                  = toset(["80", "443"])
  network_security_group_id = oci_core_network_security_group.app.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = tonumber(each.value)
      max = tonumber(each.value)
    }
  }
}
resource "oci_core_instance" "app" {
  compartment_id      = var.compartment_id
  availability_domain = var.availability_domain
  display_name        = var.name_prefix
  shape               = var.compute_shape
  shape_config {
    ocpus         = var.ocpus
    memory_in_gbs = var.memory_gb
  }
  create_vnic_details {
    subnet_id        = oci_core_subnet.app.id
    assign_public_ip = false
    nsg_ids          = [oci_core_network_security_group.app.id]
    hostname_label   = "fluxnova"
  }
  source_details {
    source_type             = "image"
    source_id               = var.image_ocid
    boot_volume_size_in_gbs = 50
  }
  metadata = {
    ssh_authorized_keys = var.ssh_authorized_key
    user_data           = base64encode(file("${path.module}/cloud-init.yaml"))
  }
  freeform_tags = local.tags
}
resource "oci_core_security_list" "bastion" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.lab.id
  egress_security_rules {
    protocol    = "6"
    destination = "${oci_core_instance.app.private_ip}/32"
    tcp_options {
      min = 22
      max = 22
    }
  }
}
resource "oci_core_subnet" "bastion" {
  compartment_id             = var.compartment_id
  vcn_id                     = oci_core_vcn.lab.id
  cidr_block                 = cidrsubnet(var.vcn_cidr, 8, 1)
  prohibit_public_ip_on_vnic = true
  security_list_ids          = [oci_core_security_list.bastion.id]
  dns_label                  = "bastion"
}
resource "oci_bastion_bastion" "lab" {
  compartment_id               = var.compartment_id
  bastion_type                 = "STANDARD"
  target_subnet_id             = oci_core_subnet.bastion.id
  name                         = "${replace(var.name_prefix, "-", "")}bastion"
  client_cidr_block_allow_list = sort(tolist(var.bastion_client_cidrs))
  max_session_ttl_in_seconds   = 3600
  freeform_tags                = local.tags
}
resource "oci_core_network_security_group_security_rule" "ssh" {
  network_security_group_id = oci_core_network_security_group.app.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "${oci_bastion_bastion.lab.private_endpoint_ip_address}/32"
  source_type               = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }
}
