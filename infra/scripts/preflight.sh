#!/usr/bin/env bash
#
# Pre-deploy readiness check for the Sovereign. Verifies tooling, cloud/secret
# auth, that the env file is complete, and that the Terraform is valid. Safe to
# run repeatedly; it changes nothing. Run from the repo root or infra/.
#
#   ./infra/scripts/preflight.sh <prod|staging> [dual|provider-only]
set -uo pipefail

# resolve repo root regardless of where we're called from
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DEPLOYMENT_ENVIRONMENT="${1:?usage: preflight.sh <prod|staging> [dual|provider-only]}"
if (( $# > 2 )); then
  echo "usage: preflight.sh <prod|staging> [dual|provider-only]" >&2
  exit 2
fi
SOVEREIGN_RELEASE_SCOPE="${2:-${SOVEREIGN_RELEASE_SCOPE:-dual}}"
source "$HERE/deployment-context.sh"
load_deployment_context || exit $?
load_release_scope || exit $?
export DEPLOYMENT_ENVIRONMENT SOVEREIGN_RELEASE_SCOPE
export TF_VAR_deployment_environment="$DEPLOYMENT_ENVIRONMENT"
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
echo "== Config ($SOVEREIGN_RELEASE_SCOPE) =="
deployment_gate_status=""
if deployment_gate_status="$(python3 "$HERE/validate-deployment-inputs.py" \
    "$ROOT/infra/deployment.auto.tfvars" "$DEPLOYMENT_ENVIRONMENT")"; then
  ok "persistent Terraform deployment gates are internally consistent"
else
  bad "infra/deployment.auto.tfvars is missing or unsafe (copy and validate the example)"
fi
if [[ "$deployment_gate_status" == *"provider-only enabled)" &&
      "$SOVEREIGN_RELEASE_SCOPE" != "provider-only" ]] ||
   [[ "$deployment_gate_status" == *"(prod enabled)" &&
      "$SOVEREIGN_RELEASE_SCOPE" == "provider-only" ]]; then
  bad "release scope conflicts with the persistent app/readiness controls"
fi
if python3 "$HERE/validate-deployment-env.py" \
    "$DEPLOYMENT_ENVIRONMENT" provider \
    "$ROOT/.env.${EXPECTED_ENV_FILE_SUFFIX}.backend" >/dev/null; then
  ok ".env.${EXPECTED_ENV_FILE_SUFFIX}.backend satisfies provider invariants"
else
  bad ".env.${EXPECTED_ENV_FILE_SUFFIX}.backend is missing or unsafe (run the validator directly)"
fi
if [[ "$SOVEREIGN_RELEASE_SCOPE" == "provider-only" ]]; then
  ok "public env is excluded from provider-only preparation"
else
  if python3 "$HERE/validate-deployment-env.py" \
      "$DEPLOYMENT_ENVIRONMENT" public \
      "$ROOT/.env.${EXPECTED_ENV_FILE_SUFFIX}.public" >/dev/null; then
    ok ".env.${EXPECTED_ENV_FILE_SUFFIX}.public satisfies public invariants"
  else
    bad ".env.${EXPECTED_ENV_FILE_SUFFIX}.public is missing or unsafe (run the validator directly)"
  fi
fi

if python3 "$HERE/verify-backend-context.py" \
    "$DEPLOYMENT_ENVIRONMENT" "$ROOT/infra" >/dev/null; then
  ok "initialized backend is pinned to $EXPECTED_TFSTATE_KEY"
else
  bad "backend is not initialized for $DEPLOYMENT_ENVIRONMENT (run init-environment.sh)"
fi

if [[ "$deployment_gate_status" == *" enabled)" ]]; then
  echo "== Enabled deployment boundaries =="
  deid_service_id="$(tofu -chdir="$ROOT/infra" output -raw azure_deid_service_id)"
  if DEID_SERVICE_ID="$deid_service_id" \
    "$HERE/verify-key-vault-boundaries.sh" >/dev/null 2>&1; then
    ok "vault inventories and direct/inherited runtime roles are mount-exact"
  else
    bad "vault inventory or runtime role boundary verification failed"
  fi
  if [[ "$SOVEREIGN_RELEASE_SCOPE" == "provider-only" ]]; then
    if app_inventory="$(provider_only_app_inventory)"; then
      if [[ "$app_inventory" == "present" ]]; then
        if RESOURCE_GROUP="$EXPECTED_RESOURCE_GROUP" \
          PROVIDER_APP="$EXPECTED_PROVIDER_APP" PUBLIC_APP="$EXPECTED_PUBLIC_APP" \
          REGISTRY_SERVER="$EXPECTED_REGISTRY_SERVER" \
          "$HERE/verify-live-app-boundaries.sh" >/dev/null 2>&1; then
          ok "provider app binds are mount-exact and public app is absent"
        else
          bad "provider app identity or secret reference boundary verification failed"
        fi
      else
        warn "live provider boundary check awaits the first reviewed provider-only creation"
      fi
    else
      bad "provider-only app inventory could not prove the public app absent"
    fi
  elif az containerapp show \
      --resource-group "$EXPECTED_RESOURCE_GROUP" \
      --name "$EXPECTED_PROVIDER_APP" >/dev/null 2>&1 &&
    az containerapp show \
      --resource-group "$EXPECTED_RESOURCE_GROUP" \
      --name "$EXPECTED_PUBLIC_APP" >/dev/null 2>&1; then
    if RESOURCE_GROUP="$EXPECTED_RESOURCE_GROUP" \
      PROVIDER_APP="$EXPECTED_PROVIDER_APP" \
      PUBLIC_APP="$EXPECTED_PUBLIC_APP" \
      REGISTRY_SERVER="$EXPECTED_REGISTRY_SERVER" \
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
  if ( cd infra && tofu validate >/dev/null 2>&1 ); then
    ok "tofu validate passes"
  else
    bad "tofu validate failed after environment-specific initialization"
  fi
fi

echo
echo "Summary: $pass ok, $warn warn, $fail fail"
[[ "$fail" -eq 0 ]] || { echo "Resolve [FAIL] items before deploying."; exit 1; }
echo "No hard blockers. Follow DEPLOY_CHECKLIST.md; do not run an ungated full apply."
