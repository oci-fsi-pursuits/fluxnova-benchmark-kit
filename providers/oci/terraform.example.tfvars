# Discover regional identifiers and use an existing CLI profile.
region               = "REPLACE_WITH_REGION"
compartment_id       = "ocid1.compartment.REPLACE"
availability_domain  = "REPLACE_WITH_FULL_AD_NAME"
image_ocid           = "ocid1.image.REPLACE_WITH_UBUNTU_24_04_X86_64"
ssh_authorized_key   = "ssh-ed25519 REPLACE_WITH_PUBLIC_KEY"
bastion_client_cidrs = ["203.0.113.10/32"] # Replace with your current public IPv4.
auth                 = "SecurityToken"
config_file_profile  = "REPLACE_WITH_YOUR_DEDICATED_PROFILE"
name_prefix          = "fluxnova-lab"
