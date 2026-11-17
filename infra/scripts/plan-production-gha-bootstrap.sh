#!/usr/bin/env bash
# Produce, verify, and display the one-purpose production RG/OIDC bootstrap plan.
# This script never applies the plan.
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA="$(cd "$HERE/.." && pwd)"
PLAN_FILE="${1:-$INFRA/sovereign-prod-rg-gha-identity.tfplan}"

case "$PLAN_FILE" in
  /*) ;;
  *) PLAN_FILE="$(pwd)/$PLAN_FILE" ;;
esac

if [[ ! -d "$(dirname "$PLAN_FILE")" ]]; then
  echo "plan directory does not exist: $(dirname "$PLAN_FILE")" >&2
  exit 1
fi

# The shared input validator requires production, enables the GHA identity,
# and keeps both externally reachable apps disabled.
python3 "$HERE/validate-deployment-inputs.py" \
  "$INFRA/deployment.auto.tfvars" prod >/dev/null
"$HERE/init-environment.sh" prod

export TF_VAR_deployment_environment="prod"
export TF_VAR_github_repository="CosmaNeura/cosmaneura-sovereign"

# Target only the two leaf resources. Their dependency graph adds the state
# guard, resource group, and user-assigned identity—nothing from the broader
# registry, vault, logging, DeID, or Container Apps foundation.
tofu -chdir="$INFRA" plan \
  -input=false \
  -lock-timeout=60s \
  -out="$PLAN_FILE" \
  -target='azurerm_federated_identity_credential.ghactions[0]' \
  -target='azurerm_role_assignment.github_resource_group_reader[0]'

tofu -chdir="$INFRA" show -json "$PLAN_FILE" \
  | python3 "$HERE/verify-production-gha-bootstrap-plan.py"

echo
echo "Verified saved plan (not applied): $PLAN_FILE"
echo "A human must explicitly authorize: tofu -chdir=infra apply $PLAN_FILE"
echo
tofu -chdir="$INFRA" show -no-color "$PLAN_FILE"
