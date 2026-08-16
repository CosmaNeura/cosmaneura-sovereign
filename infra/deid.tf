########################################
# Clinical PHI de-identification access
#
# The provider/PHI build de-identifies text with Azure Health Data Services
# (the same "AnonymizerAPI" deid service the main CosmaNeura app uses) before it
# reaches any non-BAA component (Magisterium, NPPES, ClinicalTrials). The
# Sovereign authenticates with its managed identity — no stored secret — so it
# needs the realtime de-id role on that service.
########################################

variable "azure_deid_service_id" {
  description = "Resource ID of the Azure Health Data Services de-id service"
  type        = string
  default     = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/cosmaneura-prod/providers/Microsoft.HealthDataAIServices/deidServices/AnonymizerAPI"
}

resource "azurerm_role_assignment" "sovereign_deid" {
  scope                = var.azure_deid_service_id
  role_definition_name = "DeID Realtime Data User"
  principal_id         = azurerm_user_assigned_identity.provider_runtime.principal_id
}
