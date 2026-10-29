# Provider mocks prove profile projection without Azure, state, or env secrets.
mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id = "11111111-1111-4111-8111-111111111111"
    }
  }
  mock_resource "azurerm_resource_group" {
    defaults = {
      id = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/synthetic-sovereign"
    }
  }
  mock_resource "azurerm_container_registry" {
    defaults = {
      id           = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/synthetic-sovereign/providers/Microsoft.ContainerRegistry/registries/syntheticregistry"
      login_server = "syntheticregistry.azurecr.io"
    }
  }
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/synthetic-sovereign/providers/Microsoft.ManagedIdentity/userAssignedIdentities/synthetic-uai"
      client_id    = "22222222-2222-4222-8222-222222222222"
      principal_id = "33333333-3333-4333-8333-333333333333"
    }
  }
  mock_resource "azurerm_key_vault" {
    defaults = {
      id        = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/synthetic-sovereign/providers/Microsoft.KeyVault/vaults/synthetic-vault"
      vault_uri = "https://synthetic-vault.vault.azure.net/"
    }
  }
  mock_resource "azurerm_log_analytics_workspace" {
    defaults = {
      id = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/synthetic-sovereign/providers/Microsoft.OperationalInsights/workspaces/synthetic-workspace"
    }
  }
  mock_resource "azurerm_container_app_environment" {
    defaults = {
      id = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/synthetic-sovereign/providers/Microsoft.App/managedEnvironments/synthetic-env"
    }
  }
}

mock_provider "azapi" {
  mock_data "azapi_resource" {
    defaults = {
      id     = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/synthetic-prod/providers/Microsoft.HealthDataAIServices/deidServices/synthetic-deid"
      output = { service_url = "https://synthetic-deid.example.test" }
    }
  }
  mock_resource "azapi_resource" {
    defaults = {
      id     = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/synthetic-staging/providers/Microsoft.HealthDataAIServices/deidServices/synthetic-deid"
      output = { service_url = "https://synthetic-deid.example.test" }
    }
  }
}

mock_provider "external" {
  mock_data "external" {
    defaults = {
      result = { web = "uvicorn app.main:app --host 0.0.0.0 --port 8000" }
    }
  }
}

variables {
  deployment_environment = "prod"
  azure_deid_service_id  = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/synthetic-prod/providers/Microsoft.HealthDataAIServices/deidServices/synthetic-deid"
}

run "production_defaults_remain_dark_and_omit_athena_credentials" {
  command = plan

  assert {
    condition = (
      length(azurerm_container_app.sovereign) == 0 &&
      length(azurerm_container_app.public) == 0 &&
      length(data.external.provider_public_config) == 0 &&
      length(data.external.public_config) == 0
    )
    error_message = "Default production plans must not create live apps or read deployment env files."
  }

  assert {
    condition = local.provider_runtime_secret_vars == toset([
      "MAGISTERIUM_API_KEY", "AZURE_OPENAI_API_KEY", "SOVEREIGN_RECORD_GRANT_SECRET",
    ])
    error_message = "Production provider bindings must contain only the three required runtime secrets."
  }

  assert {
    condition = (
      contains(local.provider_runtime_public_vars, "FEATURE_SESSION_PREP") &&
      contains(local.provider_runtime_public_vars, "SESSION_PREP_CONTEXT_SOURCE") &&
      !contains(local.provider_runtime_public_vars, "SESSION_PREP_SYNTHETIC_CONTEXT_REF") &&
      !contains(var.public_public_vars, "FEATURE_SESSION_PREP") &&
      !contains(var.public_public_vars, "SESSION_PREP_CONTEXT_SOURCE") &&
      !contains(var.public_secret_vars, "SOVEREIGN_RECORD_GRANT_SECRET")
    )
    error_message = "Session Prep controls must project only into the production provider profile."
  }
}

run "staging_contract_remains_unchanged" {
  command = plan
  variables {
    deployment_environment = "staging"
    azure_deid_service_id  = null
  }

  assert {
    condition = (
      local.provider_runtime_public_vars == var.sovereign_public_vars &&
      local.provider_runtime_secret_vars == var.sovereign_secret_vars &&
      contains(local.provider_runtime_secret_vars, "ATHENA_CLIENT_ID") &&
      contains(local.provider_runtime_secret_vars, "ATHENA_CLIENT_SECRET")
    )
    error_message = "This production profile change must retain staging's existing config and vault contract."
  }
}
