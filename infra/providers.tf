# Terraform/OpenTofu root for the CosmaNeura Sovereign microservice.
#
# This is a SEPARATE state and a SEPARATE resource group from the main app
# (cosmaneura/infra). That isolation is deliberate: the provider/PHI build pulls
# this service into HIPAA scope (BAA inference, ZDR, ≥6yr WORM audit), and we do
# not want that scope to engulf the main stack's shared logging/storage. See
# ../DEPLOY_AZURE.md.
#
# Patterns here intentionally mirror cosmaneura/infra/main.tf so it reads the
# same to anyone who knows that stack: data.external secrets export, the
# public/secret env-var split, SHA-pinned images deployed by CI with Terraform
# ignoring the live image, etc.

locals {
  environment           = "prod"
  azure_subscription_id = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"

  # Terraform state. We REUSE the main stack's tfstate storage account (already
  # bootstrapped in cosmaneura/infra) but under a distinct key, so the Sovereign
  # has its own independent state without standing up a second backend.
  tfstate = {
    storage_account = "cosmaneuratfstate"
    resource_group  = "cosmaneura-tfstate"
    container       = "cosmaneura-tfstate"
    key             = "sovereign-${local.environment}.tfstate"
  }

  base_name = azurerm_resource_group.main.name
  # Azure requires alphanumeric-only names in several places (ACR, etc.).
  base_name_alphanumeric = replace(local.base_name, "/[^a-zA-Z0-9]/", "")
}

terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">=4.14.0"
    }
    # extract secrets (from 1Password via `op inject`) as JSON for env wiring
    external = {
      source  = "hashicorp/external"
      version = ">=2.3.4"
    }
  }

  # Reuses the main stack's state storage account; distinct key (see locals).
  # OpenTofu permits local references in the backend block.
  backend "azurerm" {
    resource_group_name  = local.tfstate.resource_group
    storage_account_name = local.tfstate.storage_account
    container_name       = local.tfstate.container
    key                  = local.tfstate.key
  }
}

provider "azurerm" {
  subscription_id = local.azure_subscription_id
  features {}
}
