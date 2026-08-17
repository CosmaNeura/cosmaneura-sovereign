#!/usr/bin/env python3
"""Fail closed unless OpenTofu's initialized backend uses the selected state key."""
from __future__ import annotations

import json
import sys
from pathlib import Path


EXPECTED = {
    "prod": "sovereign-prod.tfstate",
    "staging": "sovereign-staging.tfstate",
}


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in EXPECTED:
        print(
            "usage: verify-backend-context.py <prod|staging> <infra-directory>",
            file=sys.stderr,
        )
        return 2
    environment, infra = argv[1], Path(argv[2])
    metadata_path = infra / ".terraform" / "terraform.tfstate"
    try:
        payload = json.loads(metadata_path.read_text())
        backend = payload["backend"]
        config = backend["config"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        print("OpenTofu backend metadata is absent or unreadable", file=sys.stderr)
        return 1
    expected = EXPECTED[environment]
    if backend.get("type") != "azurerm" or config.get("key") != expected:
        print(
            f"initialized backend does not match the {environment} state boundary",
            file=sys.stderr,
        )
        return 1
    print(f"verified isolated backend key: {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
