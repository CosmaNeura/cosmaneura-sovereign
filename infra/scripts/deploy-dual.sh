#!/usr/bin/env bash
# Deploy one immutable image digest to both mounts, or roll both back.
set -euo pipefail

: "${RESOURCE_GROUP:?RESOURCE_GROUP is required}"
: "${PROVIDER_APP:?PROVIDER_APP is required}"
: "${PUBLIC_APP:?PUBLIC_APP is required}"
: "${IMAGE_REF:?IMAGE_REF is required}"
: "${REVISION_SUFFIX:?REVISION_SUFFIX is required}"
: "${SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED:?SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED is required}"

SOVEREIGN_INTERACTIVE_SMOKE_CREDENTIALS="${SOVEREIGN_INTERACTIVE_SMOKE_CREDENTIALS:-false}"
if [[ "$SOVEREIGN_INTERACTIVE_SMOKE_CREDENTIALS" == "true" ]]; then
  if [[ ! -r /dev/tty || ! -w /dev/tty ]]; then
    echo "interactive smoke credentials require a controlling terminal" >&2
    exit 2
  fi
elif [[ "$SOVEREIGN_INTERACTIVE_SMOKE_CREDENTIALS" == "false" ]]; then
  : "${SOVEREIGN_PROVIDER_TOKEN:?SOVEREIGN_PROVIDER_TOKEN is required for transactional smoke}"
  : "${SOVEREIGN_NON_PROVIDER_TOKEN:?SOVEREIGN_NON_PROVIDER_TOKEN is required for transactional smoke}"
  : "${SOVEREIGN_SYNTHETIC_RECORD_GRANT:?SOVEREIGN_SYNTHETIC_RECORD_GRANT is required for transactional smoke}"
else
  echo "SOVEREIGN_INTERACTIVE_SMOKE_CREDENTIALS must be true or false" >&2
  exit 2
fi

# Retain short-lived values only in unexported shell variables until each smoke
# process starts. Azure CLI, curl health checks, and rollback commands must not
# inherit them even in the non-interactive compatibility mode.
provider_smoke_token="${SOVEREIGN_PROVIDER_TOKEN:-}"
non_provider_smoke_token="${SOVEREIGN_NON_PROVIDER_TOKEN:-}"
record_smoke_grant="${SOVEREIGN_SYNTHETIC_RECORD_GRANT:-}"
unset SOVEREIGN_PROVIDER_TOKEN SOVEREIGN_NON_PROVIDER_TOKEN
unset SOVEREIGN_SYNTHETIC_RECORD_GRANT

if [[ "$SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED" != "true" ]]; then
  echo "SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED must be exactly true" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SMOKE_PYTHON="${SMOKE_PYTHON:-python3}"
REGISTRY_SERVER="${REGISTRY_SERVER:-cosmaneurasovereignprodregistry.azurecr.io}"
IMAGE_REPO="${IMAGE_REPO:-cosmaneura/sovereign}"
command -v "$SMOKE_PYTHON" >/dev/null || {
  echo "SMOKE_PYTHON must name an executable Python with locked runtime dependencies" >&2
  exit 2
}
if ! PYTHONPATH="$ROOT" "$SMOKE_PYTHON" -c \
    'import httpx; from app.clients.labs import SANDBOX_LABEL' >/dev/null 2>&1; then
  echo "SMOKE_PYTHON cannot import the locked deployed-smoke dependencies" >&2
  exit 2
fi

expected_image_prefix="$REGISTRY_SERVER/$IMAGE_REPO@sha256:"
image_digest="${IMAGE_REF#"$expected_image_prefix"}"
if [[ "$IMAGE_REF" == "$image_digest" || ! "$image_digest" =~ ^[0-9a-f]{64}$ ]]; then
  echo "IMAGE_REF must be an immutable digest in the deployment ACR repository" >&2
  exit 2
fi
if [[ ! "$REVISION_SUFFIX" =~ ^[a-z0-9][a-z0-9-]{0,47}$ ]]; then
  echo "REVISION_SUFFIX must be 1-48 lowercase alphanumeric/hyphen characters" >&2
  exit 2
