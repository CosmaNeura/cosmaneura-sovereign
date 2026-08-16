#!/usr/bin/env bash
# Verify exact runtime-secret inventories without reading any secret value.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROVIDER_VAULT="${PROVIDER_VAULT:-cosmasovprodprovider}"
PUBLIC_VAULT="${PUBLIC_VAULT:-cosmasovprodpublic}"

command -v az >/dev/null || {
  echo "az is required" >&2
  exit 1
}

verify_vault() {
  local vault="$1" expected_tag="$2" expected_names="$3"
  local actual_tag inventory
  actual_tag="$(az keyvault show \
    --name "$vault" --query 'tags.mount' --output tsv)"
  [[ "$actual_tag" == "$expected_tag" ]] || {
    echo "vault mount tag is invalid" >&2
    exit 1
  }
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

provider_expected="$(printf '%s\n' \
  athena-client-id \
  athena-client-secret \
  azure-openai-api-key \
  magisterium-api-key \
  sovereign-record-grant-secret | LC_ALL=C sort)"
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
"$HERE/verify-runtime-role-boundaries.sh"
echo "both Key Vault inventories match their exact mount allowlists" >&2
