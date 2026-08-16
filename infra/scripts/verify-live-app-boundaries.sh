#!/usr/bin/env bash
# Verify live Container Apps are attached only to their mount-specific identity,
# registry identity, and versionless Key Vault secret references.
set -euo pipefail

: "${RESOURCE_GROUP:?RESOURCE_GROUP is required}"
: "${PROVIDER_APP:?PROVIDER_APP is required}"
: "${PUBLIC_APP:?PUBLIC_APP is required}"

PROVIDER_IDENTITY="${PROVIDER_IDENTITY:-cosmaneura-sovereign-prod-runtime}"
PUBLIC_IDENTITY="${PUBLIC_IDENTITY:-cosmaneura-web-prod-runtime}"
PROVIDER_VAULT="${PROVIDER_VAULT:-cosmasovprodprovider}"
PUBLIC_VAULT="${PUBLIC_VAULT:-cosmasovprodpublic}"
REGISTRY_SERVER="${REGISTRY_SERVER:-cosmaneurasovereignprodregistry.azurecr.io}"

command -v az >/dev/null || {
  echo "az is required" >&2
  exit 1
}

app_boundary_json() {
  az containerapp show \
    --name "$1" --resource-group "$RESOURCE_GROUP" \
    --query '{identity:identity,registries:properties.configuration.registries[].{server:server,identity:identity},secrets:properties.configuration.secrets[].{name:name,key_vault_url:keyVaultUrl,identity:identity},env:properties.template.containers[0].env[].{name:name,secret_ref:secretRef}}' \
    --output json
}

verify_app() {
  local app="$1" identity_name="$2" vault_name="$3" secret_names="$4" snapshot
  snapshot="$(app_boundary_json "$app")"
  python3 - \
    "$snapshot" "$RESOURCE_GROUP" "$identity_name" "$vault_name" \
    "$REGISTRY_SERVER" "$secret_names" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
resource_group, identity_name, vault_name, registry = sys.argv[2:6]
secret_names = sorted(name for name in sys.argv[6].splitlines() if name)
expected_suffix = (
    f"/resourcegroups/{resource_group.casefold()}"
    f"/providers/microsoft.managedidentity/userassignedidentities/"
    f"{identity_name.casefold()}"
)

identity = payload.get("identity") or {}
identity_ids = list((identity.get("userAssignedIdentities") or {}).keys())
if identity.get("type") != "UserAssigned" or len(identity_ids) != 1:
    raise SystemExit("app must have exactly one user-assigned runtime identity")
runtime_id = identity_ids[0]
if not runtime_id.casefold().endswith(expected_suffix):
    raise SystemExit("app has the wrong mount runtime identity")

registries = payload.get("registries") or []
if len(registries) != 1:
    raise SystemExit("app must have exactly one managed-identity registry binding")
if (
    registries[0].get("server") != registry
    or (registries[0].get("identity") or "").casefold() != runtime_id.casefold()
):
    raise SystemExit("app registry binding does not use its runtime identity")

secrets = payload.get("secrets") or []
if sorted(item.get("name") for item in secrets) != secret_names:
    raise SystemExit("app secret inventory is not the exact mount allowlist")
for item in secrets:
    name = item.get("name")
    expected_url = f"https://{vault_name}.vault.azure.net/secrets/{name}"
    if (
        item.get("key_vault_url") != expected_url
        or (item.get("identity") or "").casefold() != runtime_id.casefold()
    ):
        raise SystemExit("app secret reference crossed its identity or vault boundary")

secret_env = {
    item.get("name"): item.get("secret_ref")
    for item in (payload.get("env") or [])
    if item.get("secret_ref") is not None
}
expected_env = {name.upper().replace("-", "_"): name for name in secret_names}
if secret_env != expected_env:
    raise SystemExit("app secret environment bindings are not the exact mount allowlist")
PY
}

provider_secrets="$(printf '%s\n' \
  athena-client-id athena-client-secret azure-openai-api-key magisterium-api-key \
  sovereign-record-grant-secret \
  | LC_ALL=C sort)"
public_secrets="$(printf '%s\n' azure-openai-api-key magisterium-api-key | LC_ALL=C sort)"

verify_app \
  "$PROVIDER_APP" "$PROVIDER_IDENTITY" "$PROVIDER_VAULT" "$provider_secrets"
verify_app "$PUBLIC_APP" "$PUBLIC_IDENTITY" "$PUBLIC_VAULT" "$public_secrets"
echo "live apps retain exact mount-specific identity and secret bindings" >&2
