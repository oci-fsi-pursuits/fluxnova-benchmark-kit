# Terraform >=1.7 local test only. Mocked provider: never creates cloud resources.
mock_provider "oci" {
  mock_resource "oci_bastion_bastion" {
    defaults = { private_endpoint_ip_address = "10.78.2.2" }
  }
  mock_resource "oci_core_instance" {
    defaults = { private_ip = "10.78.0.10" }
  }
  mock_data "oci_core_services" {
    defaults = {
      services = [{ id = "ocid1.service.mock", cidr_block = "all-test-services", name = "All TEST Services In Oracle Services Network" }]
    }
  }
  mock_data "oci_objectstorage_namespace" {
    defaults = { namespace = "benchmarktest" }
  }
  mock_data "oci_objectstorage_bucket" {
    defaults = { name = "retained-test-evidence" }
  }
}

variables {
  region               = "us-ashburn-1"
  compartment_id       = "ocid1.compartment.oc1..mock"
  availability_domain  = "mock:US-ASHBURN-AD-1"
  image_ocid           = "ocid1.image.oc1.iad.mock"
  budget_limit_usd     = 100
  ssh_authorized_key   = "ssh-ed25519 MOCK-PUBLIC-KEY-NEVER-DEPLOY"
  db_admin_secret_id   = "ocid1.vaultsecret.oc1.iad.mock"
  db_shape             = "PostgreSQL.VM.Standard.E5.Flex"
  db_ocpu_count        = 2
  db_memory_gb         = 32
  db_storage_iops      = 75000
  artifact_bucket_name = "retained-test-evidence"
  bastion_client_cidrs = []
}

run "private_reference_plan" {
  command = plan

  assert {
    # OCI 7.22 exposes assign_public_ip as a string; Terraform coerces false to "false".
    condition     = length(oci_core_instance.role) == 4 && alltrue([for role in oci_core_instance.role : role.create_vnic_details[0].assign_public_ip == "false"])
    error_message = "The reference cell must have four isolated private VM roles."
  }
  assert {
    condition     = alltrue([for subnet in oci_core_subnet.private : subnet.prohibit_public_ip_on_vnic])
    error_message = "No benchmark subnet may allow public VM addresses."
  }
  assert {
    condition     = oci_psql_db_system.bench.credentials[0].password_details[0].password_type == "VAULT_SECRET" && oci_psql_db_system.bench.db_version == "17"
    error_message = "PostgreSQL 17 must use a Vault secret reference, not a password in Terraform."
  }
  assert {
    condition     = length(oci_core_network_security_group_security_rule.admin_ssh) == 0
    error_message = "An untouched configuration must not open administrative ingress."
  }
  assert {
    condition     = output.environment_contract.artifacts.retained_in_separate_stack && output.environment_contract.budget_limit_usd == 100
    error_message = "The inventory must disclose retained evidence and the campaign budget."
  }
}

run "reject_unrestricted_admin_source" {
  command = plan
  variables { administrator_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.administrator_cidrs]
}

run "bastion_controller_access" {
  command = apply # Mock provider only; evaluates computed endpoint addresses.
  variables { bastion_client_cidrs = ["203.0.113.10/32"] }

  assert {
    condition     = length(oci_bastion_bastion.controller) == 1 && oci_core_subnet.bastion[0].prohibit_public_ip_on_vnic && oci_core_subnet.bastion[0].cidr_block == "10.78.2.0/24"
    error_message = "Bastion must have a separate private subnet."
  }
  assert {
    condition     = length(oci_core_security_list.bastion[0].egress_security_rules) == 1 && one(oci_core_security_list.bastion[0].egress_security_rules).destination == "10.78.0.10/32" && one(oci_core_security_list.bastion[0].egress_security_rules).tcp_options[0].min == 22 && one(oci_core_security_list.bastion[0].egress_security_rules).tcp_options[0].max == 22 && length(oci_core_security_list.bastion[0].ingress_security_rules) == 0
    error_message = "Bastion egress must reach only the controller on TCP/22, with no unsolicited ingress."
  }
  assert {
    condition     = oci_core_network_security_group_security_rule.bastion_ssh[0].source == "10.78.2.2/32" && oci_core_network_security_group_security_rule.bastion_ssh[0].network_security_group_id == oci_core_network_security_group.role["controller"].id && length(oci_core_network_security_group_security_rule.admin_ssh) == 0
    error_message = "Only the Bastion private endpoint may enter the controller over the Bastion SSH rule."
  }
  assert {
    condition     = output.environment_contract.bastion.target_port == 22 && output.environment_contract.bastion.client_cidrs == tolist(["203.0.113.10/32"])
    error_message = "The tunnel script needs the Bastion connection details in the contract."
  }
}

run "reject_unrestricted_bastion_source" {
  command = plan
  variables { bastion_client_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.bastion_client_cidrs]
}

run "reject_compute_shape_for_postgresql" {
  command = plan
  variables { db_shape = "VM.Standard.E5.Flex" }
  expect_failures = [var.db_shape]
}
