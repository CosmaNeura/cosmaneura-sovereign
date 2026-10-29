#!/usr/bin/env bash
# Exact, non-secret names for each physically isolated deployment boundary.
# Source this file, set DEPLOYMENT_ENVIRONMENT, then call load_deployment_context.

load_deployment_context() {
  : "${DEPLOYMENT_ENVIRONMENT:?DEPLOYMENT_ENVIRONMENT must be prod or staging}"
  case "$DEPLOYMENT_ENVIRONMENT" in
    prod)
      EXPECTED_ENV_FILE_SUFFIX="production"
      EXPECTED_GITHUB_ENVIRONMENT="production"
      ;;
    staging)
      EXPECTED_ENV_FILE_SUFFIX="staging"
      EXPECTED_GITHUB_ENVIRONMENT="staging"
      ;;
    *)
      echo "DEPLOYMENT_ENVIRONMENT must be exactly prod or staging" >&2
      return 2
      ;;
  esac

  EXPECTED_RESOURCE_GROUP="cosmaneura-sovereign-${DEPLOYMENT_ENVIRONMENT}"
  EXPECTED_ACR_NAME="cosmaneurasovereign${DEPLOYMENT_ENVIRONMENT}registry"
  EXPECTED_REGISTRY_SERVER="${EXPECTED_ACR_NAME}.azurecr.io"
  EXPECTED_PROVIDER_APP="${EXPECTED_RESOURCE_GROUP}-app"
  EXPECTED_PUBLIC_APP="cosmaneura-web-${DEPLOYMENT_ENVIRONMENT}-app"
  EXPECTED_PROVIDER_IDENTITY="${EXPECTED_RESOURCE_GROUP}-runtime"
  EXPECTED_PUBLIC_IDENTITY="cosmaneura-web-${DEPLOYMENT_ENVIRONMENT}-runtime"
  EXPECTED_PROVIDER_ENVIRONMENT="${EXPECTED_RESOURCE_GROUP}-app-environment"
  EXPECTED_PUBLIC_ENVIRONMENT="cosmaneura-web-${DEPLOYMENT_ENVIRONMENT}-app-environment"
  EXPECTED_PROVIDER_CONTAINER="${EXPECTED_RESOURCE_GROUP}-app-web"
  EXPECTED_PUBLIC_CONTAINER="cosmaneura-web-${DEPLOYMENT_ENVIRONMENT}-web"
  EXPECTED_PROVIDER_VAULT="cosmasov${DEPLOYMENT_ENVIRONMENT}provider"
  EXPECTED_PUBLIC_VAULT="cosmasov${DEPLOYMENT_ENVIRONMENT}public"
  EXPECTED_TFSTATE_KEY="sovereign-${DEPLOYMENT_ENVIRONMENT}.tfstate"
}

require_deployment_value() {
  local name="$1" actual="$2" expected="$3"
  if [[ "$actual" != "$expected" ]]; then
    echo "$name does not match the ${DEPLOYMENT_ENVIRONMENT} deployment boundary" >&2
    return 2
  fi
}

# Provider-only is a separate production preparation path. Callers must opt in
# explicitly; staging and the established paired path keep their dual default.
load_release_scope() {
  SOVEREIGN_RELEASE_SCOPE="${SOVEREIGN_RELEASE_SCOPE:-dual}"
  case "$SOVEREIGN_RELEASE_SCOPE" in
    dual) ;;
    provider-only)
      if [[ "$DEPLOYMENT_ENVIRONMENT" != "prod" ]]; then
        echo "provider-only release scope requires prod" >&2
        return 2
      fi
      ;;
    *)
      echo "SOVEREIGN_RELEASE_SCOPE must be exactly dual or provider-only" >&2
      return 2
      ;;
  esac
}

provider_only_app_inventory() {
  local inventory
  [[ "$SOVEREIGN_RELEASE_SCOPE" == "provider-only" ]] || return 2
  inventory="$(az containerapp list --resource-group "$EXPECTED_RESOURCE_GROUP" \
    --query '[].name' --output json)" || return 1
  python3 - "$inventory" "$EXPECTED_PROVIDER_APP" <<'PY'
import json
import sys

items = json.loads(sys.argv[1])
if (
    not isinstance(items, list)
    or any(not isinstance(item, str) or not item for item in items)
    or len(items) != len(set(items))
    or any(item != sys.argv[2] for item in items)
):
    raise SystemExit("provider-only release requires public and other apps absent")
print("present" if items else "absent")
PY
}
