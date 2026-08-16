#!/usr/bin/env python3
"""Validate the three persistent, non-secret live-deployment Terraform inputs."""
from __future__ import annotations

import re
import sys
from pathlib import Path


EXPECTED = {
    "live_apps_enabled",
    "key_vault_secrets_ready",
    "initial_image_ref",
}
IMAGE_RE = re.compile(
    r'^"cosmaneurasovereignprodregistry\.azurecr\.io/'
    r'cosmaneura/sovereign@sha256:[0-9a-f]{64}"$'
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


def validate(values: dict[str, str]) -> str:
    enabled = values["live_apps_enabled"]
    ready = values["key_vault_secrets_ready"]
    image = values["initial_image_ref"]
    if enabled not in {"true", "false"} or ready not in {"true", "false"}:
        raise InputError("deployment gate values must be literal true or false")
    if enabled == "false":
        if ready != "false" or image != '""':
            raise InputError(
                "disabled live apps must keep secrets-ready false and image empty"
            )
        return "disabled"
    if ready != "true":
        raise InputError("live apps require key_vault_secrets_ready=true")
    if not IMAGE_RE.fullmatch(image):
        raise InputError(
            "live apps require the exact production ACR repository at an immutable digest"
        )
    return "enabled"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validate-deployment-inputs.py <deployment.auto.tfvars>", file=sys.stderr)
        return 2
    try:
        status = validate(parse(Path(argv[1])))
    except (InputError, OSError) as error:
        print(f"deployment inputs invalid: {error}", file=sys.stderr)
        return 1
    print(f"validated persistent deployment inputs ({status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
