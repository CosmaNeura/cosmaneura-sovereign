#!/usr/bin/env bash
# Verify temporary operator roles and exact runtime boundaries before data-plane use.
set -euo pipefail

: "${BOOTSTRAP_OPERATOR_OBJECT_ID:?BOOTSTRAP_OPERATOR_OBJECT_ID is required}"

RESOURCE_GROUP="${RESOURCE_GROUP:-cosmaneura-sovereign-prod}"
ACR_NAME="${ACR_NAME:-cosmaneurasovereignprodregistry}"
PROVIDER_IDENTITY="${PROVIDER_IDENTITY:-cosmaneura-sovereign-prod-runtime}"
PUBLIC_IDENTITY="${PUBLIC_IDENTITY:-cosmaneura-web-prod-runtime}"
PROVIDER_VAULT="${PROVIDER_VAULT:-cosmasovprodprovider}"
PUBLIC_VAULT="${PUBLIC_VAULT:-cosmasovprodpublic}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for command in az; do
  command -v "$command" >/dev/null || {
    echo "$command is required" >&2
    exit 1
  }
done

acr_scope="$(az acr show --name "$ACR_NAME" --query id --output tsv)"
provider_vault_scope="$(az keyvault show --name "$PROVIDER_VAULT" --query id --output tsv)"
public_vault_scope="$(az keyvault show --name "$PUBLIC_VAULT" --query id --output tsv)"
require_role() {
  local principal="$1" scope="$2" role="$3"
  local count
  count="$(az role assignment list \
    --assignee "$principal" --scope "$scope" \
    --query "[?roleDefinitionName=='$role'] | length(@)" --output tsv)"
  if [[ ! "$count" =~ ^[1-9][0-9]*$ ]]; then
    echo "missing required scoped role assignment: $role" >&2
    exit 1
  fi
  echo "verified role: $role" >&2
}

require_role "$BOOTSTRAP_OPERATOR_OBJECT_ID" "$acr_scope" "AcrPush"
require_role "$BOOTSTRAP_OPERATOR_OBJECT_ID" "$acr_scope" "Container Registry Tasks Contributor"
require_role "$BOOTSTRAP_OPERATOR_OBJECT_ID" "$provider_vault_scope" "Key Vault Secrets Officer"
require_role "$BOOTSTRAP_OPERATOR_OBJECT_ID" "$public_vault_scope" "Key Vault Secrets Officer"
"$HERE/verify-runtime-role-boundaries.sh"

echo "all bootstrap role assignments are visible; allow Azure data-plane propagation before continuing" >&2
