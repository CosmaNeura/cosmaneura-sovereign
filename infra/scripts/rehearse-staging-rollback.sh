#!/usr/bin/env bash
# Run the enabled live smokes on a second staging digest, intentionally fail
# only afterward, prove deploy-dual restored both prior revisions, and retain
# a secret-free JSON evidence record.
set -euo pipefail
umask 077

: "${IMAGE_REF:?IMAGE_REF is required}"
: "${REVISION_SUFFIX:?REVISION_SUFFIX is required}"
: "${REHEARSAL_EVIDENCE_PATH:?REHEARSAL_EVIDENCE_PATH is required}"
: "${SOVEREIGN_PLATFORM_URL:?SOVEREIGN_PLATFORM_URL is required}"

SOVEREIGN_RUN_LABS_SMOKE="${SOVEREIGN_RUN_LABS_SMOKE:-false}"
if [[ "$SOVEREIGN_RUN_LABS_SMOKE" == "true" ]]; then
  : "${SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED:?SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED is required}"
  : "${SOVEREIGN_SYNTHETIC_PLATFORM_RECORD_REF:?SOVEREIGN_SYNTHETIC_PLATFORM_RECORD_REF is required}"
elif [[ "$SOVEREIGN_RUN_LABS_SMOKE" != "false" ]]; then
  echo "SOVEREIGN_RUN_LABS_SMOKE must be true or false" >&2
  exit 2
fi

# Keep smoke credentials out of every command invoked by this wrapper.
provider_smoke_token="${SOVEREIGN_PROVIDER_TOKEN:-}"
non_provider_smoke_token="${SOVEREIGN_NON_PROVIDER_TOKEN:-}"
record_smoke_grant="${SOVEREIGN_SYNTHETIC_RECORD_GRANT:-}"
unset SOVEREIGN_PROVIDER_TOKEN SOVEREIGN_NON_PROVIDER_TOKEN
unset SOVEREIGN_SYNTHETIC_RECORD_GRANT

DEPLOYMENT_ENVIRONMENT="${DEPLOYMENT_ENVIRONMENT:-staging}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
source "$HERE/deployment-context.sh"
load_deployment_context
if [[ "$DEPLOYMENT_ENVIRONMENT" != "staging" ]]; then
  echo "rollback rehearsal is allowed only in staging" >&2
  exit 2
fi

RESOURCE_GROUP="${RESOURCE_GROUP:-$EXPECTED_RESOURCE_GROUP}"
PROVIDER_APP="${PROVIDER_APP:-$EXPECTED_PROVIDER_APP}"
PUBLIC_APP="${PUBLIC_APP:-$EXPECTED_PUBLIC_APP}"
REGISTRY_SERVER="${REGISTRY_SERVER:-$EXPECTED_REGISTRY_SERVER}"
require_deployment_value RESOURCE_GROUP "$RESOURCE_GROUP" "$EXPECTED_RESOURCE_GROUP"
require_deployment_value PROVIDER_APP "$PROVIDER_APP" "$EXPECTED_PROVIDER_APP"
require_deployment_value PUBLIC_APP "$PUBLIC_APP" "$EXPECTED_PUBLIC_APP"
require_deployment_value REGISTRY_SERVER "$REGISTRY_SERVER" "$EXPECTED_REGISTRY_SERVER"

