#!/usr/bin/env bash
# Emit only explicitly allowlisted, non-secret configuration for data.external.
set -euo pipefail

ENVIRONMENT="${1:?usage: export-public-env.sh <prod|staging> <env-file> <comma-separated-keys> <provider|public>}"
FILE="${2:?usage: export-public-env.sh <prod|staging> <env-file> <comma-separated-keys> <provider|public>}"
KEYS="${3:?usage: export-public-env.sh <prod|staging> <env-file> <comma-separated-keys> <provider|public>}"
MOUNT="${4:?usage: export-public-env.sh <prod|staging> <env-file> <comma-separated-keys> <provider|public>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$FILE" ]]; then
  echo "env file not found: $FILE" >&2
  exit 1
fi

# Terraform must enforce the same complete mount contract as preflight; an
# operator cannot bypass environment-specific authority/public-isolation gates by
# skipping the runbook and calling `tofu apply` directly.
python3 "$HERE/validate-deployment-env.py" \
  "$ENVIRONMENT" "$MOUNT" "$FILE" >/dev/null

python3 - "$FILE" "$KEYS" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
required = {item for item in sys.argv[2].split(",") if item}
values: dict[str, str] = {}

for raw in path.read_text().splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("export "):
        line = line[len("export "):]
    if "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    if key not in required:
        continue
    if key in values:
        raise SystemExit(f"duplicate configuration key: {key}")
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    if value.startswith("op://"):
        raise SystemExit(f"secret reference forbidden in public configuration: {key}")
    values[key] = value

missing = sorted(required - values.keys())
if missing:
    raise SystemExit("missing configuration keys: " + ", ".join(missing))

json.dump(values, sys.stdout, sort_keys=True)
PY
