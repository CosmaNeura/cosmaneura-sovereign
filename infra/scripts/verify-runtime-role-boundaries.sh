#!/usr/bin/env bash
# Prove each runtime identity has only its intended relevant data-plane roles.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/deployment-context.sh"
load_deployment_context

RESOURCE_GROUP="${RESOURCE_GROUP:-$EXPECTED_RESOURCE_GROUP}"
ACR_NAME="${ACR_NAME:-$EXPECTED_ACR_NAME}"
PROVIDER_IDENTITY="${PROVIDER_IDENTITY:-$EXPECTED_PROVIDER_IDENTITY}"
PUBLIC_IDENTITY="${PUBLIC_IDENTITY:-$EXPECTED_PUBLIC_IDENTITY}"
PROVIDER_VAULT="${PROVIDER_VAULT:-$EXPECTED_PROVIDER_VAULT}"
PUBLIC_VAULT="${PUBLIC_VAULT:-$EXPECTED_PUBLIC_VAULT}"
: "${DEID_SERVICE_ID:?DEID_SERVICE_ID is required}"

require_deployment_value RESOURCE_GROUP "$RESOURCE_GROUP" "$EXPECTED_RESOURCE_GROUP"
require_deployment_value ACR_NAME "$ACR_NAME" "$EXPECTED_ACR_NAME"
require_deployment_value PROVIDER_IDENTITY "$PROVIDER_IDENTITY" "$EXPECTED_PROVIDER_IDENTITY"
require_deployment_value PUBLIC_IDENTITY "$PUBLIC_IDENTITY" "$EXPECTED_PUBLIC_IDENTITY"
require_deployment_value PROVIDER_VAULT "$PROVIDER_VAULT" "$EXPECTED_PROVIDER_VAULT"
require_deployment_value PUBLIC_VAULT "$PUBLIC_VAULT" "$EXPECTED_PUBLIC_VAULT"

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

list_direct_roles() {
  # Fetch every direct assignment in the current subscription once per UAI.
  # Scope-local queries omit secret-level descendants; the validator below
  # deliberately treats both ancestors and descendants as relevant.
  az role assignment list \
    --assignee-object-id "$1" --all --fill-principal-name false \
    --query '[].{role:roleDefinitionName,scope:scope}' --output json
}

assert_direct_inventory() {
  local assignments="$1" expected="$2" label="$3"
  python3 - "$assignments" "$expected" "$label" <<'PY'
import json
import sys

items = json.loads(sys.argv[1])
if not isinstance(items, list) or any(
    not isinstance(item, dict)
    or not isinstance(item.get("role"), str)
    or not isinstance(item.get("scope"), str)
    for item in items
):
    raise SystemExit(f"could not prove direct role inventory: {sys.argv[3]}")
actual = sorted(
    (item["role"], item["scope"].rstrip("/").casefold()) for item in items
)
expected = sorted(
    (role, scope.rstrip("/").casefold())
    for line in sys.argv[2].splitlines()
    if line
    for role, scope in [line.split("\t", 1)]
)
if actual != expected:
    raise SystemExit(f"unexpected direct role assignment: {sys.argv[3]}")
PY
}

assert_scoped_roles() {
  local principal="$1" direct_assignments="$2" scope="$3" expected="$4" label="$5"
  local inherited_assignments
  # Require expected built-ins at the exact resource scope. Any custom role or
  # assignment at a parent/child scope fails closed. Azure resolves management
  # group and other ancestors for the target query; the subscription-wide list
  # separately catches per-secret descendants omitted by that query.
  inherited_assignments="$(az role assignment list \
    --assignee-object-id "$principal" --scope "$scope" --include-inherited \
    --fill-principal-name false \
    --query '[].{role:roleDefinitionName,scope:scope}' --output json)"
  python3 - \
    "$inherited_assignments" "$direct_assignments" "$scope" "$expected" "$label" <<'PY'
import json
import sys

inherited = json.loads(sys.argv[1])
direct = json.loads(sys.argv[2])

def valid(items: object) -> bool:
    return isinstance(items, list) and not any(
        not isinstance(item, dict)
        or not isinstance(item.get("role"), str)
        or not isinstance(item.get("scope"), str)
        for item in items
    )

if not valid(inherited) or not valid(direct):
    raise SystemExit(f"could not prove role boundary: {sys.argv[5]}")

target = sys.argv[3].rstrip("/").casefold()

actual = sorted(
    {
        (item["role"], item["scope"].rstrip("/").casefold())
        for item in inherited
    }
    | {
        (item["role"], item["scope"].rstrip("/").casefold())
        for item in direct
        if item["scope"].rstrip("/").casefold().startswith(target + "/")
    }
)
expected = sorted(
    (role, target) for role in sys.argv[4].splitlines() if role
)
if actual != expected:
    raise SystemExit(f"unexpected ancestor, resource, or descendant role boundary: {sys.argv[5]}")
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

provider_assignments="$(list_direct_roles "$provider_principal")"
public_assignments="$(list_direct_roles "$public_principal")"

provider_expected_roles="$(printf '%s\t%s\n' \
  "AcrPull" "$acr_scope" \
  "Key Vault Secrets User" "$provider_vault_scope" \
  "DeID Realtime Data User" "$DEID_SERVICE_ID")"
public_expected_roles="$(printf '%s\t%s\n' \
  "AcrPull" "$acr_scope" \
  "Key Vault Secrets User" "$public_vault_scope")"
assert_direct_inventory \
  "$provider_assignments" "$provider_expected_roles" "provider runtime identity"
assert_direct_inventory \
  "$public_assignments" "$public_expected_roles" "public runtime identity"

assert_scoped_roles \
  "$provider_principal" "$provider_assignments" "$acr_scope" "AcrPull" "provider ACR"
assert_scoped_roles \
  "$public_principal" "$public_assignments" "$acr_scope" "AcrPull" "public ACR"
assert_scoped_roles \
  "$provider_principal" "$provider_assignments" \
  "$provider_vault_scope" "Key Vault Secrets User" \
  "provider identity/provider vault"
assert_scoped_roles \
  "$public_principal" "$public_assignments" "$provider_vault_scope" "" \
  "public identity/provider vault"
assert_scoped_roles \
  "$public_principal" "$public_assignments" \
  "$public_vault_scope" "Key Vault Secrets User" \
  "public identity/public vault"
assert_scoped_roles \
  "$provider_principal" "$provider_assignments" "$public_vault_scope" "" \
  "provider identity/public vault"
assert_scoped_roles \
  "$provider_principal" "$provider_assignments" \
  "$DEID_SERVICE_ID" "DeID Realtime Data User" \
  "provider identity/DeID"
assert_scoped_roles \
  "$public_principal" "$public_assignments" "$DEID_SERVICE_ID" "" \
  "public identity/DeID"

echo "runtime identities satisfy exact mount-specific role boundaries" >&2
