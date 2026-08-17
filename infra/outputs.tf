output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "container_app" {
  value = try(azurerm_container_app.sovereign[0].name, null)
}

output "public_container_app" {
  value = try(azurerm_container_app.public[0].name, null)
}

output "container_registry" {
  value = azurerm_container_registry.main.login_server
}

# Public ingress hostname assigned by the environment (until a custom domain is
# bound — see ../DEPLOY_AZURE.md §3 for sovereign.cosmaneura.com).
output "ingress_fqdn" {
  value = try(azurerm_container_app.sovereign[0].ingress[0].fqdn, null)
}

output "public_ingress_fqdn" {
  value = try(azurerm_container_app.public[0].ingress[0].fqdn, null)
}

output "provider_key_vault" {
  value = azurerm_key_vault.provider.name
}

output "public_key_vault" {
  value = azurerm_key_vault.public.name
}

output "provider_runtime_identity_client_id" {
  value = azurerm_user_assigned_identity.provider_runtime.client_id
}

output "public_runtime_identity_client_id" {
  value = azurerm_user_assigned_identity.public_runtime.client_id
}

# Wire these into the GitHub repo (vars/secrets) for the deploy workflow.
output "gha_identity_client_id" {
  value = azurerm_user_assigned_identity.ghactions.client_id
}

output "tenant_id" {
  value = azurerm_user_assigned_identity.ghactions.tenant_id
}
