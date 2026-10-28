terraform {
  required_version = ">= 1.5.0, < 2.0.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "= 7.22.0"
    }
  }
}

provider "oci" { region = var.region }

variable "region" { type = string }
variable "compartment_id" { type = string }
variable "artifact_bucket_name" {
  type        = string
  description = "Unique evidence bucket name within the tenancy namespace."
}

data "oci_objectstorage_namespace" "account" { compartment_id = var.compartment_id }

# Keep this stack independent: destroying oci-reference does not target evidence.
resource "oci_objectstorage_bucket" "evidence" {
  compartment_id = var.compartment_id
  namespace      = data.oci_objectstorage_namespace.account.namespace
  name           = var.artifact_bucket_name
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"
  versioning     = "Enabled"
  freeform_tags  = { managed_by = "fluxnova-benchmarks", purpose = "retained-evidence" }
  lifecycle {
    prevent_destroy = true
  }
}

output "evidence_contract" {
  value = {
    region    = var.region
    namespace = data.oci_objectstorage_namespace.account.namespace
    bucket    = oci_objectstorage_bucket.evidence.name
    bucket_id = oci_objectstorage_bucket.evidence.bucket_id
    retention = "Versioned; no automatic expiry. Explicit archival review required before deleting this separate stack."
  }
}
