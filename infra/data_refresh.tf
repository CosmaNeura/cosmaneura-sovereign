########################################
# Partner-directory data refresh
#
# MyCatholicDoctor has no API, so a scheduled Container Apps Job scrapes the
# public per-state clinician listing into providers.json on an Azure Files
# share. The Sovereign service mounts the SAME share read-only and serves it via
# PARTNER_DIRECTORY_PATH — so the service stays stateless (no DB), the data
# refresh is decoupled from deploys, and updates are picked up live.
########################################

# Dedicated storage account for the shared file (name <=24 chars, alphanumeric).
resource "azurerm_storage_account" "data" {
  name                     = "cosmasov${local.environment}data"
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  tags                     = var.tags
}

resource "azurerm_storage_share" "partner_data" {
  name               = "partner-data"
  storage_account_id = azurerm_storage_account.data.id
  quota              = 1 # GB — the directory is a few hundred KB
}

# Register the share with the environment so apps/jobs can mount it.
resource "azurerm_container_app_environment_storage" "partner_data" {
  name                         = "partner-data"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.data.name
  share_name                   = azurerm_storage_share.partner_data.name
  access_key                   = azurerm_storage_account.data.primary_access_key
  access_mode                  = "ReadWrite"
}

# Scheduled scraper. Reuses the service image (which bundles the scraper +
# beautifulsoup/lxml) and writes providers.json to the shared volume.
resource "azurerm_container_app_job" "scraper" {
  name                         = "${local.base_name}-scraper"
  resource_group_name          = azurerm_resource_group.main.name
  location                     = azurerm_resource_group.main.location
  container_app_environment_id = azurerm_container_app_environment.main.id

  replica_timeout_in_seconds = 1800 # 50 states + DC with a polite delay
  replica_retry_limit        = 1

  schedule_trigger_config {
    # Weekly, Mondays 06:00 UTC. Adjust cadence here.
    cron_expression          = "0 6 * * 1"
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name   = "scraper"
      image  = "${azurerm_container_registry.main.login_server}/cosmaneura/sovereign:latest"
      cpu    = 0.5
      memory = "1Gi"

      command = ["sh", "-lc"]
      args    = ["python tools/scrape_mycatholicdoctor.py --out /data/providers.json"]

      volume_mounts {
        name = "partner-data"
        path = "/data"
      }
    }

    volume {
      name         = "partner-data"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.partner_data.name
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
}
