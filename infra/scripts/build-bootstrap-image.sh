#!/usr/bin/env bash
# Build and push the first immutable image before Terraform creates either app.
set -euo pipefail
umask 077

: "${DEPLOYMENT_ENVIRONMENT:?DEPLOYMENT_ENVIRONMENT is required}"
: "${REGISTRY:?REGISTRY is required (for example, name.azurecr.io)}"
IMAGE_REPO="${IMAGE_REPO:-cosmaneura/sovereign}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$ROOT/infra/scripts"

run_closed_git() {
  env -i \
    GIT_ATTR_NOSYSTEM=1 \
    GIT_CONFIG_GLOBAL=/dev/null \
    GIT_CONFIG_NOSYSTEM=1 \
    GIT_NO_LAZY_FETCH=1 \
    GIT_OPTIONAL_LOCKS=0 \
    GIT_TERMINAL_PROMPT=0 \
    LC_ALL=C \
    PATH="$PATH" \
    git --no-replace-objects "$@"
}

run_isolated_python() {
  python3 -I -B -S "$@"
}

run_closed_tar() {
  env -i LC_ALL=C PATH="$PATH" tar "$@"
}

for command in az curl git python3 tar wc; do
  command -v "$command" >/dev/null || {
    echo "$command is required" >&2
    exit 1
  }
done
if run_closed_git -c core.fsmonitor=false -C "$ROOT" \
  config --includes --get-regexp '^filter\.' >/dev/null 2>&1; then
  echo "refusing to build with repository-local Git content filters" >&2
  exit 2
else
  filter_status="$?"
  if [[ "$filter_status" -ne 1 ]]; then
    echo "unable to verify repository-local Git filter configuration" >&2
    exit 1
  fi
fi
if ! worktree_status="$(
  run_closed_git -c core.fsmonitor=false -C "$ROOT" \
    status --porcelain --untracked-files=all
)"; then
  echo "unable to verify that the bootstrap source worktree is clean" >&2
  exit 1
fi
if [[ -n "$worktree_status" ]]; then
  echo "refusing to build a bootstrap image from a dirty worktree" >&2
  exit 2
fi

source "$HERE/deployment-context.sh"
load_deployment_context
load_release_scope
if [[ "$SOVEREIGN_RELEASE_SCOPE" != "dual" ]]; then
  echo "provider-only release uses a separately verified local immutable image; remote bootstrap is not permitted" >&2
  exit 2
fi
require_deployment_value REGISTRY "$REGISTRY" "$EXPECTED_REGISTRY_SERVER"

if [[ ! "$REGISTRY" =~ ^[a-z0-9]{5,50}\.azurecr\.io$ ]]; then
  echo "REGISTRY must be a bare Azure Container Registry hostname" >&2
  exit 2
fi
if [[ ! "$IMAGE_REPO" =~ ^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*$ ]]; then
  echo "IMAGE_REPO is invalid" >&2
  exit 2
fi
if [[ "$DEPLOYMENT_ENVIRONMENT" == "staging" ]]; then
  if [[ -z "${SESSION_PREP_CORE_REPO:-}" ]]; then
    echo "SESSION_PREP_CORE_REPO is required for staging build admission" >&2
    exit 2
  fi
  if [[ -z "${SESSION_PREP_UI_RECEIPT:-}" ]]; then
    echo "SESSION_PREP_UI_RECEIPT is required for staging build admission" >&2
    exit 2
  fi
  if [[ "$IMAGE_REPO" != "cosmaneura/sovereign" ]]; then
    echo "staging build admission requires the exact Sovereign image repository" >&2
    exit 2
  fi
fi
tmp_dir="$(mktemp -d)"
cleanup() {
  local status="$?"
  trap - EXIT
  rm -rf -- "$tmp_dir"
  exit "$status"
}
trap cleanup EXIT

# A staging image is allowed to use only the exact Session Prep source object
# admitted by the reviewed cross-repository reconciler and a fresh UI receipt.
# This completes before the first download or Azure command. Production keeps
# its existing clean-HEAD behavior and cannot consume the staging admission.
if [[ "$DEPLOYMENT_ENVIRONMENT" == "staging" ]]; then
  admission_output="$tmp_dir/staging-build-admission.fields"
  if ! run_isolated_python \
    "$ROOT/tools/admit_session_prep_staging_build.py" \
      --core-repo "$SESSION_PREP_CORE_REPO" \
      --sovereign-repo "$ROOT" \
      --ui-receipt "$SESSION_PREP_UI_RECEIPT" \
      --output build-fields >"$admission_output"; then
    echo "staging build admission rejected" >&2
    exit 1
  fi
  read -r admission_bytes < <(wc -c <"$admission_output")
  read -r admission_lines < <(wc -l <"$admission_output")
  admission_fields="$(<"$admission_output")"
  if [[ "$admission_bytes" != "154" ||
        "$admission_lines" != "1" ||
        "${#admission_fields}" -ne 153 ]]; then
    echo "staging build admission returned an invalid result" >&2
    exit 1
  fi
  IFS=$'\t' read -r sha source_tree admission_digest extra \
    <<<"$admission_fields"
  if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ||
        ! "$source_tree" =~ ^[0-9a-f]{40}$ ||
        ! "$admission_digest" =~ ^sha256:[0-9a-f]{64}$ ||
        -n "$extra" ||
        "$admission_fields" != "$sha"$'\t'"$source_tree"$'\t'"$admission_digest" ]]; then
    echo "staging build admission returned an invalid result" >&2
    exit 1
  fi
  resolved_sha="$(
    run_closed_git -C "$ROOT" rev-parse --verify "${sha}^{commit}"
  )"
  resolved_tree="$(
    run_closed_git -C "$ROOT" rev-parse --verify "${sha}^{tree}"
  )"
  if [[ "$resolved_sha" != "$sha" || "$resolved_tree" != "$source_tree" ]]; then
    echo "staging build source does not match the admitted Git object" >&2
    exit 1
  fi
