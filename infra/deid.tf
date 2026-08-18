########################################
# Clinical PHI de-identification boundary
#
# Production keeps its already-provisioned service as an explicit read-only
# dependency. Staging owns a physically separate DeID service in the Sovereign
# staging resource group. The provider runtime identity gets one resource-level
# realtime role on the service selected by the current state boundary.
########################################

variable "azure_deid_service_id" {
  description = "Explicit resource ID of the existing production Azure Health Data Services DeID service; omitted in staging"
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.azure_deid_service_id == null ||
      can(regex(
        "^/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+/providers/Microsoft\\.HealthDataAIServices/deidServices/[^/]+$",
        var.azure_deid_service_id,
      ))
    )
    error_message = "azure_deid_service_id must be null or an explicit DeID service resource ID."
  }
}

# Production is deliberately data-only: this root neither creates nor updates
# the existing service. Reading its serviceUrl removes the duplicated endpoint
# literal from the deployment environment file.
data "azapi_resource" "production_deid" {
  count = local.environment == "prod" ? 1 : 0

  type        = "Microsoft.HealthDataAIServices/deidServices@2024-09-20"
  resource_id = var.azure_deid_service_id
  response_export_values = {
    service_url = "properties.serviceUrl"
  }

  lifecycle {
    precondition {
      condition     = var.azure_deid_service_id != null
      error_message = "production requires the explicit existing azure_deid_service_id."
    }
  }
}

# North Central US does not offer this resource. East US is the reviewed,
# supported staging location and remains a different service from production.
resource "azapi_resource" "staging_deid" {
  count = local.environment == "staging" ? 1 : 0

  type      = "Microsoft.HealthDataAIServices/deidServices@2024-09-20"
  name      = "cosmaneura-staging-deid"
  parent_id = azurerm_resource_group.main.id
  location  = "eastus"
  tags      = merge(local.resource_tags, { mount = "provider_assistant" })

  identity {
    type = "None"
  }

  body = {
    properties = {
      publicNetworkAccess = "Enabled"
    }
  }

  response_export_values = {
    service_url = "properties.serviceUrl"
  }

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  azure_deid_service_id = (
    local.environment == "staging"
    ? azapi_resource.staging_deid[0].id
    : data.azapi_resource.production_deid[0].id
  )
  azure_deid_endpoint = (
    local.environment == "staging"
    ? azapi_resource.staging_deid[0].output.service_url
    : data.azapi_resource.production_deid[0].output.service_url
  )
}

resource "azurerm_role_assignment" "sovereign_deid" {
  scope                = local.azure_deid_service_id
  role_definition_name = "DeID Realtime Data User"
  principal_id         = azurerm_user_assigned_identity.provider_runtime.principal_id
}
