"""Reproducible-build configuration regressions."""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _assignment(path: Path, name: str) -> str:
    prefix = f"{name}="
    matches = [
        line[len(prefix):]
        for line in path.read_text().splitlines()
        if line.startswith(prefix)
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize(
    "path",
    [
        ROOT / ".env.example",
        ROOT / ".env.production.backend.example",
        ROOT / ".env.production.public.example",
    ],
)
def test_example_fast_model_is_the_live_deployment(path: Path):
    assert _assignment(path, "FAST_MODEL") == "gpt-5.4-nano"


def test_handoff_names_the_live_fast_model():
    handoff = (ROOT / "SETUP_AND_HANDOFF.md").read_text()
    assert "gpt-5.4-nano" in handoff
    assert "gpt-5.4-mini" not in handoff


def test_nixpacks_installs_only_the_hash_locked_runtime():
    config = tomllib.loads((ROOT / "nixpacks.toml").read_text())
    install = "\n".join(config["phases"]["install"]["cmds"])
    assert "--require-hashes" in install
    assert "--only-binary=:all:" in install
    assert "-r requirements.lock" in install
    assert "-r requirements.txt" not in install
