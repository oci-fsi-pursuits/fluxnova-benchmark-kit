locals {
  tags = {
    benchmark           = var.name_prefix
    managed_by          = "fluxnova-benchmarks"
    campaign_budget_usd = tostring(var.budget_limit_usd)
  }
  roles = toset(["controller", "app", "loadgen", "support"])
}

resource "oci_core_vcn" "bench" {
  compartment_id = var.compartment_id
  cidr_block     = var.vcn_cidr
  display_name   = var.name_prefix
  dns_label      = "fluxbench"
  freeform_tags  = local.tags
}

resource "oci_core_nat_gateway" "egress" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.bench.id
  display_name   = "${var.name_prefix}-egress"
  freeform_tags  = local.tags
}

data "oci_core_services" "regional" {
  filter {
    name   = "name"
    values = ["All .* Services In Oracle Services Network"]
    regex  = true
  }
}

resource "oci_core_service_gateway" "services" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.bench.id
  display_name   = "${var.name_prefix}-services"
  services { service_id = data.oci_core_services.regional.services[0].id }
  freeform_tags = local.tags
}

resource "oci_core_route_table" "private" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.bench.id
  display_name   = "${var.name_prefix}-private"
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_nat_gateway.egress.id
  }
  route_rules {
    destination       = data.oci_core_services.regional.services[0].cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.services.id
  }
  freeform_tags = local.tags
}

# Empty security lists ensure the default VCN allow rules are not inherited.
resource "oci_core_security_list" "empty" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.bench.id
  display_name   = "${var.name_prefix}-nsg-only"
  freeform_tags  = local.tags
}

resource "oci_core_subnet" "private" {
  for_each                   = { compute = 0, database = 1 }
  compartment_id             = var.compartment_id
  vcn_id                     = oci_core_vcn.bench.id
  cidr_block                 = cidrsubnet(var.vcn_cidr, 8, each.value)
  display_name               = "${var.name_prefix}-${each.key}"
  dns_label                  = each.key
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.empty.id]
  freeform_tags              = local.tags
}

resource "oci_core_network_security_group" "role" {
  for_each       = setunion(local.roles, toset(["database"]))
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.bench.id
  display_name   = "${var.name_prefix}-${each.key}"
  freeform_tags  = local.tags
}

# Bootstrap and OCI service traffic only. NAT supplies outbound access, never inbound.
resource "oci_core_network_security_group_security_rule" "https_out" {
  for_each                  = local.roles
  network_security_group_id = oci_core_network_security_group.role[each.key].id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = 443
      max = 443
    }
  }
}

resource "oci_core_network_security_group_security_rule" "http_packages_out" {
  for_each                  = local.roles
  network_security_group_id = oci_core_network_security_group.role[each.key].id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = 80
      max = 80
    }
  }
}

locals {
  # Every connection has paired ingress/egress rules; replies use stateful tracking.
  connections = {
    controller_app_ssh     = { source = "controller", target = "app", port = 22 }
    controller_loadgen_ssh = { source = "controller", target = "loadgen", port = 22 }
    controller_support_ssh = { source = "controller", target = "support", port = 22 }
    controller_app_api     = { source = "controller", target = "app", port = 8080 }
    loadgen_app_api        = { source = "loadgen", target = "app", port = 8080 }
    support_app_api        = { source = "support", target = "app", port = 8080 }
    app_support            = { source = "app", target = "support", port = 8090 }
    controller_support     = { source = "controller", target = "support", port = 8090 }
    loadgen_support        = { source = "loadgen", target = "support", port = 8090 }
    app_db                 = { source = "app", target = "database", port = 5432 }
    controller_db          = { source = "controller", target = "database", port = 5432 }
    app_otel               = { source = "app", target = "support", port = 4318 }
    controller_otel        = { source = "controller", target = "support", port = 4318 }
  }
}

resource "oci_core_network_security_group_security_rule" "service_in" {
  for_each                  = local.connections
  network_security_group_id = oci_core_network_security_group.role[each.value.target].id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = oci_core_network_security_group.role[each.value.source].id
  source_type               = "NETWORK_SECURITY_GROUP"
  tcp_options {
    destination_port_range {
      min = each.value.port
      max = each.value.port
    }
  }
}

resource "oci_core_network_security_group_security_rule" "service_out" {
  for_each                  = local.connections
  network_security_group_id = oci_core_network_security_group.role[each.value.source].id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = oci_core_network_security_group.role[each.value.target].id
  destination_type          = "NETWORK_SECURITY_GROUP"
  tcp_options {
    destination_port_range {
      min = each.value.port
      max = each.value.port
    }
  }
}

resource "oci_core_network_security_group_security_rule" "admin_ssh" {
  for_each                  = var.administrator_cidrs
  network_security_group_id = oci_core_network_security_group.role["controller"].id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = each.value
  source_type               = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }
}

locals {
  local_services = { dns_udp = { protocol = "17", port = 53 }, dns_tcp = { protocol = "6", port = 53 }, ntp = { protocol = "17", port = 123 } }
  local_service_rules = { for pair in setproduct(local.roles, keys(local.local_services)) : "${pair[0]}-${pair[1]}" => {
    role = pair[0], protocol = local.local_services[pair[1]].protocol, port = local.local_services[pair[1]].port
  } }
}

resource "oci_core_network_security_group_security_rule" "local_services" {
  for_each                  = local.local_service_rules
  network_security_group_id = oci_core_network_security_group.role[each.value.role].id
  direction                 = "EGRESS"
  protocol                  = each.value.protocol
  destination               = "169.254.169.254/32"
  destination_type          = "CIDR_BLOCK"
  dynamic "tcp_options" {
    for_each = each.value.protocol == "6" ? [1] : []
    content {
      destination_port_range {
        min = each.value.port
        max = each.value.port
      }
    }
  }
  dynamic "udp_options" {
    for_each = each.value.protocol == "17" ? [1] : []
    content {
      destination_port_range {
        min = each.value.port
        max = each.value.port
      }
    }
  }
}

resource "oci_core_network_security_group_security_rule" "path_mtu" {
  for_each                  = setunion(local.roles, toset(["database"]))
  network_security_group_id = oci_core_network_security_group.role[each.key].id
  direction                 = "INGRESS"
  protocol                  = "1"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"
  icmp_options {
    type = 3
    code = 4
  }
}
