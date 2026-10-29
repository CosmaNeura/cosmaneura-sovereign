########################################
# Runtime identities and mount-specific Key Vaults
#
# Secret VALUES are deliberately not Terraform resources: putting them in an
# azurerm_key_vault_secret block would persist plaintext in Terraform state.
# The versionless secret URIs below are populated out-of-band with
# scripts/sync-key-vault-secrets.sh before the Container Apps are created.
########################################

data "azurerm_client_config" "current" {}

resource "azurerm_user_assigned_identity" "provider_runtime" {
  name                = "${local.base_name}-runtime"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = merge(local.resource_tags, { mount = "provider_assistant" })

  # Recreating this principal changes both its Key Vault/ACR grants and the
  # client ID pinned into the provider app.
  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_user_assigned_identity" "public_runtime" {
  name                = "${local.public_base_name}-runtime"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = merge(local.resource_tags, { mount = "public" })

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_key_vault" "provider" {
  name                       = "cosmasov${local.environment}provider"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = true
  soft_delete_retention_days = 90

  # Container Apps use managed identity and RBAC. Network isolation should be
  # tightened with private endpoints when the two app environments gain VNets;
  # no secret is exposed without an explicit role assignment in the meantime.
  public_network_access_enabled = true

  tags = merge(local.resource_tags, { mount = "provider_assistant" })

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_key_vault" "public" {
  name                          = "cosmasov${local.environment}public"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  purge_protection_enabled      = true
  soft_delete_retention_days    = 90
  public_network_access_enabled = true

  tags = merge(local.resource_tags, { mount = "public" })

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_role_assignment" "provider_key_vault_secrets" {
  scope                = azurerm_key_vault.provider.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.provider_runtime.principal_id
}

resource "azurerm_role_assignment" "public_key_vault_secrets" {
  scope                = azurerm_key_vault.public.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.public_runtime.principal_id
}

resource "azurerm_role_assignment" "provider_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.provider_runtime.principal_id
}

resource "azurerm_role_assignment" "public_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.public_runtime.principal_id
}

# These data-plane roles exist only during the explicit bootstrap apply. Run the
# final apply without bootstrap_operator_object_id to remove them after the
# immutable image and vault values have been seeded.
resource "azurerm_role_assignment" "bootstrap_acr_push" {
  count                = var.bootstrap_operator_object_id == null ? 0 : 1
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPush"
  principal_id         = var.bootstrap_operator_object_id
}

# `az acr build` needs the registry task-run management action in addition to
# repository push/read. Remove this temporary role with the other bootstrap
# operator grants as soon as the first immutable digest is available.
resource "azurerm_role_assignment" "bootstrap_acr_tasks" {
  count                = var.bootstrap_operator_object_id == null ? 0 : 1
  scope                = azurerm_container_registry.main.id
  role_definition_name = "Container Registry Tasks Contributor"
  principal_id         = var.bootstrap_operator_object_id
}

resource "azurerm_role_assignment" "bootstrap_provider_secrets" {
  count                = var.bootstrap_operator_object_id == null ? 0 : 1
  scope                = azurerm_key_vault.provider.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = var.bootstrap_operator_object_id
}

resource "azurerm_role_assignment" "bootstrap_public_secrets" {
  count                = var.bootstrap_operator_object_id == null ? 0 : 1
  scope                = azurerm_key_vault.public.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = var.bootstrap_operator_object_id
}

locals {
  provider_key_vault_secret_ids = {
    for name in local.provider_runtime_secret_vars : name =>
    "${azurerm_key_vault.provider.vault_uri}secrets/${replace(lower(name), "_", "-")}"
  }
  public_key_vault_secret_ids = {
    for name in var.public_secret_vars : name =>
    "${azurerm_key_vault.public.vault_uri}secrets/${replace(lower(name), "_", "-")}"
  }
}
