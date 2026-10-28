locals {
  bastion_enabled = length(var.bastion_client_cidrs) > 0
}

# A separate private subnet keeps Bastion egress rules off the compute VNICs.
resource "oci_core_security_list" "bastion" {
  count          = local.bastion_enabled ? 1 : 0
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.bench.id
  display_name   = "${var.name_prefix}-bastion"
  freeform_tags  = local.tags
  egress_security_rules {
    protocol         = "6"
    destination      = "${oci_core_instance.role["controller"].private_ip}/32"
    destination_type = "CIDR_BLOCK"
    stateless        = false
    tcp_options {
      min = 22
      max = 22
    }
  }
}

resource "oci_core_subnet" "bastion" {
  count                      = local.bastion_enabled ? 1 : 0
  compartment_id             = var.compartment_id
  vcn_id                     = oci_core_vcn.bench.id
  cidr_block                 = cidrsubnet(var.vcn_cidr, 8, 2)
  display_name               = "${var.name_prefix}-bastion"
  dns_label                  = "bastion"
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.bastion[0].id]
  freeform_tags              = local.tags
}

resource "oci_bastion_bastion" "controller" {
  count                        = local.bastion_enabled ? 1 : 0
  bastion_type                 = "STANDARD"
  compartment_id               = var.compartment_id
  target_subnet_id             = oci_core_subnet.bastion[0].id
  name                         = "${replace(var.name_prefix, "-", "")}bastion"
  client_cidr_block_allow_list = sort(tolist(var.bastion_client_cidrs))
  max_session_ttl_in_seconds   = 3600
  freeform_tags                = local.tags
}

resource "oci_core_network_security_group_security_rule" "bastion_ssh" {
  count                     = local.bastion_enabled ? 1 : 0
  network_security_group_id = oci_core_network_security_group.role["controller"].id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "${oci_bastion_bastion.controller[0].private_endpoint_ip_address}/32"
  source_type               = "CIDR_BLOCK"
  stateless                 = false
  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }
}
