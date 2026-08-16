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
for t in tofu az op curl git uv python3 tar; do
  if command -v "$t" >/dev/null 2>&1; then ok "$t installed"; else bad "$t missing"; fi
done

case "$(uname -s):$(uname -m)" in
  Darwin:arm64|Darwin:x86_64|Linux:x86_64|Linux:aarch64|Linux:arm64)
    ok "bootstrap platform has a checksum-pinned Nixpacks 1.41.0 artifact"
    ;;
  *) bad "bootstrap platform has no reviewed Nixpacks artifact" ;;
esac

echo "== Cloud / secret auth =="
if az account show >/dev/null 2>&1; then
  subscription_id="$(az account show --query id -o tsv 2>/dev/null)"
  if [[ "$subscription_id" == "bab2a709-1fcb-4c87-a9fc-802f7477ec13" ]]; then
    ok "az logged in to the expected subscription"
  else
    bad "az is logged in to the wrong subscription"
  fi
else
  bad "az not logged in — run 'az login' before bootstrap"
fi
if op whoami >/dev/null 2>&1; then
  ok "1Password (op) authenticated"
else
  bad "op not authenticated — needed to resolve op:// secret references"
fi
echo "== Config (provider + public) =="
deployment_gate_status=""
if deployment_gate_status="$(python3 "$HERE/validate-deployment-inputs.py" \
    "$ROOT/infra/deployment.auto.tfvars")"; then
  ok "persistent Terraform deployment gates are internally consistent"
else
  bad "infra/deployment.auto.tfvars is missing or unsafe (copy and validate the example)"
fi
if python3 "$HERE/validate-deployment-env.py" \
    provider "$ROOT/.env.production.backend" >/dev/null; then
  ok ".env.production.backend satisfies provider invariants"
else
  bad ".env.production.backend is missing or unsafe (run the validator directly)"
fi
if python3 "$HERE/validate-deployment-env.py" \
    public "$ROOT/.env.production.public" >/dev/null; then
  ok ".env.production.public satisfies public invariants"
else
  bad ".env.production.public is missing or unsafe (run the validator directly)"
fi

if [[ "$deployment_gate_status" == *"(enabled)" ]]; then
  echo "== Enabled deployment boundaries =="
  if "$HERE/verify-key-vault-boundaries.sh" >/dev/null 2>&1; then
    ok "vault inventories and direct/inherited runtime roles are mount-exact"
  else
    bad "vault inventory or runtime role boundary verification failed"
  fi
  if az containerapp show \
      --resource-group cosmaneura-sovereign-prod \
      --name cosmaneura-sovereign-prod-app >/dev/null 2>&1 &&
    az containerapp show \
      --resource-group cosmaneura-sovereign-prod \
      --name cosmaneura-web-prod-app >/dev/null 2>&1; then
    if RESOURCE_GROUP=cosmaneura-sovereign-prod \
      PROVIDER_APP=cosmaneura-sovereign-prod-app \
      PUBLIC_APP=cosmaneura-web-prod-app \
      "$HERE/verify-live-app-boundaries.sh" >/dev/null 2>&1; then
      ok "live app identities, vault references, and registry binds are mount-exact"
    else
      bad "live app identity or secret reference boundary verification failed"
    fi
  else
    warn "live app boundary check awaits the first reviewed paired creation"
  fi
fi

echo "== Terraform =="
if command -v tofu >/dev/null 2>&1; then
  ( cd infra && tofu fmt -check -recursive >/dev/null 2>&1 ) \
    && ok "tofu fmt clean" || warn "tofu fmt would reformat (run: tofu -chdir=infra fmt -recursive)"
  if ( cd infra && tofu init -backend=false -lockfile=readonly >/dev/null 2>&1 \
       && tofu validate >/dev/null 2>&1 ); then
    ok "tofu validate passes"
  else
    bad "tofu validate failed (run: cd infra && tofu init -backend=false -lockfile=readonly && tofu validate)"
  fi
fi

echo
echo "Summary: $pass ok, $warn warn, $fail fail"
[[ "$fail" -eq 0 ]] || { echo "Resolve [FAIL] items before deploying."; exit 1; }
echo "No hard blockers. Follow DEPLOY_CHECKLIST.md; do not run an ungated full apply."
