########################################
# Resource group (isolated from the main app for HIPAA scope)
########################################

resource "azurerm_resource_group" "main" {
  name     = "cosmaneura-sovereign-${local.environment}"
  location = "northcentralus"
  tags     = local.resource_tags

  # Azure cascades a resource-group deletion to every child resource. Protect
  # the parent itself because a targeted destroy can omit the separate state
  # guard and every child lifecycle block from OpenTofu's plan graph.
  lifecycle {
    prevent_destroy = true
  }

  # Every targeted bootstrap path reaches the resource group. Making the state
  # guard an explicit dependency prevents `-target` from pruning the cached
  # backend/environment protection out of the plan graph.
  depends_on = [terraform_data.state_environment_guard]
}

# The backend key is selected from the same explicit environment input. Once a
# state is initialized, changing environments forces replacement of this guard,
# which prevent_destroy rejects. This closes the cached-backend failure mode in
# addition to the init/preflight backend inspection.
resource "terraform_data" "state_environment_guard" {
  triggers_replace = local.environment

  lifecycle {
    prevent_destroy = true
  }
}

########################################
# Observability
########################################

# Dedicated workspace so PHI-adjacent operational logs are not commingled with
# the main stack's (non-HIPAA-scoped) Log Analytics. Provider/PHI builds should
# raise retention to satisfy the audit policy agreed with counsel.
resource "azurerm_log_analytics_workspace" "main" {
  name                = "${local.base_name}-logs"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  daily_quota_gb      = local.log_analytics_daily_quota_gb
  retention_in_days   = 30
  tags                = local.resource_tags

  # Operational evidence and the environment's logging dependency must not be
  # removable through a narrow target-destroy plan.
  lifecycle {
    prevent_destroy = true
  }
}

# Consumer health data has a different legal/retention regime from provider
# PHI, so the public mount does not write into the provider workspace.
resource "azurerm_log_analytics_workspace" "public" {
  name                = "${local.public_base_name}-logs"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  daily_quota_gb      = local.log_analytics_daily_quota_gb
  retention_in_days   = 30
  tags                = merge(local.resource_tags, { mount = "public" })

  lifecycle {
    prevent_destroy = true
  }
}

########################################
# Container registry (dedicated — keeps the root self-contained)
########################################

resource "azurerm_container_registry" "main" {
  name                = "${local.base_name_alphanumeric}registry"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.resource_tags

  # Immutable digests in this registry are the paired deployment's rollback
  # roots. Require an explicit code review before Terraform can remove them.
  lifecycle {
    prevent_destroy = true
  }
}

########################################
# Container App Environment
########################################

resource "azurerm_container_app_environment" "main" {
  name                       = "${local.base_name}-app-environment"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.resource_tags

  # Deleting the environment can strand or remove every provider revision.
  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_container_app_environment" "public" {
  name                       = "${local.public_base_name}-app-environment"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.public.id
  tags                       = merge(local.resource_tags, { mount = "public" })

  lifecycle {
    prevent_destroy = true
  }
}

########################################
# Sovereign service (stateless FastAPI: POST /chat, GET /healthz)
########################################

