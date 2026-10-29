#!/usr/bin/env bash
# Verify live Container Apps are attached only to their mount-specific identity,
# registry identity, and versionless Key Vault secret references.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/deployment-context.sh"
load_deployment_context
load_release_scope

: "${RESOURCE_GROUP:?RESOURCE_GROUP is required}"
: "${PROVIDER_APP:?PROVIDER_APP is required}"
: "${PUBLIC_APP:?PUBLIC_APP is required}"

PROVIDER_IDENTITY="${PROVIDER_IDENTITY:-$EXPECTED_PROVIDER_IDENTITY}"
PUBLIC_IDENTITY="${PUBLIC_IDENTITY:-$EXPECTED_PUBLIC_IDENTITY}"
PROVIDER_ENVIRONMENT="${PROVIDER_ENVIRONMENT:-$EXPECTED_PROVIDER_ENVIRONMENT}"
PUBLIC_ENVIRONMENT="${PUBLIC_ENVIRONMENT:-$EXPECTED_PUBLIC_ENVIRONMENT}"
PROVIDER_CONTAINER="${PROVIDER_CONTAINER:-$EXPECTED_PROVIDER_CONTAINER}"
PUBLIC_CONTAINER="${PUBLIC_CONTAINER:-$EXPECTED_PUBLIC_CONTAINER}"
PROVIDER_VAULT="${PROVIDER_VAULT:-$EXPECTED_PROVIDER_VAULT}"
PUBLIC_VAULT="${PUBLIC_VAULT:-$EXPECTED_PUBLIC_VAULT}"
REGISTRY_SERVER="${REGISTRY_SERVER:-$EXPECTED_REGISTRY_SERVER}"

require_deployment_value RESOURCE_GROUP "$RESOURCE_GROUP" "$EXPECTED_RESOURCE_GROUP"
require_deployment_value PROVIDER_APP "$PROVIDER_APP" "$EXPECTED_PROVIDER_APP"
require_deployment_value PUBLIC_APP "$PUBLIC_APP" "$EXPECTED_PUBLIC_APP"
require_deployment_value PROVIDER_IDENTITY "$PROVIDER_IDENTITY" "$EXPECTED_PROVIDER_IDENTITY"
require_deployment_value PUBLIC_IDENTITY "$PUBLIC_IDENTITY" "$EXPECTED_PUBLIC_IDENTITY"
require_deployment_value PROVIDER_ENVIRONMENT "$PROVIDER_ENVIRONMENT" "$EXPECTED_PROVIDER_ENVIRONMENT"
require_deployment_value PUBLIC_ENVIRONMENT "$PUBLIC_ENVIRONMENT" "$EXPECTED_PUBLIC_ENVIRONMENT"
require_deployment_value PROVIDER_CONTAINER "$PROVIDER_CONTAINER" "$EXPECTED_PROVIDER_CONTAINER"
require_deployment_value PUBLIC_CONTAINER "$PUBLIC_CONTAINER" "$EXPECTED_PUBLIC_CONTAINER"
require_deployment_value PROVIDER_VAULT "$PROVIDER_VAULT" "$EXPECTED_PROVIDER_VAULT"
require_deployment_value PUBLIC_VAULT "$PUBLIC_VAULT" "$EXPECTED_PUBLIC_VAULT"
require_deployment_value REGISTRY_SERVER "$REGISTRY_SERVER" "$EXPECTED_REGISTRY_SERVER"

command -v az >/dev/null || {
  echo "az is required" >&2
  exit 1
}

app_boundary_json() {
  az containerapp show \
    --name "$1" --resource-group "$RESOURCE_GROUP" \
    --query '{environment_id:properties.environmentId,managed_environment_id:properties.managedEnvironmentId,identity:identity,registries:properties.configuration.registries[].{server:server,identity:identity},secrets:properties.configuration.secrets[].{name:name,key_vault_url:keyVaultUrl,identity:identity},containers:properties.template.containers[].{name:name,env:env[].{name:name,secret_ref:secretRef}}}' \
    --output json
}

