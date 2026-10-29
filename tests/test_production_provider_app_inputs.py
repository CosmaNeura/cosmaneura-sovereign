"""Persistent creation inputs distinguish production provider-only and dual apps."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "infra" / "scripts" / "validate-deployment-inputs.py"


def validate(tmp_path: Path, source: str, environment: str = "prod") -> subprocess.CompletedProcess[str]:
    path = tmp_path / "deployment.auto.tfvars"
    path.write_text(source)
    return subprocess.run(
        [sys.executable, VALIDATOR, path, environment], text=True, capture_output=True,
    )


def production_inputs(*, provider: bool = False, dual: bool = False) -> str:
    source = (ROOT / "infra" / "deployment.auto.tfvars.example").read_text()
    if provider:
        source = source.replace("production_provider_app_enabled = false", "production_provider_app_enabled = true")
    if dual:
        source = source.replace("live_apps_enabled       = false", "live_apps_enabled       = true")
    if provider or dual:
        source = source.replace("key_vault_secrets_ready = false", "key_vault_secrets_ready = true")
        source = source.replace('initial_image_ref        = ""',
                                'initial_image_ref        = "cosmaneurasovereignprodregistry.azurecr.io/'
                                'cosmaneura/sovereign@sha256:' + "a" * 64 + '"')
    return source


@pytest.mark.parametrize("provider,dual,status", [
    (False, False, "prod disabled"),
    (True, False, "prod provider-only enabled"),
    (False, True, "prod enabled"),
])
def test_creation_inputs_accept_only_explicit_dark_provider_only_or_legacy_dual_states(
    tmp_path: Path, provider: bool, dual: bool, status: str,
) -> None:
    result = validate(tmp_path, production_inputs(provider=provider, dual=dual))
    assert result.returncode == 0, result.stderr
    assert status in result.stdout


def test_legacy_production_inputs_without_the_new_control_remain_accepted(tmp_path: Path) -> None:
    source = "\n".join(line for line in production_inputs(dual=True).splitlines()
                       if not line.startswith("production_provider_app_enabled ="))
    result = validate(tmp_path, source)
    assert result.returncode == 0, result.stderr
    assert "prod enabled" in result.stdout


def test_creation_inputs_refuse_combining_provider_only_and_dual_gates(tmp_path: Path) -> None:
    result = validate(tmp_path, production_inputs(provider=True, dual=True))
    assert result.returncode == 1
    assert "requires prod and live_apps_enabled=false" in result.stderr


@pytest.mark.parametrize("old,new,message", [
    ("production_provider_app_enabled = true", 'production_provider_app_enabled = "true"', "must be literal true or false"),
    ("key_vault_secrets_ready = true", "key_vault_secrets_ready = false", "live apps require key_vault_secrets_ready=true"),
    ("@sha256:" + "a" * 64, ":latest", "immutable digest"),
    ("cosmaneurasovereignprodregistry.azurecr.io", "cosmaneurasovereignstagingregistry.azurecr.io", "exact prod ACR"),
])
def test_provider_only_creation_requires_literal_controls_ready_secrets_and_immutable_production_image(
    tmp_path: Path, old: str, new: str, message: str,
) -> None:
    source = production_inputs(provider=True)
    assert old in source
    result = validate(tmp_path, source.replace(old, new))
    assert result.returncode == 1
    assert message in result.stderr


def test_staging_inputs_keep_existing_semantics_and_refuse_production_control(tmp_path: Path) -> None:
    source = (ROOT / "infra" / "deployment.staging.auto.tfvars.example").read_text()
    result = validate(tmp_path, source, "staging")
    assert result.returncode == 0, result.stderr
    assert "staging disabled" in result.stdout
    result = validate(tmp_path, source + "\nproduction_provider_app_enabled = true\n", "staging")
    assert result.returncode == 1
    assert "staging forbids deployment inputs: production_provider_app_enabled" in result.stderr
