#!/usr/bin/env bash
# Verify exact runtime-secret inventories without reading any secret value.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/deployment-context.sh"
load_deployment_context

PROVIDER_VAULT="${PROVIDER_VAULT:-$EXPECTED_PROVIDER_VAULT}"
PUBLIC_VAULT="${PUBLIC_VAULT:-$EXPECTED_PUBLIC_VAULT}"
require_deployment_value PROVIDER_VAULT "$PROVIDER_VAULT" "$EXPECTED_PROVIDER_VAULT"
require_deployment_value PUBLIC_VAULT "$PUBLIC_VAULT" "$EXPECTED_PUBLIC_VAULT"

command -v az >/dev/null || {
  echo "az is required" >&2
  exit 1
}

verify_vault() {
  local vault="$1" expected_tag="$2" expected_names="$3"
  local metadata inventory
  metadata="$(az keyvault show \
    --name "$vault" \
    --query '{mount:tags.mount,rbac:properties.enableRbacAuthorization,access_policies:properties.accessPolicies}' \
    --output json)"
  python3 - "$metadata" "$expected_tag" <<'PY'
import json
import sys

metadata = json.loads(sys.argv[1])
if (
    not isinstance(metadata, dict)
    or metadata.get("mount") != sys.argv[2]
    or metadata.get("rbac") is not True
    or metadata.get("access_policies") != []
):
    raise SystemExit("vault must use exact mount tagging and RBAC-only authorization")
PY
  inventory="$(az keyvault secret list \
    --vault-name "$vault" \
    --query '[].{name:name,enabled:attributes.enabled,expires:attributes.expires}' \
    --output json)"
  python3 - "$inventory" "$expected_names" <<'PY'
import datetime as dt
import json
import sys

items = json.loads(sys.argv[1])
expected = sorted(name for name in sys.argv[2].splitlines() if name)
actual = sorted(item.get("name") for item in items)
if actual != expected:
    raise SystemExit("vault secret inventory is not the exact mount allowlist")
now = dt.datetime.now(dt.timezone.utc)
for item in items:
    if item.get("enabled") is not True:
        raise SystemExit("vault contains a disabled runtime secret")
    expires = item.get("expires")
    if expires:
        expiry = dt.datetime.fromisoformat(expires.replace("Z", "+00:00"))
        if expiry <= now:
            raise SystemExit("vault contains an expired runtime secret")
PY
}

provider_names=(
  azure-openai-api-key
  magisterium-api-key
  sovereign-record-grant-secret
)
if [[ "$DEPLOYMENT_ENVIRONMENT" == "staging" ]]; then
  provider_names+=(athena-client-id athena-client-secret)
fi
provider_expected="$(printf '%s\n' "${provider_names[@]}" | LC_ALL=C sort)"
public_expected="$(printf '%s\n' \
  azure-openai-api-key \
  magisterium-api-key | LC_ALL=C sort)"

provider_id="$(az keyvault show --name "$PROVIDER_VAULT" --query id --output tsv)"
public_id="$(az keyvault show --name "$PUBLIC_VAULT" --query id --output tsv)"
if [[ -z "$provider_id" || -z "$public_id" || "$provider_id" == "$public_id" ]]; then
  echo "provider and public vaults must be distinct Azure resources" >&2
  exit 1
fi

verify_vault "$PROVIDER_VAULT" provider_assistant "$provider_expected"
verify_vault "$PUBLIC_VAULT" public "$public_expected"
DEPLOYMENT_ENVIRONMENT="$DEPLOYMENT_ENVIRONMENT" \
  "$HERE/verify-runtime-role-boundaries.sh"
echo "both Key Vault inventories match their exact mount allowlists" >&2
