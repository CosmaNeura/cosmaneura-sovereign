#!/usr/bin/env bash
# Regenerate or verify the reproducible Python 3.13/Linux dependency locks.
set -euo pipefail

readonly UV_VERSION="0.12.5"
readonly LOCK_CUTOFF="2026-08-16T00:00:00Z"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

actual_uv_version="$(uv --version | awk '{print $2}')"
if [[ "$actual_uv_version" != "$UV_VERSION" ]]; then
  echo "uv $UV_VERSION is required to generate dependency locks (found $actual_uv_version)" >&2
  exit 1
fi

compile_lock() {
  local input="$1"
  local output="$2"
  uv pip compile "$input" \
    --python-version 3.13 \
    --python-platform x86_64-manylinux_2_28 \
    --only-binary :all: \
    --generate-hashes \
    --exclude-newer "$LOCK_CUTOFF" \
    --custom-compile-command "tools/compile_requirements_locks.sh" \
    --quiet \
    --output-file "$output"
}

if [[ "${1:-}" == "--check" ]]; then
  runtime_lock="$(mktemp)"
  ci_lock="$(mktemp)"
  trap 'rm -f "$runtime_lock" "$ci_lock"' EXIT

  compile_lock requirements.txt "$runtime_lock"
  compile_lock requirements-ci.in "$ci_lock"

  diff -u requirements.lock "$runtime_lock"
  diff -u requirements-ci.lock "$ci_lock"
  exit 0
fi

if [[ $# -ne 0 ]]; then
  echo "usage: tools/compile_requirements_locks.sh [--check]" >&2
  exit 2
fi

compile_lock requirements.txt requirements.lock
compile_lock requirements-ci.in requirements-ci.lock
