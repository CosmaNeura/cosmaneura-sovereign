#!/usr/bin/env python3
"""Validate the persistent, non-secret live-deployment Terraform inputs."""
from __future__ import annotations

import re
import sys
from pathlib import Path


EXPECTED = {
    "deployment_environment",
    "azure_deid_service_id",
    "github_deploy_enabled",
    "live_apps_enabled",
    "key_vault_secrets_ready",
    "initial_image_ref",
}
DEID_RE = re.compile(
    r'^"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+/'
    r'providers/Microsoft\.HealthDataAIServices/deidServices/[^/]+"$'
)


class InputError(ValueError):
    """Persistent deployment inputs are absent or unsafe."""


def parse(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise InputError(
            "copy infra/deployment.auto.tfvars.example to "
            "infra/deployment.auto.tfvars"
        )
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([a-z][a-z0-9_]*)\s*=\s*(.+)", line)
        if not match:
            raise InputError(f"line {number} is not a simple assignment")
        name, value = match.groups()
        if name not in EXPECTED:
            raise InputError(f"unexpected deployment input: {name}")
        if name in values:
            raise InputError(f"duplicate deployment input: {name}")
        values[name] = value.strip()
    missing = sorted(EXPECTED - values.keys())
    if missing:
        raise InputError("missing deployment inputs: " + ", ".join(missing))
    return values


def validate(values: dict[str, str], expected_environment: str | None = None) -> str:
    environment_literal = values["deployment_environment"]
    if environment_literal not in {'"prod"', '"staging"'}:
        raise InputError("deployment_environment must be exactly prod or staging")
    environment = environment_literal.strip('"')
    if expected_environment is not None and environment != expected_environment:
        raise InputError(
            f"deployment_environment does not match requested {expected_environment} boundary"
        )
    if not DEID_RE.fullmatch(values["azure_deid_service_id"]):
        raise InputError("azure_deid_service_id must be an explicit DeID resource ID")

    github_deploy_enabled = values["github_deploy_enabled"]
    enabled = values["live_apps_enabled"]
    ready = values["key_vault_secrets_ready"]
    image = values["initial_image_ref"]
    if github_deploy_enabled not in {"true", "false"}:
        raise InputError("github_deploy_enabled must be literal true or false")
    if enabled not in {"true", "false"} or ready not in {"true", "false"}:
        raise InputError("deployment gate values must be literal true or false")
    if enabled == "false":
        if ready != "false" or image != '""':
            raise InputError(
                "disabled live apps must keep secrets-ready false and image empty"
            )
        return f"{environment} disabled"
    if ready != "true":
        raise InputError("live apps require key_vault_secrets_ready=true")
    registry = f"cosmaneurasovereign{environment}registry.azurecr.io"
    image_re = re.compile(
        rf'^"{re.escape(registry)}/'
        r'cosmaneura/sovereign@sha256:[0-9a-f]{64}"$'
    )
    if not image_re.fullmatch(image):
        raise InputError(
            f"live apps require the exact {environment} ACR repository at an immutable digest"
        )
    return f"{environment} enabled"


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 3}:
        print(
            "usage: validate-deployment-inputs.py <deployment.auto.tfvars> [prod|staging]",
            file=sys.stderr,
        )
        return 2
    expected_environment = argv[2] if len(argv) == 3 else None
    if expected_environment not in {None, "prod", "staging"}:
        print("expected environment must be prod or staging", file=sys.stderr)
        return 2
    try:
        status = validate(parse(Path(argv[1])), expected_environment)
    except (InputError, OSError) as error:
        print(f"deployment inputs invalid: {error}", file=sys.stderr)
        return 1
    print(f"validated persistent deployment inputs ({status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