resource "azurerm_container_app" "sovereign" {
  count                        = var.live_apps_enabled ? 1 : 0
  name                         = "${local.base_name}-app"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"

  # A stable user-assigned identity reads only the provider vault, pulls the
  # image, and calls the de-identification service. It exists before the app so
  # Key Vault RBAC can be established without a secret-bearing bootstrap.
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.provider_runtime.id]
  }

  ingress {
    external_enabled = true
    target_port      = 8000 # uvicorn (see ../Procfile)

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    # >0 so a crashed container self-heals even when config is unchanged.
    min_replicas = 1

    container {
      name   = "${local.base_name}-app-web"
      image  = var.initial_image_ref
      cpu    = 0.5
      memory = "1Gi"

      args = [data.external.procfile.result["web"]]

      # The provider authority axis is fixed for this entire process. Surface,
      # tier, and capabilities cannot be supplied by the env export.
      env {
        name  = "MOUNT_PROFILE"
        value = "provider_assistant"
      }

      # DefaultAzureCredential must select the same provider-only user-assigned
      # identity that Key Vault and ACR use. Without this, Azure DeID attempts a
      # nonexistent system identity.
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.provider_runtime.client_id
      }

      env {
        name  = "PLATFORM_WORKLOAD_CLIENT_ID"
        value = azurerm_user_assigned_identity.provider_runtime.client_id
      }

      # This is derived from the environment-owned DeID resource. Keeping it
      # outside the file export prevents staging from reusing a production URL.
      env {
        name  = "AZURE_DEID_ENDPOINT"
        value = local.azure_deid_endpoint
      }

      # Unlike the main app (TCP probes), the Sovereign exposes a real health
      # endpoint, so probe it over HTTP.
      readiness_probe {
        port      = 8000
        transport = "HTTP"
        path      = "/healthz"
      }
      startup_probe {
        port      = 8000
        transport = "HTTP"
        path      = "/healthz"
      }
      liveness_probe {
        port      = 8000
        transport = "HTTP"
        path      = "/healthz"
      }

      dynamic "env" {
        for_each = var.sovereign_public_vars
        content {
          name  = env.key
          value = data.external.provider_public_config[count.index].result[env.key]
        }
      }

      dynamic "env" {
        for_each = var.sovereign_secret_vars
        content {
          name        = env.key
          secret_name = replace(lower(env.key), "_", "-")
        }
      }
    }

  }

  dynamic "secret" {
    for_each = var.sovereign_secret_vars
    content {
      name                = replace(lower(secret.key), "_", "-")
      key_vault_secret_id = local.provider_key_vault_secret_ids[secret.key]
      identity            = azurerm_user_assigned_identity.provider_runtime.id
    }
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.provider_runtime.id
  }

  # Image lifecycle is owned by the deploy pipeline (CI runs
  # `az containerapp update --image <digest>`); Terraform must not revert the
  # live revision to the bootstrap digest.
  lifecycle {
    # Losing the persistent live-app inputs or changing a replacement-only
    # property must fail a plan, never silently destroy/recreate an ingress app
    # on its original bootstrap digest.
    prevent_destroy = true
    ignore_changes = [
      template[0].container[0].image,
    ]
    precondition {
      condition     = var.key_vault_secrets_ready
      error_message = "Seed both mount-specific Key Vaults before creating the Container Apps."
    }
    precondition {
      condition = (
        startswith(var.initial_image_ref, "${azurerm_container_registry.main.login_server}/") &&
        can(regex("@sha256:[0-9a-f]{64}$", var.initial_image_ref))
      )
      error_message = "Create both Container Apps from one immutable digest in this deployment's ACR."
    }
  }

  depends_on = [
    azurerm_role_assignment.provider_acr_pull,
    azurerm_role_assignment.provider_key_vault_secrets,
    azurerm_role_assignment.sovereign_deid,
  ]

  tags = local.resource_tags
}

########################################
# Anonymous public mount (separate app, environment, logs, and secrets)
########################################

resource "azurerm_container_app" "public" {
  count                        = var.live_apps_enabled ? 1 : 0
  name                         = "${local.public_base_name}-app"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.public.id
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.public_runtime.id]
  }

  ingress {
    external_enabled = true
    target_port      = 8000

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    # The rate limiter is deliberately process-local in this interim release.
    # A shared limiter is required before raising this value.
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "${local.public_base_name}-web"
      image  = var.initial_image_ref
      cpu    = 0.5
      memory = "1Gi"

      args = [data.external.procfile.result["web"]]

      # Safety-critical public invariants are literal Terraform values, not
      # entries copied from a shared deployment export.
      env {
        name  = "MOUNT_PROFILE"
        value = "public"
      }
      env {
        name  = "AUTH_MODE"
        value = "disabled"
      }
      env {
        name  = "ALLOW_PROVIDER_PERSONA"
        value = "false"
      }
      env {
        name  = "BAA_ATTESTED"
        value = "false"
      }
      env {
        name  = "FEATURE_ATHENA_LABS"
        value = "false"
      }
      env {
        name  = "LABS_ALLOW_PATIENT_SEARCH"
        value = "false"
      }
      env {
        name  = "PUBLIC_CHAT_TRUST_FORWARDED_FOR"
        value = "true"
      }
      readiness_probe {
        port      = 8000
        transport = "HTTP"
        path      = "/healthz"
      }
      startup_probe {
        port      = 8000
        transport = "HTTP"
        path      = "/healthz"
      }
      liveness_probe {
        port      = 8000
        transport = "HTTP"
        path      = "/healthz"
      }

      dynamic "env" {
        for_each = var.public_public_vars
        content {
          name  = env.key
          value = data.external.public_config[count.index].result[env.key]
        }
      }

      dynamic "env" {
        for_each = var.public_secret_vars
        content {
          name        = env.key
          secret_name = replace(lower(env.key), "_", "-")
        }
      }
    }

  }

  dynamic "secret" {
    for_each = var.public_secret_vars
    content {
      name                = replace(lower(secret.key), "_", "-")
      key_vault_secret_id = local.public_key_vault_secret_ids[secret.key]
      identity            = azurerm_user_assigned_identity.public_runtime.id
    }
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.public_runtime.id
  }

  lifecycle {
    prevent_destroy = true
    ignore_changes = [
      template[0].container[0].image,
    ]
    precondition {
      condition     = var.key_vault_secrets_ready
      error_message = "Seed both mount-specific Key Vaults before creating the Container Apps."
    }
    precondition {
      condition = (
        startswith(var.initial_image_ref, "${azurerm_container_registry.main.login_server}/") &&
        can(regex("@sha256:[0-9a-f]{64}$", var.initial_image_ref))
      )
      error_message = "Create both Container Apps from one immutable digest in this deployment's ACR."
    }
  }

  depends_on = [
    azurerm_role_assignment.public_acr_pull,
    azurerm_role_assignment.public_key_vault_secrets,
  ]

  tags = merge(local.resource_tags, { mount = "public" })
}

