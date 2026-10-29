"""Secretless production profile acceptance from env export to runtime gates."""
from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from app.config import Settings, validate_deployment_configuration
from app.feature_catalog import visible_paths
from app.security.mount import MountContext
from app.sovereign.orchestrator import INTENT_CAPABILITIES


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "infra" / "scripts"
SPEC = importlib.util.spec_from_file_location(
    "production_profile_validator", SCRIPTS / "validate-deployment-env.py",
)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def production_source(*, enabled: bool = False) -> str:
    source = (ROOT / ".env.production.backend.example").read_text()
    source = source.replace("tenant_replace_me", "tenant_synthetic_production")
    source = source.replace("your-instance.clerk.accounts.dev", "synthetic.clerk.accounts.dev")
    if enabled:
        source = source.replace("FEATURE_SESSION_PREP=false", "FEATURE_SESSION_PREP=true")
        source = source.replace("SESSION_PREP_CONTEXT_SOURCE=synthetic", "SESSION_PREP_CONTEXT_SOURCE=platform")
    return source


def export(path: Path, environment: str, mount: str, keys: set[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", SCRIPTS / "export-public-env.sh", environment, path,
         ",".join(sorted(keys)), mount],
        text=True, capture_output=True,
    )


def runtime_settings(values: dict[str, str]) -> Settings:
    runtime = {name.lower(): value for name, value in values.items()}
    for name in ("oidc_algorithms", "cors_allow_origins"):
        runtime[name] = json.loads(runtime[name])
    return Settings(
        _env_file=None,
        **runtime,
        azure_deid_endpoint="https://synthetic-deid.example.test",
        azure_client_id="11111111-1111-4111-8111-111111111111",
        platform_workload_client_id="11111111-1111-4111-8111-111111111111",
        sovereign_record_grant_secret=base64.urlsafe_b64encode(bytes(32)).decode(),
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_production_profile_exports_valid_platform_runtime_and_narrows_broad_grants(
    tmp_path: Path, enabled: bool,
) -> None:
    path = tmp_path / "production.env"
    path.write_text(production_source(enabled=enabled))
    keys = VALIDATOR.PROVIDER_PUBLIC | VALIDATOR.PRODUCTION_SESSION_PREP_PUBLIC
    result = export(path, "prod", "provider", keys)
    assert result.returncode == 0, result.stderr
    values = json.loads(result.stdout)
    assert set(values) == keys
    assert "op://" not in result.stdout
    assert values["FEATURE_SESSION_PREP"] == str(enabled).lower()
    settings = runtime_settings(values)
    validate_deployment_configuration(settings)
    assert settings.platform_agent_api_enabled and not settings.feature_athena_labs
    mount = MountContext(
        surface="provider_assistant", auth_tier="provider",
        tenant_id="tenant_synthetic_production", actor_id="actor_synthetic",
        capabilities=(
            "education:answer", "ethics:consult", "visit-prep:run", "session-prep:read",
            "referrals:search", "ehr:labs:read", "plan-workbench:run", "journey-digest:write",
        ),
    )
    intents = {path["intent"] for path in visible_paths(settings, mount, INTENT_CAPABILITIES)}
    assert intents == {"session_prep" if enabled else "visit_prep", "ethics_consult", "engagement"}


@pytest.mark.parametrize("old,new", [
    ("AUTH_MODE=disabled", "AUTH_MODE=oidc"),
    ("AUTH_MODE=disabled", "AUTH_MODE=dev_hs256"),
    ("PLATFORM_AGENT_API_ENABLED=true", "PLATFORM_AGENT_API_ENABLED=false"),
    ("FEATURE_ATHENA_LABS=false", "FEATURE_ATHENA_LABS=true"),
    ("AUDIT_SINK=stdout", "AUDIT_SINK=file"),
    ("PLATFORM_AGENT_API_BASE_URL=https://app.cosmaneura.com",
     "PLATFORM_AGENT_API_BASE_URL=https://staging-app.cosmaneura.com"),
    ("PLATFORM_AGENT_API_BASE_URL=https://app.cosmaneura.com",
     "PLATFORM_AGENT_API_BASE_URL=https://app.cosmaneura.com/agent-api"),
    ("PLATFORM_AGENT_API_SCOPE=api://af957741-0342-498b-8496-e93706af9e99/.default",
     "PLATFORM_AGENT_API_SCOPE=api://22222222-2222-4222-8222-222222222222/.default"),
    ('CORS_ALLOW_ORIGINS=["https://app.cosmaneura.com"]',
     'CORS_ALLOW_ORIGINS=["https://staging-app.cosmaneura.com"]'),
    ("FEATURE_SESSION_PREP=false", "FEATURE_SESSION_PREP=TRUE"),
    ("FEATURE_SESSION_PREP=false", "FEATURE_SESSION_PREP=true"),
    ("SESSION_PREP_CONTEXT_SOURCE=synthetic", "SESSION_PREP_CONTEXT_SOURCE=platform"),
])
def test_production_export_refuses_mixed_authority_or_activation_configuration(
    tmp_path: Path, old: str, new: str,
) -> None:
    source = production_source()
    assert old in source
    path = tmp_path / "production.env"
    path.write_text(source.replace(old, new))
    result = export(path, "prod", "provider", {"FEATURE_SESSION_PREP"})
    assert result.returncode != 0
    assert result.stdout == ""


@pytest.mark.parametrize("name", ["FEATURE_SESSION_PREP", "SESSION_PREP_CONTEXT_SOURCE"])
def test_production_export_requires_explicit_dark_or_active_pair(tmp_path: Path, name: str) -> None:
    path = tmp_path / "production.env"
    path.write_text("\n".join(
        line for line in production_source().splitlines() if not line.startswith(name + "=")
    ))
    result = export(path, "prod", "provider", {"MOCK_MODE"})
    assert result.returncode != 0
    assert "missing deployment keys" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("name", [
    "ATHENA_CLIENT_ID", "ATHENA_CLIENT_SECRET", "SESSION_PREP_SYNTHETIC_CONTEXT_REF",
    "MOUNT_CAPABILITIES", "PLATFORM_WORKLOAD_CLIENT_ID",
])
def test_production_profile_refuses_unused_credentials_and_operator_authority_overrides(
    tmp_path: Path, name: str,
) -> None:
    sentinel = "never-print-this-synthetic-sentinel"
    path = tmp_path / "production.env"
    path.write_text(production_source() + f"\n{name}=op://Synthetic/{sentinel}\n")
    result = export(path, "prod", "provider", {"MOCK_MODE"})
    assert result.returncode != 0
    assert f"unexpected deployment keys: {name}" in result.stderr
    assert sentinel not in result.stdout + result.stderr


@pytest.mark.parametrize("name", [
    "FEATURE_SESSION_PREP", "SESSION_PREP_CONTEXT_SOURCE", "PLATFORM_AGENT_API_ENABLED",
    "PLATFORM_WORKLOAD_CLIENT_ID", "SOVEREIGN_RECORD_GRANT_SECRET", "ATHENA_CLIENT_SECRET",
])
def test_public_export_refuses_every_provider_control_and_secret_without_value_leak(
    tmp_path: Path, name: str,
) -> None:
    sentinel = "never-print-this-synthetic-sentinel"
    path = tmp_path / "public.env"
    path.write_text((ROOT / ".env.production.public.example").read_text()
                    + f"\n{name}=op://Synthetic/{sentinel}\n")
    result = export(path, "prod", "public", VALIDATOR.PUBLIC_PUBLIC)
    assert result.returncode != 0
    assert f"unexpected deployment keys: {name}" in result.stderr
    assert sentinel not in result.stdout + result.stderr


def test_staging_contract_retains_its_existing_keys_and_athena_secret_references(tmp_path: Path) -> None:
    path = tmp_path / "staging.env"
    path.write_text((ROOT / ".env.staging.backend.example").read_text())
    result = export(path, "staging", "provider", VALIDATOR.PROVIDER_PUBLIC)
    assert result.returncode == 0, result.stderr
    assert set(json.loads(result.stdout)) == VALIDATOR.PROVIDER_PUBLIC
    values = VALIDATOR.parse_env(path)
    assert {name for name in values if name.startswith("ATHENA_CLIENT_")} == VALIDATOR.ATHENA_SECRETS
    assert "FEATURE_SESSION_PREP" not in values and "SESSION_PREP_CONTEXT_SOURCE" not in values
