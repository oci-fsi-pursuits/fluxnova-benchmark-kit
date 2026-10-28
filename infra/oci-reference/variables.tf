variable "region" { type = string }
variable "compartment_id" { type = string }
variable "availability_domain" { type = string }
variable "image_ocid" {
  type        = string
  description = "Pinned Ubuntu 24.04 x86_64 platform image OCID in the chosen region."
}
variable "budget_limit_usd" {
  type        = number
  description = "Approved campaign ceiling recorded in the contract. Does not enforce a billing hard cap."
  validation {
    condition     = var.budget_limit_usd > 0
    error_message = "A positive approved campaign budget is required."
  }
}
variable "ssh_authorized_key" {
  type        = string
  description = "Public key only; private keys must never enter Terraform variables."
}
variable "db_admin_secret_id" {
  type        = string
  description = "Existing OCI Vault secret OCID containing the database administrator password."
}
variable "db_admin_secret_version" {
  type    = number
  default = 1
}
variable "db_admin_username" {
  type    = string
  default = "benchadmin"
}
variable "db_shape" {
  type        = string
  description = "Exact PostgreSQL shape ID (id, not the Compute shape field) returned by PostgreSQL ListShapes."
  validation {
    condition     = startswith(var.db_shape, "PostgreSQL.")
    error_message = "Use the PostgreSQL ListShapes id, for example PostgreSQL.VM.Standard.E5.Flex, not VM.Standard.E5.Flex."
  }
}
variable "db_ocpu_count" { type = number }
variable "db_memory_gb" { type = number }
variable "db_storage_iops" { type = number }
variable "artifact_bucket_name" {
  type        = string
  description = "Existing retained bucket from the separate oci-evidence stack."
}
variable "name_prefix" {
  type    = string
  default = "fluxnova-bench"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,23}$", var.name_prefix))
    error_message = "Use 3-24 lowercase letters, digits or hyphens beginning with a letter."
  }
}
variable "compute_shape" {
  type    = string
  default = "VM.Standard.E5.Flex"
}
variable "vcn_cidr" {
  type    = string
  default = "10.78.0.0/16"
}
variable "administrator_cidrs" {
  type        = set(string)
  default     = []
  description = "Private VPN/DRG or OCI Bastion private endpoint CIDRs permitted to SSH. No public endpoint is created."
  validation {
    condition     = alltrue([for cidr in var.administrator_cidrs : can(cidrnetmask(cidr)) && cidr != "0.0.0.0/0"])
    error_message = "Administrator sources must be explicit IPv4 CIDRs, never 0.0.0.0/0."
  }
}
variable "bastion_client_cidrs" {
  type        = set(string)
  default     = []
  description = "Public client IPv4 CIDRs allowed to use OCI Bastion. A nonempty list enables Bastion; prefer your workstation's public /32."
  validation {
    condition     = alltrue([for cidr in var.bastion_client_cidrs : can(cidrnetmask(cidr)) && try(tonumber(split("/", cidr)[1]) > 0, false)])
    error_message = "Bastion clients must be explicit IPv4 CIDRs; unrestricted /0 access is not allowed."
  }
}
variable "role_resources" {
  type = map(object({ ocpus = number, memory_gb = number }))
  default = {
    controller = { ocpus = 2, memory_gb = 16 }
    app        = { ocpus = 2, memory_gb = 16 }
    loadgen    = { ocpus = 2, memory_gb = 8 }
    support    = { ocpus = 2, memory_gb = 8 }
  }
  validation {
    condition     = length(setsubtract(toset(keys(var.role_resources)), toset(["controller", "app", "loadgen", "support"]))) == 0 && length(var.role_resources) == 4
    error_message = "Exactly controller, app, loadgen and support roles are required."
  }
}
variable "package_versions" {
  type        = map(string)
  default     = {}
  description = "Optional exact Ubuntu package versions; empty uses the pinned image repositories and records installed versions. For comparison campaigns pin python3, python3-venv, docker.io, docker-compose-v2 and git."
  validation {
    condition     = alltrue([for name, version in var.package_versions : contains(["python3", "python3-venv", "docker.io", "docker-compose-v2", "git", "chrony"], name) && can(regex("^[A-Za-z0-9.+:~_-]+$", version))])
    error_message = "Only approved package names and literal Debian package version strings are accepted."
  }
}
