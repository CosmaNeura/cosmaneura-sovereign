#!/usr/bin/env bash
# Seed one mount's Key Vault without placing secret values in Terraform state.
set -euo pipefail
umask 077

DEPLOYMENT_ENVIRONMENT="${1:?usage: sync-key-vault-secrets.sh <prod|staging> <provider|public> <vault-name>}"
MOUNT="${2:?usage: sync-key-vault-secrets.sh <prod|staging> <provider|public> <vault-name>}"
VAULT_NAME="${3:?usage: sync-key-vault-secrets.sh <prod|staging> <provider|public> <vault-name>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$ROOT/infra/scripts"
source "$HERE/deployment-context.sh"
load_deployment_context

case "$MOUNT" in
  provider)
    EXPECTED_VAULT="$EXPECTED_PROVIDER_VAULT"
    EXPECTED_TAG="provider_assistant"
    ENV_FILE="$ROOT/.env.${EXPECTED_ENV_FILE_SUFFIX}.backend"
    SECRET_NAMES=(
      MAGISTERIUM_API_KEY
      AZURE_OPENAI_API_KEY
      ATHENA_CLIENT_ID
      ATHENA_CLIENT_SECRET
      SOVEREIGN_RECORD_GRANT_SECRET
    )
    ;;
  public)
    EXPECTED_VAULT="$EXPECTED_PUBLIC_VAULT"
    EXPECTED_TAG="public"
    ENV_FILE="$ROOT/.env.${EXPECTED_ENV_FILE_SUFFIX}.public"
    SECRET_NAMES=(MAGISTERIUM_API_KEY AZURE_OPENAI_API_KEY)
    ;;
  *)
    echo "mount must be provider or public" >&2
    exit 2
    ;;
esac

if [[ "$VAULT_NAME" != "$EXPECTED_VAULT" ]]; then
  echo "vault name does not match the selected mount" >&2
  exit 2
fi

for command in az op python3; do
  command -v "$command" >/dev/null || {
    echo "$command is required" >&2
    exit 1
  }
done

if [[ ! -f "$ENV_FILE" ]]; then
  echo "env file not found: $ENV_FILE" >&2
  exit 1
fi
python3 "$ROOT/infra/scripts/validate-deployment-env.py" \
  "$DEPLOYMENT_ENVIRONMENT" "$MOUNT" "$ENV_FILE" >/dev/null

actual_tag="$(az keyvault show \
  --name "$VAULT_NAME" --query 'tags.mount' --output tsv)"
if [[ "$actual_tag" != "$EXPECTED_TAG" ]]; then
  echo "vault mount tag does not match the selected mount" >&2
  exit 2
fi

existing_names="$(az keyvault secret list \
  --vault-name "$VAULT_NAME" --query '[].name' --output tsv)"
allowed_names="$(printf '%s\n' "${SECRET_NAMES[@]}" \
  | tr '[:upper:]_' '[:lower:]-' | LC_ALL=C sort)"
while IFS= read -r existing; do
  [[ -z "$existing" ]] && continue
  if ! grep -Fqx "$existing" <<<"$allowed_names"; then
    echo "unexpected secret metadata is present in the selected vault" >&2
    exit 2
  fi
done <<<"$existing_names"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

for name in "${SECRET_NAMES[@]}"; do
  reference="$({ python3 - "$ENV_FILE" "$name" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
target = sys.argv[2]
matches = []
for raw in path.read_text().splitlines():
    line = raw.strip()
    if line.startswith("export "):
        line = line[len("export "):]
    if not line.startswith(target + "="):
        continue
    value = line.split("=", 1)[1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    matches.append(value)
if len(matches) != 1 or not matches[0].startswith("op://"):
    raise SystemExit(f"{target} must have exactly one op:// reference")
print(matches[0])
PY
  })"

  secret_file="$TMP_DIR/$name"
  op read --no-newline "$reference" >"$secret_file"
  if [[ "$name" == "SOVEREIGN_RECORD_GRANT_SECRET" ]]; then
    python3 - "$secret_file" <<'PY'
import base64
import binascii
import pathlib
import re
import sys

value = pathlib.Path(sys.argv[1]).read_text()
try:
    decoded = base64.b64decode(value, altchars=b"-_", validate=True)
except (binascii.Error, ValueError):
    decoded = b""
if (
    not re.fullmatch(r"[A-Za-z0-9_-]{43}=", value)
    or len(decoded) != 32
    or base64.urlsafe_b64encode(decoded).decode() != value
):
    raise SystemExit("record grant secret is not a canonical Fernet key")
PY
  fi
  azure_name="$(printf '%s' "$name" | tr '[:upper:]_' '[:lower:]-')"
  az keyvault secret set \
    --vault-name "$VAULT_NAME" \
    --name "$azure_name" \
    --file "$secret_file" \
    --encoding utf-8 \
    --output none
  rm -f "$secret_file"
  echo "seeded $azure_name in $VAULT_NAME" >&2
done
