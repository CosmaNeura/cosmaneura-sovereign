#!/usr/bin/env python3
"""Emit a Procfile as the string map required by OpenTofu external data."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def parse(path: Path) -> dict[str, str]:
    processes: dict[str, str] = {}
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" not in raw:
            raise ValueError(f"Procfile line {number} has no process separator")
        name, command = raw.split(":", 1)
        name = name.strip()
        command = command.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", name) or not command:
            raise ValueError(f"Procfile line {number} is invalid")
        if name in processes:
            raise ValueError(f"Procfile process is duplicated: {name}")
        processes[name] = command
    if "web" not in processes:
        raise ValueError("Procfile must define a web process")
    return processes


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: read-procfile.py <Procfile>", file=sys.stderr)
        return 2
    try:
        payload = parse(Path(argv[1]))
    except (OSError, ValueError) as error:
        print(f"invalid Procfile: {error}", file=sys.stderr)
        return 1
    json.dump(payload, sys.stdout, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
