output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "container_app" {
  value = azurerm_container_app.sovereign.name
}

output "public_container_app" {
  value = azurerm_container_app.public.name
}

output "container_registry" {
  value = azurerm_container_registry.main.login_server
}

# Public ingress hostname assigned by the environment (until a custom domain is
# bound — see ../DEPLOY_AZURE.md §3 for sovereign.cosmaneura.com).
output "ingress_fqdn" {
  value = azurerm_container_app.sovereign.ingress[0].fqdn
}

output "public_ingress_fqdn" {
  value = azurerm_container_app.public.ingress[0].fqdn
}

# Wire these into the GitHub repo (vars/secrets) for the deploy workflow.
output "gha_identity_client_id" {
  value = azurerm_user_assigned_identity.ghactions.client_id
}

output "tenant_id" {
  value = azurerm_user_assigned_identity.ghactions.tenant_id
}