fi
for app in "$PROVIDER_APP" "$PUBLIC_APP"; do
  # Azure's full revision name is <app>--<suffix> and is capped at 64 chars.
  if (( ${#app} + ${#REVISION_SUFFIX} + 7 > 64 )); then
    echo "application name and revision suffix are too long for rollback" >&2
    exit 2
  fi
done

active_healthy_revision() {
  local revisions
  revisions="$(az containerapp revision list \
    --name "$1" --resource-group "$RESOURCE_GROUP" --all --output json)"
  python3 - "$revisions" <<'PY'
import json
import sys

items = json.loads(sys.argv[1])
matches = []
for item in items:
    properties = item.get("properties") or {}
    active = item.get("active", properties.get("active"))
    health = item.get("healthState", properties.get("healthState"))
    provisioning = item.get(
        "provisioningState", properties.get("provisioningState")
    )
    traffic = item.get("trafficWeight", properties.get("trafficWeight"))
    if (
        active is True
        and health == "Healthy"
        and provisioning in {"Provisioned", "Succeeded"}
        and traffic == 100
    ):
        matches.append(item.get("name") or properties.get("name"))
if len(matches) != 1 or not matches[0]:
    raise SystemExit("expected exactly one active healthy 100%-traffic revision")
print(matches[0])
PY
}

revision_image() {
  az containerapp revision show \
    --name "$1" --resource-group "$RESOURCE_GROUP" --revision "$2" \
    --query 'properties.template.containers[0].image' --output tsv
}

current_image() {
  local app="$1" revision
  revision="$(active_healthy_revision "$app")"
  revision_image "$app" "$revision"
}

app_fqdn() {
  az containerapp show \
    --name "$1" --resource-group "$RESOURCE_GROUP" \
    --query 'properties.configuration.ingress.fqdn' --output tsv
}

update_app() {
  local app="$1" image="$2" suffix="$3"
  az containerapp update \
    --name "$app" --resource-group "$RESOURCE_GROUP" \
    --image "$image" --revision-suffix "$suffix" --output none
}

verify_revision() {
  local app="$1" image="$2" suffix="$3"
  local expected_revision="${app}--${suffix}"
  local active_revision actual_image
  active_revision="$(active_healthy_revision "$app")"
  [[ "$active_revision" == "$expected_revision" ]] || return 1
  actual_image="$(revision_image "$app" "$expected_revision")"
  [[ "$actual_image" == "$image" ]]
}

smoke_mount() {
  local app="$1" expected_mount="$2"
  local fqdn body
  fqdn="$(app_fqdn "$app")"
  [[ -n "$fqdn" ]] || return 1
  body="$(curl --fail --silent --show-error \
    --retry 12 --retry-all-errors --retry-delay 5 --max-time 15 \
    "https://$fqdn/healthz")"
  python3 - "$expected_mount" "$body" <<'PY'
import json
import sys

expected = sys.argv[1]
payload = json.loads(sys.argv[2])
if payload.get("status") != "ok":
    raise SystemExit("health status is not ok")
if payload.get("mock_mode") is not False:
    raise SystemExit("MOCK_MODE must be false")
if payload.get("mount_profile") != expected:
    raise SystemExit("unexpected mount profile")
PY
}


verify_and_smoke() {
  local app="$1" expected_mount="$2" image="$3" suffix="$4"
  local attempt
  for attempt in {1..24}; do
    if verify_revision "$app" "$image" "$suffix"; then
      smoke_mount "$app" "$expected_mount"
      return
    fi
    sleep 5
  done
  return 1
}

functional_smoke() {
  local public_fqdn provider_fqdn
  public_fqdn="$(app_fqdn "$PUBLIC_APP")"
  provider_fqdn="$(app_fqdn "$PROVIDER_APP")"
  [[ -n "$public_fqdn" && -n "$provider_fqdn" ]] || return 1
  export SOVEREIGN_PUBLIC_URL="https://$public_fqdn"
  export SOVEREIGN_PROVIDER_URL="https://$provider_fqdn"
  if [[ "$SOVEREIGN_INTERACTIVE_SMOKE_CREDENTIALS" == "true" ]]; then
    # These credentials are intentionally requested only after the new
    # revisions are healthy. Reading from the controlling terminal keeps them
    # out of argv, shell history, workflow logs, and persistent secrets.
    IFS= read -r -s -p "Fresh provider-role Clerk JWT: " \
      provider_smoke_token </dev/tty || return 1
    printf '\n' >/dev/tty
    IFS= read -r -s -p "Fresh non-provider Clerk JWT: " \
      non_provider_smoke_token </dev/tty || return 1
    printf '\n' >/dev/tty
  fi
  PYTHONPATH="$ROOT" \
    SOVEREIGN_PROVIDER_TOKEN="$provider_smoke_token" \
    SOVEREIGN_NON_PROVIDER_TOKEN="$non_provider_smoke_token" \
    "$SMOKE_PYTHON" "$ROOT/tools/smoke_live.py" || return 1
  if [[ "$SOVEREIGN_INTERACTIVE_SMOKE_CREDENTIALS" == "true" ]]; then
    # Mint/paste the 60-second record grant only after the live-model checks,
    # immediately before the one bounded EHR request.
    IFS= read -r -s -p "Fresh synthetic record grant: " \
      record_smoke_grant </dev/tty || return 1
    printf '\n' >/dev/tty
  fi
  PYTHONPATH="$ROOT" \
    SOVEREIGN_PROVIDER_TOKEN="$provider_smoke_token" \
    SOVEREIGN_SYNTHETIC_RECORD_GRANT="$record_smoke_grant" \
    "$SMOKE_PYTHON" "$ROOT/tools/smoke_labs.py"
}

"$ROOT/infra/scripts/verify-live-app-boundaries.sh"

old_provider="$(current_image "$PROVIDER_APP")"
old_public="$(current_image "$PUBLIC_APP")"
old_provider_digest="${old_provider#"$expected_image_prefix"}"
old_public_digest="${old_public#"$expected_image_prefix"}"
if [[ "$old_provider" == "$old_provider_digest" ||
      ! "$old_provider_digest" =~ ^[0-9a-f]{64}$ ||
      "$old_public" == "$old_public_digest" ||
      ! "$old_public_digest" =~ ^[0-9a-f]{64}$ ]]; then
  echo "both applications must have an immutable previous image in the deployment ACR for rollback" >&2
  exit 1
fi

rollback() {
  local failed=0
  echo "deployment verification failed; rolling back both mounts" >&2
  update_app "$PROVIDER_APP" "$old_provider" "rb-${REVISION_SUFFIX}-p" || failed=1
  update_app "$PUBLIC_APP" "$old_public" "rb-${REVISION_SUFFIX}-u" || failed=1
  verify_and_smoke \
    "$PROVIDER_APP" provider_assistant "$old_provider" "rb-${REVISION_SUFFIX}-p" || failed=1
  verify_and_smoke \
    "$PUBLIC_APP" public "$old_public" "rb-${REVISION_SUFFIX}-u" || failed=1
  return "$failed"
}

deployment_started=0
deployment_finished=0
rollback_running=0

on_exit() {
  local status=$?
  trap - EXIT
  trap '' HUP INT TERM
  if (( deployment_started && ! deployment_finished && ! rollback_running )); then
    rollback_running=1
    rollback || {
      echo "paired rollback failed and requires operator intervention" >&2
      exit 2
    }
  fi
  exit "$status"
}

on_signal() {
  exit 130
}

trap on_exit EXIT
trap on_signal HUP INT TERM
deployment_started=1

if ! update_app "$PROVIDER_APP" "$IMAGE_REF" "${REVISION_SUFFIX}-p" ||
   ! update_app "$PUBLIC_APP" "$IMAGE_REF" "${REVISION_SUFFIX}-u" ||
   ! verify_and_smoke \
     "$PROVIDER_APP" provider_assistant "$IMAGE_REF" "${REVISION_SUFFIX}-p" ||
   ! verify_and_smoke \
     "$PUBLIC_APP" public "$IMAGE_REF" "${REVISION_SUFFIX}-u" ||
   ! "$ROOT/infra/scripts/verify-live-app-boundaries.sh" ||
   ! functional_smoke; then
  exit 1
fi

deployment_finished=1
trap - EXIT HUP INT TERM
echo "both mounts passed health, identity, live-model, and synthetic-labs gates on $IMAGE_REF" >&2
