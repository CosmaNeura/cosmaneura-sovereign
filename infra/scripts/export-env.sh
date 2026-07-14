#!/usr/bin/env bash
#
# Resolve an env file (with 1Password op:// references) into a flat JSON object
# for Terraform's `data.external`. Stdout MUST be a single JSON object of
# string->string, so all human-readable chatter goes to stderr.
#
# Usage: export-env.sh PATH_TO_ENV_FILE
# Inspect locally: ./scripts/export-env.sh ../.env.production.backend | jq .
#
# Requires the 1Password CLI (`op`) authenticated via OP_SERVICE_ACCOUNT_TOKEN
# (CI) or an interactive session (local). Parsing is done in Python so values
# containing '=', quotes, or spaces survive intact.
set -euo pipefail

FILE="${1:?usage: export-env.sh <env-file>}"

if [[ ! -f "$FILE" ]]; then
  echo "env file not found: $FILE" >&2
  exit 1
fi

# `op inject` substitutes op:// references; non-secret lines pass through.
op inject -i "$FILE" 2>/dev/null | python3 -c '
import json, sys

out = {}
for raw in sys.stdin:
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("export "):
        line = line[len("export "):]
    if "=" not in line:
        continue
    key, val = line.split("=", 1)
    key = key.strip()
    val = val.strip()
    # strip a single layer of matching surrounding quotes
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("\x27", "\""):
        val = val[1:-1]
    out[key] = val

json.dump(out, sys.stdout)
'
