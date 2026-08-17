#!/usr/bin/env bash
# Build and push the first immutable image before Terraform creates either app.
set -euo pipefail

: "${DEPLOYMENT_ENVIRONMENT:?DEPLOYMENT_ENVIRONMENT is required}"
: "${REGISTRY:?REGISTRY is required (for example, name.azurecr.io)}"
IMAGE_REPO="${IMAGE_REPO:-cosmaneura/sovereign}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$ROOT/infra/scripts"
source "$HERE/deployment-context.sh"
load_deployment_context
require_deployment_value REGISTRY "$REGISTRY" "$EXPECTED_REGISTRY_SERVER"

for command in az curl git python3 tar; do
  command -v "$command" >/dev/null || {
    echo "$command is required" >&2
    exit 1
  }
done
if [[ -n "$(git -C "$ROOT" status --porcelain --untracked-files=all)" ]]; then
  echo "refusing to build a bootstrap image from a dirty worktree" >&2
  exit 2
fi

if [[ ! "$REGISTRY" =~ ^[a-z0-9]{5,50}\.azurecr\.io$ ]]; then
  echo "REGISTRY must be a bare Azure Container Registry hostname" >&2
  exit 2
fi
if [[ ! "$IMAGE_REPO" =~ ^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*$ ]]; then
  echo "IMAGE_REPO is invalid" >&2
  exit 2
fi

sha="$(git -C "$ROOT" rev-parse HEAD)"
commit_epoch="$(git -C "$ROOT" show -s --format=%ct HEAD)"
image="$REGISTRY/$IMAGE_REPO"
tmp_dir="$(mktemp -d)"
source_dir="$tmp_dir/source"
mkdir -p "$source_dir"
trap 'rm -rf "$tmp_dir"' EXIT

# The first digest is both mounts' rollback root. Do not trust whichever
# executable happens to be on the operator's PATH: download one exact upstream
# release asset and verify its published SHA-256 before it can build anything.
case "$(uname -s):$(uname -m)" in
  Darwin:arm64)
    nixpacks_asset="nixpacks-v1.41.0-aarch64-apple-darwin.tar.gz"
    nixpacks_sha256="0c6799690ad0b7328691fe3e457ec93ddc250ab229b7422fe8d8cb6ecc72a70c"
    ;;
  Darwin:x86_64)
    nixpacks_asset="nixpacks-v1.41.0-x86_64-apple-darwin.tar.gz"
    nixpacks_sha256="56bf48d4defe652368eaa39573e8d09fc1dd518ad414d63f8d30d3df05c6e421"
    ;;
  Linux:x86_64)
    nixpacks_asset="nixpacks-v1.41.0-x86_64-unknown-linux-gnu.tar.gz"
    nixpacks_sha256="194bcad8c379f78a309eee1a88b2e6b2abc59f354efe7ecd7b4bbaf21de99a06"
    ;;
  Linux:aarch64|Linux:arm64)
    nixpacks_asset="nixpacks-v1.41.0-aarch64-unknown-linux-musl.tar.gz"
    nixpacks_sha256="912bd02dd2bb6f9c3a9ed965fe8a68b4aa318dc7a2546e2eca6f2806a894ba39"
    ;;
  *)
    echo "unsupported bootstrap platform; use a reviewed supported machine" >&2
    exit 2
    ;;
esac
nixpacks_archive="$tmp_dir/$nixpacks_asset"
curl --fail --location --silent --show-error \
  --output "$nixpacks_archive" \
  "https://github.com/railwayapp/nixpacks/releases/download/v1.41.0/$nixpacks_asset"
python3 - "$nixpacks_archive" "$nixpacks_sha256" <<'PY'
import hashlib
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
expected = sys.argv[2]
actual = hashlib.sha256(path.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit("Nixpacks release checksum mismatch")
PY
nixpacks_dir="$tmp_dir/nixpacks"
mkdir -p "$nixpacks_dir"
tar -xzf "$nixpacks_archive" -C "$nixpacks_dir"
nixpacks_bin="$nixpacks_dir/nixpacks"
[[ -x "$nixpacks_bin" ]] || {
  echo "verified Nixpacks archive did not contain an executable" >&2
  exit 2
}
nixpacks_version="$($nixpacks_bin --version)"
if [[ ! "$nixpacks_version" =~ (^|[[:space:]])1\.41\.0($|[[:space:]]) ]]; then
  echo "verified artifact did not report Nixpacks 1.41.0" >&2
  exit 2
fi

git -C "$ROOT" archive --format=tar HEAD | tar -xf - -C "$source_dir"
"$nixpacks_bin" build "$source_dir" \
  --name "$image:$sha" \
  --out "$source_dir" \
  --cache-key "$sha" \
  --platform linux/amd64 \
  --label "org.opencontainers.image.revision=$sha" >&2
python3 "$ROOT/infra/scripts/finalize-nixpacks-context.py" \
  "$source_dir" "$image:$sha" "$sha" "$commit_epoch"

# Build on Azure rather than requiring a privileged local Docker daemon. The
# source is a clean Git archive plus the checksum-pinned, normalized Nixpacks
# output; no untracked env file or operator secret can enter the context.
az acr build \
  --registry "${REGISTRY%%.*}" \
  --image "$IMAGE_REPO:$sha" \
  --file .nixpacks/Dockerfile \
  --platform linux/amd64 \
  --build-arg NIXPACKS_METADATA=python \
  --build-arg NIXPACKS_PYTHON_VERSION=3.13 \
  --build-arg PIP_DEFAULT_TIMEOUT=100 \
  --build-arg PIP_DISABLE_PIP_VERSION_CHECK=1 \
  --build-arg PIP_NO_CACHE_DIR=1 \
  --build-arg PYTHONDONTWRITEBYTECODE=1 \
  --build-arg PYTHONFAULTHANDLER=1 \
  --build-arg PYTHONHASHSEED=random \
  --build-arg PYTHONUNBUFFERED=1 \
  --timeout 3600 \
  "$source_dir" >&2
digest="$(az acr manifest show-metadata \
  --registry "${REGISTRY%%.*}" \
  --name "$IMAGE_REPO:$sha" \
  --query digest --output tsv)"
if [[ ! "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "ACR did not return an immutable image digest" >&2
  exit 1
fi

printf '%s\n' "$image@$digest"