verify_app() {
  local app="$1" identity_name="$2" environment_name="$3" container_name="$4"
  local vault_name="$5" secret_names="$6"
  local expected_identity_id expected_environment_id snapshot
  expected_identity_id="$(az identity show \
    --name "$identity_name" --resource-group "$RESOURCE_GROUP" \
    --query id --output tsv)"
  expected_environment_id="$(az containerapp env show \
    --name "$environment_name" --resource-group "$RESOURCE_GROUP" \
    --query id --output tsv)"
  [[ -n "$expected_identity_id" && -n "$expected_environment_id" ]] || {
    echo "could not resolve the exact runtime identity or app environment" >&2
    return 1
  }
  snapshot="$(app_boundary_json "$app")"
  python3 - \
    "$snapshot" "$expected_identity_id" "$expected_environment_id" \
    "$container_name" "$vault_name" "$REGISTRY_SERVER" "$secret_names" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
expected_identity_id, expected_environment_id, container_name, vault_name, registry = sys.argv[2:7]
secret_names = sorted(name for name in sys.argv[7].splitlines() if name)

identity = payload.get("identity") or {}
identity_ids = list((identity.get("userAssignedIdentities") or {}).keys())
if identity.get("type") != "UserAssigned" or len(identity_ids) != 1:
    raise SystemExit("app must have exactly one user-assigned runtime identity")
runtime_id = identity_ids[0]
if runtime_id.casefold() != expected_identity_id.casefold():
    raise SystemExit("app has the wrong mount runtime identity")
actual_environment_id = (
    payload.get("environment_id") or payload.get("managed_environment_id") or ""
)
if actual_environment_id.casefold() != expected_environment_id.casefold():
    raise SystemExit("app is attached to the wrong mount environment")

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

containers = payload.get("containers") or []
if (
    len(containers) != 1
    or not isinstance(containers[0], dict)
    or containers[0].get("name") != container_name
):
    raise SystemExit("app must have exactly one expected mount container")
container_env = containers[0].get("env") or []
env_names = [item.get("name") for item in container_env]
if (
    any(not isinstance(name, str) or not name for name in env_names)
    or len(env_names) != len(set(env_names))
):
    raise SystemExit("app container has invalid or duplicate environment names")
secret_env = {
    item.get("name"): item.get("secret_ref")
    for item in container_env
    if item.get("secret_ref") is not None
}
expected_env = {name.upper().replace("-", "_"): name for name in secret_names}
if secret_env != expected_env:
    raise SystemExit("app secret environment bindings are not the exact mount allowlist")
PY
}

provider_names=(azure-openai-api-key magisterium-api-key sovereign-record-grant-secret)
if [[ "$DEPLOYMENT_ENVIRONMENT" == "staging" ]]; then
  provider_names+=(athena-client-id athena-client-secret)
fi
provider_secrets="$(printf '%s\n' "${provider_names[@]}" | LC_ALL=C sort)"
public_secrets="$(printf '%s\n' azure-openai-api-key magisterium-api-key | LC_ALL=C sort)"

if [[ "$SOVEREIGN_RELEASE_SCOPE" == "provider-only" ]]; then
  [[ "$(provider_only_app_inventory)" == "present" ]] || {
    echo "provider-only live verification requires the sole provider app" >&2
    exit 1
  }
fi
verify_app \
  "$PROVIDER_APP" "$PROVIDER_IDENTITY" "$PROVIDER_ENVIRONMENT" \
  "$PROVIDER_CONTAINER" "$PROVIDER_VAULT" "$provider_secrets"
if [[ "$SOVEREIGN_RELEASE_SCOPE" == "dual" ]]; then
  verify_app \
    "$PUBLIC_APP" "$PUBLIC_IDENTITY" "$PUBLIC_ENVIRONMENT" \
    "$PUBLIC_CONTAINER" "$PUBLIC_VAULT" "$public_secrets"
fi
echo "live apps retain exact mount-specific identity and secret bindings" >&2
