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
