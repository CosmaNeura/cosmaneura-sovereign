########################################
# Resource group (isolated from the main app for HIPAA scope)
########################################

resource "azurerm_resource_group" "main" {
  name     = "cosmaneura-sovereign-${local.environment}"
  location = "northcentralus"
  tags     = var.tags
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
  retention_in_days   = 30
  tags                = var.tags
}

# Consumer health data has a different legal/retention regime from provider
# PHI, so the public mount does not write into the provider workspace.
resource "azurerm_log_analytics_workspace" "public" {
  name                = "${local.public_base_name}-logs"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  retention_in_days   = 30
  tags                = merge(var.tags, { mount = "public" })
}

########################################
# Container registry (dedicated — keeps the root self-contained)
########################################

resource "azurerm_container_registry" "main" {
  name                = "${local.base_name_alphanumeric}registry"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = true
  tags                = var.tags
}

########################################
# Container App Environment
########################################

resource "azurerm_container_app_environment" "main" {
  name                       = "${local.base_name}-app-environment"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = var.tags
}

resource "azurerm_container_app_environment" "public" {
  name                       = "${local.public_base_name}-app-environment"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.public.id
  tags                       = merge(var.tags, { mount = "public" })
}

########################################
# Sovereign service (stateless FastAPI: POST /chat, GET /healthz)
########################################

resource "azurerm_container_app" "sovereign" {
  name                         = "${local.base_name}-app"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"

  # Managed identity for keyless auth to Azure services (the de-identification
  # service — see deid.tf). No stored secret.
  identity {
    type = "SystemAssigned"
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
      image  = "${azurerm_container_registry.main.login_server}/cosmaneura/sovereign:latest"
      cpu    = 0.5
      memory = "1Gi"

      args = [data.external.procfile.result["web"]]

      # The provider authority axis is fixed for this entire process. Surface,
      # tier, and capabilities cannot be supplied by the env export.
      env {
        name  = "MOUNT_PROFILE"
        value = "provider_assistant"
      }

      # Read the scraped MyCatholicDoctor directory from the shared volume
      # (written by the scheduled scraper job — see data_refresh.tf). The
      # client falls back to the bundled fixture if the file isn't there yet.
      env {
        name  = "PARTNER_DIRECTORY_PATH"
        value = "/data/providers.json"
      }

      volume_mounts {
        name = "partner-data"
        path = "/data"
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
          value = data.external.secrets.result[env.key]
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

    # Shared partner-directory volume, written by the scraper job.
    volume {
      name         = "partner-data"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.partner_data.name
    }
  }

  dynamic "secret" {
    for_each = var.sovereign_secret_vars
    content {
      name  = replace(lower(secret.key), "_", "-")
      value = data.external.secrets.result[secret.key]
    }
  }

  registry {
    server               = azurerm_container_registry.main.login_server
    username             = azurerm_container_registry.main.admin_username
    password_secret_name = "registry-secret"
  }

  secret {
    name  = "registry-secret"
    value = azurerm_container_registry.main.admin_password
  }

  # Image lifecycle is owned by the deploy pipeline (CI runs
  # `az containerapp update --set-image <SHA>`); Terraform must not revert the
  # live revision back to ":latest". Mirrors cosmaneura/infra.
  lifecycle {
    ignore_changes = [
      template[0].container[0].image,
    ]
  }

  tags = var.tags
}

########################################
# Anonymous public mount (separate app, environment, logs, and secrets)
########################################

resource "azurerm_container_app" "public" {
  name                         = "${local.public_base_name}-app"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.public.id
  revision_mode                = "Single"

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
      image  = "${azurerm_container_registry.main.login_server}/cosmaneura/sovereign:latest"
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
      env {
        name  = "PARTNER_DIRECTORY_PATH"
        value = "/data/providers.json"
      }

      volume_mounts {
        name = "partner-data"
        path = "/data"
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
          value = data.external.public_secrets.result[env.key]
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

    volume {
      name         = "partner-data"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.public_partner_data.name
    }
  }

  dynamic "secret" {
    for_each = var.public_secret_vars
    content {
      name  = replace(lower(secret.key), "_", "-")
      value = data.external.public_secrets.result[secret.key]
    }
  }

  registry {
    server               = azurerm_container_registry.main.login_server
    username             = azurerm_container_registry.main.admin_username
    password_secret_name = "registry-secret"
  }

  secret {
    name  = "registry-secret"
    value = azurerm_container_registry.main.admin_password
  }

  lifecycle {
    ignore_changes = [
      template[0].container[0].image,
    ]
  }

  tags = merge(var.tags, { mount = "public" })
}

########################################
# GitHub Actions deploy access (OIDC federation — no stored cloud creds)
########################################

resource "azurerm_user_assigned_identity" "ghactions" {
  name                = "${local.base_name}-gha-identity"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
}

resource "azurerm_federated_identity_credential" "ghactions" {
  name                = "github-actions"
  parent_id           = azurerm_user_assigned_identity.ghactions.id
  resource_group_name = azurerm_resource_group.main.name
  audience            = ["api://AzureADTokenExchange"]
  issuer              = "https://token.actions.githubusercontent.com"
  # Casing must match the GitHub repo exactly or token exchange fails.
  subject = "repo:${var.github_repository}:ref:refs/heads/${var.github_branch}"
}

# Push images to ACR.
resource "azurerm_role_assignment" "github_acr_push" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.ghactions.principal_id
}

# Update revisions on the environment + the service.
resource "azurerm_role_assignment" "github_env_operator" {
  scope                = azurerm_container_app_environment.main.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ghactions.principal_id
}

resource "azurerm_role_assignment" "github_app_operator" {
  scope                = azurerm_container_app.sovereign.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ghactions.principal_id
}

resource "azurerm_role_assignment" "github_public_env_operator" {
  scope                = azurerm_container_app_environment.public.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ghactions.principal_id
}

resource "azurerm_role_assignment" "github_public_app_operator" {
  scope                = azurerm_container_app.public.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ghactions.principal_id
}
