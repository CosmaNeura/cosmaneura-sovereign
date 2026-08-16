#!/usr/bin/env bash
# Prove each runtime identity has only its intended relevant data-plane roles.
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-cosmaneura-sovereign-prod}"
ACR_NAME="${ACR_NAME:-cosmaneurasovereignprodregistry}"
PROVIDER_IDENTITY="${PROVIDER_IDENTITY:-cosmaneura-sovereign-prod-runtime}"
PUBLIC_IDENTITY="${PUBLIC_IDENTITY:-cosmaneura-web-prod-runtime}"
PROVIDER_VAULT="${PROVIDER_VAULT:-cosmasovprodprovider}"
PUBLIC_VAULT="${PUBLIC_VAULT:-cosmasovprodpublic}"
DEID_SERVICE_ID="${DEID_SERVICE_ID:-/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/cosmaneura-prod/providers/Microsoft.HealthDataAIServices/deidServices/AnonymizerAPI}"

command -v az >/dev/null || {
  echo "az is required" >&2
  exit 1
}

acr_scope="$(az acr show --name "$ACR_NAME" --query id --output tsv)"
provider_vault_scope="$(az keyvault show --name "$PROVIDER_VAULT" --query id --output tsv)"
public_vault_scope="$(az keyvault show --name "$PUBLIC_VAULT" --query id --output tsv)"
provider_principal="$(az identity show \
  --resource-group "$RESOURCE_GROUP" --name "$PROVIDER_IDENTITY" \
  --query principalId --output tsv)"
public_principal="$(az identity show \
  --resource-group "$RESOURCE_GROUP" --name "$PUBLIC_IDENTITY" \
  --query principalId --output tsv)"

if [[ -z "$acr_scope" || -z "$provider_vault_scope" || -z "$public_vault_scope" ||
      -z "$provider_principal" || -z "$public_principal" ||
      "$provider_vault_scope" == "$public_vault_scope" ||
      "$provider_principal" == "$public_principal" ]]; then
  echo "runtime identities and vaults must be nonempty, distinct Azure resources" >&2
  exit 1
fi

assert_effective_roles() {
  local principal="$1" scope="$2" expected="$3" label="$4" assignments
  # Reject every unexpected direct OR inherited assignment, not only known
  # built-in role names. That also closes custom-role dataAction bypasses.
  assignments="$(az role assignment list \
    --assignee-object-id "$principal" --scope "$scope" --all --include-inherited \
    --fill-principal-name false \
    --query '[].{role:roleDefinitionName,scope:scope}' --output json)"
  python3 - "$assignments" "$expected" "$label" <<'PY'
import json
import sys

items = json.loads(sys.argv[1])
if not isinstance(items, list) or any(
    not isinstance(item, dict) or not isinstance(item.get("role"), str)
    for item in items
):
    raise SystemExit(f"could not prove role boundary: {sys.argv[3]}")
actual = sorted(item["role"] for item in items)
expected = sorted(role for role in sys.argv[2].splitlines() if role)
if actual != expected:
    raise SystemExit(f"unexpected direct or inherited role boundary: {sys.argv[3]}")
PY
}

assert_no_group_memberships() {
  local principal="$1" memberships
  # Group-derived assignments would not appear in the direct SP query above.
  # Fail closed unless Microsoft Graph proves this UAI belongs to no group.
  memberships="$(az rest --method get \
    --url "https://graph.microsoft.com/v1.0/servicePrincipals/$principal/transitiveMemberOf?%24select=id" \
    --query 'value[].id' --output json)"
  python3 - "$memberships" <<'PY'
import json
import sys

items = json.loads(sys.argv[1])
if not isinstance(items, list) or items:
    raise SystemExit("runtime managed identity must not inherit roles through groups")
PY
}

assert_no_group_memberships "$provider_principal"
assert_no_group_memberships "$public_principal"

assert_effective_roles "$provider_principal" "$acr_scope" "AcrPull" "provider ACR"
assert_effective_roles "$public_principal" "$acr_scope" "AcrPull" "public ACR"
assert_effective_roles \
  "$provider_principal" "$provider_vault_scope" "Key Vault Secrets User" \
  "provider identity/provider vault"
assert_effective_roles "$public_principal" "$provider_vault_scope" "" \
  "public identity/provider vault"
assert_effective_roles \
  "$public_principal" "$public_vault_scope" "Key Vault Secrets User" \
  "public identity/public vault"
assert_effective_roles "$provider_principal" "$public_vault_scope" "" \
  "provider identity/public vault"
assert_effective_roles \
  "$provider_principal" "$DEID_SERVICE_ID" "DeID Realtime Data User" \
  "provider identity/DeID"
assert_effective_roles "$public_principal" "$DEID_SERVICE_ID" "" \
  "public identity/DeID"

echo "runtime identities satisfy exact mount-specific role boundaries" >&2
