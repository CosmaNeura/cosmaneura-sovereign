#!/usr/bin/env bash
#
# Pre-deploy readiness check for the Sovereign. Verifies tooling, cloud/secret
# auth, that the env file is complete, and that the Terraform is valid. Safe to
# run repeatedly; it changes nothing. Run from the repo root or infra/.
#
#   ./infra/scripts/preflight.sh
set -uo pipefail

# resolve repo root regardless of where we're called from
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT"

pass=0 warn=0 fail=0
ok()   { echo "  [ OK ] $1"; pass=$((pass+1)); }
warn() { echo "  [WARN] $1"; warn=$((warn+1)); }
bad()  { echo "  [FAIL] $1"; fail=$((fail+1)); }

echo "== Tooling =="
for t in tofu az yq op nixpacks docker python3 jq; do
  if command -v "$t" >/dev/null 2>&1; then ok "$t installed"; else bad "$t missing"; fi
done

echo "== Cloud / secret auth =="
if az account show >/dev/null 2>&1; then
  ok "az logged in ($(az account show --query name -o tsv 2>/dev/null))"
else
  warn "az not logged in — run 'az login' before 'tofu apply' / deploy"
fi
if op whoami >/dev/null 2>&1; then
  ok "1Password (op) authenticated"
else
  warn "op not authenticated — needed to resolve op:// secret references"
fi
if docker info >/dev/null 2>&1; then ok "docker daemon running"; else warn "docker daemon not running (needed to build the image)"; fi

check_env_file() {
  local env_file="$1" example="$2" label="$3"
  if [[ -f "$env_file" ]]; then
    ok "$label present"
    # Required keys = every assignment in the example (the canonical key set).
    required=$(grep -oE '^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=' "$example" \
             | sed -E 's/^[[:space:]]*(export[[:space:]]+)?//; s/=$//' | sort -u)
    resolved=$("$HERE/export-env.sh" "$env_file" 2>/dev/null | jq -r 'keys[]' 2>/dev/null | sort -u)
    if [[ -z "$resolved" ]]; then
      bad "$label could not be resolved (op auth? op:// refs valid?)"
    else
      missing=$(comm -23 <(echo "$required") <(echo "$resolved"))
      if [[ -z "$missing" ]]; then ok "$label has all required keys"; else
        bad "$label missing/unresolved keys: $(echo "$missing" | tr '\n' ' ')"; fi
    fi
  else
    bad "no $label — copy from $(basename "$example") and fill it in"
  fi
}

echo "== Config (provider + public) =="
check_env_file "$ROOT/.env.production.backend" \
  "$ROOT/.env.production.backend.example" ".env.production.backend"
check_env_file "$ROOT/.env.production.public" \
  "$ROOT/.env.production.public.example" ".env.production.public"

echo "== Terraform =="
if command -v tofu >/dev/null 2>&1; then
  ( cd infra && tofu fmt -check -recursive >/dev/null 2>&1 ) \
    && ok "tofu fmt clean" || warn "tofu fmt would reformat (run: tofu -chdir=infra fmt -recursive)"
  if ( cd infra && tofu init -backend=false >/dev/null 2>&1 && tofu validate >/dev/null 2>&1 ); then
    ok "tofu validate passes"
  else
    bad "tofu validate failed (run: cd infra && tofu init -backend=false && tofu validate)"
  fi
fi

echo
echo "Summary: $pass ok, $warn warn, $fail fail"
[[ "$fail" -eq 0 ]] || { echo "Resolve [FAIL] items before deploying."; exit 1; }
echo "No hard blockers. Address any [WARN] items, then: cd infra && tofu apply"
