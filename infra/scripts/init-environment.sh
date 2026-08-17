#!/usr/bin/env bash
# Initialize exactly one environment's backend and prove the selected state key.
set -euo pipefail

DEPLOYMENT_ENVIRONMENT="${1:?usage: init-environment.sh <prod|staging>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA="$(cd "$HERE/.." && pwd)"
source "$HERE/deployment-context.sh"
load_deployment_context

# An auto-loaded file has higher precedence than TF_VAR. Refuse a conflicting
# or incomplete file before OpenTofu can select any backend.
python3 "$HERE/validate-deployment-inputs.py" \
  "$INFRA/deployment.auto.tfvars" "$DEPLOYMENT_ENVIRONMENT" >/dev/null
export TF_VAR_deployment_environment="$DEPLOYMENT_ENVIRONMENT"
tofu -chdir="$INFRA" init -reconfigure -lockfile=readonly
python3 "$HERE/verify-backend-context.py" "$DEPLOYMENT_ENVIRONMENT" "$INFRA"
