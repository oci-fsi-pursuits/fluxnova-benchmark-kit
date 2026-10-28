terraform {
  required_version = ">= 1.5.0, < 2.0.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "= 7.22.0"
    }
  }
}

# Resource Manager supplies authentication. Local validation requires no credentials.
provider "oci" {
  region = var.region
}