########################################
# Optional GitHub Actions deploy access (OIDC federation — no stored cloud creds)
########################################

resource "azurerm_user_assigned_identity" "ghactions" {
  count               = local.github_deploy_enabled ? 1 : 0
  name                = "${local.base_name}-gha-identity"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
}

resource "azurerm_federated_identity_credential" "ghactions" {
  count               = local.github_deploy_enabled ? 1 : 0
  name                = "github-actions"
  parent_id           = azurerm_user_assigned_identity.ghactions[0].id
  resource_group_name = azurerm_resource_group.main.name
  audience            = ["api://AzureADTokenExchange"]
  issuer              = "https://token.actions.githubusercontent.com"
  # Each deployment boundary has its own protected GitHub Environment and OIDC
  # subject. A staging token therefore cannot operate the production identity.
  subject = "repo:${var.github_repository}:environment:${local.deployment_context.github_environment}"
}

# Push images to ACR.
resource "azurerm_role_assignment" "github_acr_push" {
  count                = local.github_deploy_enabled ? 1 : 0
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.ghactions[0].principal_id
}

# The shared checksum-pinned build path uses ACR remote builds instead of a
# privileged runner Docker daemon.
resource "azurerm_role_assignment" "github_acr_tasks" {
  count                = local.github_deploy_enabled ? 1 : 0
  scope                = azurerm_container_registry.main.id
  role_definition_name = "Container Registry Tasks Contributor"
  principal_id         = azurerm_user_assigned_identity.ghactions[0].principal_id
}

# Update revisions on the environment + the service.
resource "azurerm_role_assignment" "github_env_operator" {
  count                = local.github_deploy_enabled ? 1 : 0
  scope                = azurerm_container_app_environment.main.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ghactions[0].principal_id
}

resource "azurerm_role_assignment" "github_app_operator" {
  count                = var.live_apps_enabled && local.github_deploy_enabled ? 1 : 0
  scope                = azurerm_container_app.sovereign[0].id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ghactions[0].principal_id
}

resource "azurerm_role_assignment" "github_public_env_operator" {
  count                = local.github_deploy_enabled ? 1 : 0
  scope                = azurerm_container_app_environment.public.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ghactions[0].principal_id
}

resource "azurerm_role_assignment" "github_public_app_operator" {
  count                = var.live_apps_enabled && local.github_deploy_enabled ? 1 : 0
  scope                = azurerm_container_app.public[0].id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ghactions[0].principal_id
}

# Adding a conditional count changes these six legacy production addresses.
# Record the one-time address migration so enabling the existing prod default
# does not recreate its deploy principal, federation, or grants.
moved {
  from = azurerm_user_assigned_identity.ghactions
  to   = azurerm_user_assigned_identity.ghactions[0]
}

moved {
  from = azurerm_federated_identity_credential.ghactions
  to   = azurerm_federated_identity_credential.ghactions[0]
}

moved {
  from = azurerm_role_assignment.github_acr_push
  to   = azurerm_role_assignment.github_acr_push[0]
}

moved {
  from = azurerm_role_assignment.github_acr_tasks
  to   = azurerm_role_assignment.github_acr_tasks[0]
}

moved {
  from = azurerm_role_assignment.github_env_operator
  to   = azurerm_role_assignment.github_env_operator[0]
}

moved {
  from = azurerm_role_assignment.github_public_env_operator
  to   = azurerm_role_assignment.github_public_env_operator[0]
}