else
  sha="$(run_closed_git -C "$ROOT" rev-parse --verify 'HEAD^{commit}')"
fi

commit_epoch="$(
  run_closed_git -C "$ROOT" show -s --format=%ct "$sha"
)"
if [[ ! "$commit_epoch" =~ ^[0-9]{1,12}$ ]]; then
  echo "build source has an invalid commit timestamp" >&2
  exit 1
fi
image="$REGISTRY/$IMAGE_REPO"
source_dir="$tmp_dir/source"
archive_git_dir="$tmp_dir/archive.git"
archive_template_dir="$tmp_dir/empty-git-template"
mkdir -p "$source_dir" "$archive_template_dir"

# Archive through isolated Git metadata. The admitted commit's own
# .gitattributes remain authoritative, while operator-controlled repository,
# global, and system attribute files cannot add export-ignore/export-subst.
object_directory="$(
  run_closed_git -C "$ROOT" \
    rev-parse --path-format=absolute --git-path objects
)"
if [[ ! -d "$object_directory" ]]; then
  echo "build source Git object storage is unavailable" >&2
  exit 1
fi
env -i \
  GIT_CONFIG_GLOBAL=/dev/null \
  GIT_CONFIG_NOSYSTEM=1 \
  LC_ALL=C \
  PATH="$PATH" \
  git init --bare --quiet --template="$archive_template_dir" "$archive_git_dir"

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
run_isolated_python - "$nixpacks_archive" "$nixpacks_sha256" <<'PY'
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
run_closed_tar -xzf "$nixpacks_archive" -C "$nixpacks_dir"
nixpacks_bin="$nixpacks_dir/nixpacks"
[[ -x "$nixpacks_bin" ]] || {
  echo "verified Nixpacks archive did not contain an executable" >&2
  exit 2
}
run_closed_nixpacks() (
  local variable
  while IFS= read -r variable; do
    unset "$variable"
  done < <(compgen -A variable NIXPACKS_)
  "$nixpacks_bin" "$@"
)
nixpacks_version="$(run_closed_nixpacks --version)"
if [[ ! "$nixpacks_version" =~ (^|[[:space:]])1\.41\.0($|[[:space:]]) ]]; then
  echo "verified artifact did not report Nixpacks 1.41.0" >&2
  exit 2
fi

env -i \
  GIT_ALTERNATE_OBJECT_DIRECTORIES="$object_directory" \
  GIT_ATTR_NOSYSTEM=1 \
  GIT_CONFIG_GLOBAL=/dev/null \
  GIT_CONFIG_NOSYSTEM=1 \
  GIT_NO_LAZY_FETCH=1 \
  LC_ALL=C \
  PATH="$PATH" \
  git --no-replace-objects --git-dir="$archive_git_dir" \
    -c core.attributesFile=/dev/null \
    archive --format=tar "$sha" | run_closed_tar -xf - -C "$source_dir"
if [[ "$DEPLOYMENT_ENVIRONMENT" == "staging" ]]; then
  run_closed_nixpacks build "$source_dir" \
    --name "$image:$sha" \
    --out "$source_dir" \
    --cache-key "$sha" \
    --platform linux/amd64 \
    --label "org.opencontainers.image.revision=$sha" \
    --label "org.cosmaneura.session-prep.build-admission=$admission_digest" >&2
  run_isolated_python "$ROOT/infra/scripts/finalize-nixpacks-context.py" \
    "$source_dir" "$image:$sha" "$sha" "$commit_epoch" "$admission_digest"
else
  run_closed_nixpacks build "$source_dir" \
    --name "$image:$sha" \
    --out "$source_dir" \
    --cache-key "$sha" \
    --platform linux/amd64 \
    --label "org.opencontainers.image.revision=$sha" >&2
  run_isolated_python "$ROOT/infra/scripts/finalize-nixpacks-context.py" \
    "$source_dir" "$image:$sha" "$sha" "$commit_epoch"
fi

# Build on Azure rather than requiring a privileged local Docker daemon. The
# source is a clean Git archive plus the checksum-pinned, normalized Nixpacks
# output; no untracked env file or operator secret can enter the context.
# For a local context, Azure CLI validates an explicit --file against its
# process working directory before it archives the positional source. Run from
# the generated context so the same relative path is valid for both operations.
(
  cd "$source_dir"
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
    .
) >&2
digest="$(az acr manifest show-metadata \
  --registry "${REGISTRY%%.*}" \
  --name "$IMAGE_REPO:$sha" \
  --query digest --output tsv)"
if [[ ! "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "ACR did not return an immutable image digest" >&2
  exit 1
fi

printf '%s\n' "$image@$digest"