case "$REHEARSAL_EVIDENCE_PATH" in
  /*.json) ;;
  *) echo "REHEARSAL_EVIDENCE_PATH must be an absolute .json path" >&2; exit 2 ;;
esac
if [[ -e "$REHEARSAL_EVIDENCE_PATH" ]]; then
  echo "refusing to overwrite existing rehearsal evidence" >&2
  exit 2
fi

# Bind the rehearsal to one clean source tree and to an image whose immutable
# config proves that exact commit. These checks must complete before the first
# Container Apps inventory read, deployment mutation, or evidence write.
if [[ -n "$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all --ignore-submodules=none)" ]]; then
  echo "rollback rehearsal requires a clean worktree with no untracked files" >&2
  exit 2
fi
source_commit="$(git -C "$ROOT" rev-parse --verify 'HEAD^{commit}')"
if [[ ! "$source_commit" =~ ^[0-9a-f]{40,64}$ ]]; then
  echo "could not resolve a full lowercase source commit" >&2
  exit 2
fi

candidate_prefix="$REGISTRY_SERVER/cosmaneura/sovereign@"
candidate_digest="${IMAGE_REF#"$candidate_prefix"}"
if [[ "$IMAGE_REF" == "$candidate_digest" ||
      ! "$candidate_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "candidate must be an immutable digest in the exact staging repository" >&2
  exit 2
fi
python3 "$HERE/verify-acr-image-revision.py" \
  "$REGISTRY_SERVER" "cosmaneura/sovereign" "$candidate_digest" "$source_commit"

active_snapshot() {
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
    provisioning = item.get("provisioningState", properties.get("provisioningState"))
    traffic = item.get("trafficWeight", properties.get("trafficWeight"))
    template = item.get("template", properties.get("template")) or {}
    containers = template.get("containers") or []
    if (
        active is True
        and health == "Healthy"
        and provisioning in {"Provisioned", "Succeeded"}
        and traffic == 100
        and len(containers) == 1
    ):
        matches.append((item.get("name") or properties.get("name"), containers[0].get("image")))
if len(matches) != 1 or not all(matches[0]):
    raise SystemExit("expected exactly one active healthy revision with one image")
print("\t".join(matches[0]))
PY
}

IFS=$'\t' read -r before_provider_revision before_provider_image \
  < <(active_snapshot "$PROVIDER_APP")
IFS=$'\t' read -r before_public_revision before_public_image \
  < <(active_snapshot "$PUBLIC_APP")

expected_prefix="$REGISTRY_SERVER/cosmaneura/sovereign@sha256:"
for image in "$before_provider_image" "$before_public_image"; do
  digest="${image#"$expected_prefix"}"
  if [[ "$image" == "$digest" || ! "$digest" =~ ^[0-9a-f]{64}$ ]]; then
    echo "baseline and candidate must be immutable staging ACR digests" >&2
    exit 2
  fi
done
if [[ "$before_provider_image" != "$before_public_image" ]]; then
  echo "staging baseline must already be paired on one digest" >&2
  exit 2
fi
if [[ "$IMAGE_REF" == "$before_provider_image" ]]; then
  echo "rollback rehearsal requires a second digest" >&2
  exit 2
fi

set +e
SOVEREIGN_PROVIDER_TOKEN="$provider_smoke_token" \
SOVEREIGN_NON_PROVIDER_TOKEN="$non_provider_smoke_token" \
SOVEREIGN_SYNTHETIC_RECORD_GRANT="$record_smoke_grant" \
SOVEREIGN_PLATFORM_URL="$SOVEREIGN_PLATFORM_URL" \
SOVEREIGN_RUN_LABS_SMOKE="$SOVEREIGN_RUN_LABS_SMOKE" \
SOVEREIGN_SYNTHETIC_PLATFORM_RECORD_REF="${SOVEREIGN_SYNTHETIC_PLATFORM_RECORD_REF:-}" \
SOVEREIGN_REHEARSAL_FORCE_ROLLBACK=true \
DEPLOYMENT_ENVIRONMENT="$DEPLOYMENT_ENVIRONMENT" \
RESOURCE_GROUP="$RESOURCE_GROUP" \
PROVIDER_APP="$PROVIDER_APP" \
PUBLIC_APP="$PUBLIC_APP" \
REGISTRY_SERVER="$REGISTRY_SERVER" \
"$HERE/deploy-dual.sh"
deploy_status=$?
set -e
if [[ "$deploy_status" -ne 75 ]]; then
  echo "rehearsal did not reach the post-smoke intentional rollback gate" >&2
  exit 1
fi

IFS=$'\t' read -r after_provider_revision after_provider_image \
  < <(active_snapshot "$PROVIDER_APP")
IFS=$'\t' read -r after_public_revision after_public_image \
  < <(active_snapshot "$PUBLIC_APP")
if [[ "$after_provider_image" != "$before_provider_image" ||
      "$after_public_image" != "$before_public_image" ]]; then
  echo "paired rollback did not restore the exact staging baseline" >&2
  exit 1
fi

DEPLOYMENT_ENVIRONMENT=staging \
RESOURCE_GROUP="$RESOURCE_GROUP" \
PROVIDER_APP="$PROVIDER_APP" \
PUBLIC_APP="$PUBLIC_APP" \
REGISTRY_SERVER="$REGISTRY_SERVER" \
"$HERE/verify-live-app-boundaries.sh"

python3 - \
  "$REHEARSAL_EVIDENCE_PATH" "$source_commit" "$IMAGE_REF" \
  "$before_provider_revision" "$before_public_revision" \
  "$after_provider_revision" "$after_public_revision" \
  "$before_provider_image" <<'PY'
import datetime as dt
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
payload = {
    "schema": "cosmaneura.sovereign.paired-rollback-rehearsal.v1",
    "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    "environment": "staging",
    "source_commit": sys.argv[2],
    "candidate_image": sys.argv[3],
    "intentional_failure_gate": "after_live_and_synthetic_preview_smokes",
    "before": {
        "provider_revision": sys.argv[4],
        "public_revision": sys.argv[5],
        "paired_image": sys.argv[8],
    },
    "after": {
        "provider_revision": sys.argv[6],
        "public_revision": sys.argv[7],
        "paired_image": sys.argv[8],
    },
    "result": "passed",
}
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("x", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY

echo "staging live smokes passed and both mounts rolled back to the paired baseline" >&2
echo "$REHEARSAL_EVIDENCE_PATH"
