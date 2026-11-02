variable "region" { type = string }
variable "compartment_id" { type = string }
variable "availability_domain" { type = string }
variable "image_ocid" {
  type        = string
  description = "Pinned Ubuntu 24.04 x86_64 image in the selected region."
}
variable "auth" {
  type    = string
  default = "APIKey"
  validation {
    condition     = contains(["APIKey", "SecurityToken", "InstancePrincipal", "ResourcePrincipal"], var.auth)
    error_message = "Use a supported OCI authentication method."
  }
}
variable "config_file_profile" {
  type        = string
  default     = "DEFAULT"
  description = "Existing profile only; this stack never creates or changes CLI profiles."
}
variable "ssh_authorized_key" {
  type        = string
  description = "Your SSH public key; never provide a private key."
  validation {
    condition     = can(regex("^ssh-(ed25519|rsa) [A-Za-z0-9+/=]+", var.ssh_authorized_key))
    error_message = "Provide an OpenSSH public key."
  }
}
variable "bastion_client_cidrs" {
  type        = set(string)
  description = "Public IPv4 /32 addresses allowed to open temporary Bastion sessions."
  validation {
    condition     = length(var.bastion_client_cidrs) > 0 && alltrue([for cidr in var.bastion_client_cidrs : can(cidrnetmask(cidr)) && endswith(cidr, "/32")])
    error_message = "Provide at least one explicit public client IPv4 /32."
  }
}
variable "name_prefix" {
  type    = string
  default = "fluxnova-lab"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.name_prefix))
    error_message = "Use 3–20 lowercase letters, digits or hyphens, starting with a letter."
  }
}
variable "vcn_cidr" {
  type    = string
  default = "10.86.0.0/16"
  validation {
    condition     = can(cidrsubnet(var.vcn_cidr, 8, 1))
    error_message = "Supply an IPv4 network with space for two /24-style subnets."
  }
}
variable "compute_shape" {
  type    = string
  default = "VM.Standard.E5.Flex"
}
variable "ocpus" {
  type    = number
  default = 1
  validation {
    condition     = var.ocpus >= 1 && var.ocpus <= 4 && floor(var.ocpus) == var.ocpus
    error_message = "This small lab supports 1–4 OCPUs."
  }
}
variable "memory_gb" {
  type    = number
  default = 8
  validation {
    condition     = var.memory_gb >= 8 && var.memory_gb <= 32
    error_message = "This small lab supports 8–32 GB."
  }
}
