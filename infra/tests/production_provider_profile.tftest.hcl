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
      login_server = "cosmaneurasovereignprodregistry.azurecr.io"
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
  mock_resource "azurerm_container_app" {
    defaults = {
      id = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/synthetic-sovereign/providers/Microsoft.App/containerApps/synthetic-app"
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
      result = {
        ALLOW_PROVIDER_PERSONA                = "false"
        ANTHROPIC_MCP_BETA                    = "mcp-client-2025-11-20"
        ATHENA_DEPARTMENT_ID                  = "1"
        ATHENA_ENV                            = "preview"
        ATHENA_PRACTICE_ID                    = "195900"
        AUDIT_PATH                            = "/tmp/audit_log.jsonl"
        AUDIT_SINK                            = "stdout"
        AUTH_MODE                             = "disabled"
        AUTH_PROVIDER_ROLE                    = "provider"
        AUTH_ROLE_CLAIM                       = "roles"
        AZURE_OPENAI_API_VERSION              = "2025-03-01-preview"
        AZURE_OPENAI_ENDPOINT                 = "https://cosmascode.services.ai.azure.com/openai/v1"
        BAA_ATTESTED                          = "true"
        CLINICALTRIALS_BASE_URL               = "https://clinicaltrials.gov/api/v2"
        CORS_ALLOW_ORIGINS                    = "[\"https://app.cosmaneura.com\"]"
        DEID_PROVIDER                         = "azure"
        FAST_MODEL                            = "gpt-5.4-nano"
        FEATURE_ATHENA_LABS                   = "false"
        FEATURE_SESSION_PREP                  = "false"
        LABS_ALLOW_PATIENT_SEARCH             = "false"
        LLM_PROVIDER                          = "azure_openai"
        MAGISTERIUM_BASE_URL                  = "https://www.magisterium.com/api/v1"
        MAGISTERIUM_MCP_ENABLED               = "false"
        MAGISTERIUM_MODEL                     = "magisterium-1"
        MOCK_MODE                             = "false"
        MOUNT_PROFILE                         = "provider_assistant"
        MOUNT_TENANT_ID                       = "tenant_synthetic_production"
        NPPES_BASE_URL                        = "https://npiregistry.cms.hhs.gov/api"
        OIDC_ALGORITHMS                       = "[\"RS256\"]"
        OIDC_AUDIENCE                         = "cosmaneura-sovereign"
        OIDC_ISSUER                           = "https://synthetic.clerk.accounts.dev"
        OIDC_JWKS_URL                         = "https://synthetic.clerk.accounts.dev/.well-known/jwks.json"
        PLATFORM_AGENT_API_BASE_URL           = "https://app.cosmaneura.com"
        PLATFORM_AGENT_API_ENABLED            = "true"
        PLATFORM_AGENT_API_SCOPE              = "api://af957741-0342-498b-8496-e93706af9e99/.default"
        PUBLIC_CHAT_RATE_LIMIT_MAX_CLIENTS    = "10000"
        PUBLIC_CHAT_RATE_LIMIT_REQUESTS       = "30"
        PUBLIC_CHAT_RATE_LIMIT_WINDOW_SECONDS = "60"
        REASONING_MODEL                       = "gpt-5.6-sol"
        SESSION_PREP_CONTEXT_SOURCE           = "synthetic"
        SOVEREIGN_MODE                        = "hybrid"
        web                                   = "uvicorn app.main:app --host 0.0.0.0 --port 8000"
      }
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

run "production_provider_only_creates_no_public_workload_or_config_read" {
  command = plan
  variables {
    production_provider_app_enabled = true
    key_vault_secrets_ready         = true
    initial_image_ref               = "cosmaneurasovereignprodregistry.azurecr.io/cosmaneura/sovereign@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }

  assert {
    condition = (
      length(azurerm_container_app.sovereign) == 1 &&
      length(data.external.provider_public_config) == 1 &&
      length(azurerm_container_app.public) == 0 &&
      length(data.external.public_config) == 0 &&
      length(azurerm_role_assignment.github_app_operator) == 1 &&
      length(azurerm_role_assignment.github_public_app_operator) == 0
    )
    error_message = "Provider-only production must create only the provider workload/config read/app operator role."
  }
  assert {
    condition = (
      [for env in azurerm_container_app.sovereign[0].template[0].container[0].env : env.value if env.name == "FEATURE_SESSION_PREP"] == ["false"] &&
      [for env in azurerm_container_app.sovereign[0].template[0].container[0].env : env.value if env.name == "MOUNT_PROFILE"] == ["provider_assistant"]
    )
    error_message = "Provider app creation must retain the dark Session Prep profile and provider mount."
  }
}

run "legacy_production_dual_creation_still_creates_both_workloads" {
  command = plan
  variables {
    live_apps_enabled       = true
    key_vault_secrets_ready = true
    initial_image_ref       = "cosmaneurasovereignprodregistry.azurecr.io/cosmaneura/sovereign@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  assert {
    condition = (
      length(azurerm_container_app.sovereign) == 1 &&
      length(azurerm_container_app.public) == 1 &&
      length(data.external.provider_public_config) == 1 &&
      length(data.external.public_config) == 1 &&
      length(azurerm_role_assignment.github_app_operator) == 1 &&
      length(azurerm_role_assignment.github_public_app_operator) == 1
    )
    error_message = "The existing dual-app gate must retain both workload and config projections."
  }
}

run "production_rejects_provider_only_combined_with_legacy_dual_gate" {
  command = plan
  variables {
    production_provider_app_enabled = true
    live_apps_enabled               = true
    key_vault_secrets_ready         = true
    initial_image_ref               = "cosmaneurasovereignprodregistry.azurecr.io/cosmaneura/sovereign@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  expect_failures = [var.production_provider_app_enabled]
}

run "staging_rejects_the_production_provider_control" {
  command = plan
  variables {
    deployment_environment          = "staging"
    azure_deid_service_id           = null
    production_provider_app_enabled = true
    key_vault_secrets_ready         = true
    initial_image_ref               = "cosmaneurasovereignstagingregistry.azurecr.io/cosmaneura/sovereign@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  override_resource {
    target = azurerm_container_registry.main
    values = {
      id           = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/synthetic-sovereign/providers/Microsoft.ContainerRegistry/registries/syntheticstagingregistry"
      login_server = "cosmaneurasovereignstagingregistry.azurecr.io"
    }
  }
  expect_failures = [var.production_provider_app_enabled]
}

run "staging_dual_creation_retains_existing_workloads_and_credentials" {
  command = plan
  variables {
    deployment_environment  = "staging"
    azure_deid_service_id   = null
    live_apps_enabled       = true
    key_vault_secrets_ready = true
    initial_image_ref       = "cosmaneurasovereignstagingregistry.azurecr.io/cosmaneura/sovereign@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  override_resource {
    target = azurerm_container_registry.main
    values = {
      id           = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/synthetic-sovereign/providers/Microsoft.ContainerRegistry/registries/syntheticstagingregistry"
      login_server = "cosmaneurasovereignstagingregistry.azurecr.io"
    }
  }
  assert {
    condition = (
      length(azurerm_container_app.sovereign) == 1 &&
      length(azurerm_container_app.public) == 1 &&
      length(data.external.provider_public_config) == 1 &&
      length(data.external.public_config) == 1 &&
      local.provider_runtime_secret_vars == var.sovereign_secret_vars &&
      length(azurerm_role_assignment.github_app_operator) == 0
    )
    error_message = "Staging retains its legacy dual app/config/secret and default-off GitHub operator behavior."
  }
}
