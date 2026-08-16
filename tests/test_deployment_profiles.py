"""Deployment-profile, welcome, and anonymous rate-limit regressions."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.config import (AuthMode, DEPLOYMENT_MOUNTS,
                        DeploymentConfigurationError, MountProfile, Settings,
                        validate_deployment_configuration)
from app.security.mount import MountResolveRequest, PinnedResolver
from app.security.rate_limit import TokenBucketRateLimiter


def _run(coro):
    return asyncio.run(coro)


def test_mount_profile_is_the_only_pinned_authority_selector():
    public_settings = Settings()
    public = _run(PinnedResolver.from_settings(public_settings).resolve(
        MountResolveRequest(client_turn_id=uuid.uuid4(), trace_id=uuid.uuid4().hex),
    )).context
    assert public.surface == "public"
    assert public.auth_tier == "anonymous"
    assert public.capabilities == DEPLOYMENT_MOUNTS[MountProfile.PUBLIC].capabilities
    assert public.actor_id is None

    provider_settings = Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
    )
    provider = _run(PinnedResolver.from_settings(provider_settings).resolve(
        MountResolveRequest(
            client_turn_id=uuid.uuid4(),
            trace_id=uuid.uuid4().hex,
            verified_actor_id="provider-subject",
            verified_actor_is_provider=True,
        ),
    )).context
    assert provider.surface == "provider_assistant"
    assert provider.auth_tier == "provider"
    assert provider.capabilities == (
        DEPLOYMENT_MOUNTS[MountProfile.PROVIDER_ASSISTANT].capabilities
    )
    assert provider.tenant_id == "tenant_test"
    assert provider.actor_id is not None


@pytest.mark.parametrize(
    ("setting", "value", "env_name"),
    [
        ("auth_mode", AuthMode.DEV_HS256, "AUTH_MODE"),
        ("auth_dev_secret", "dev-secret-sentinel", "AUTH_DEV_SECRET"),
        ("oidc_issuer", "https://identity-sentinel.example", "OIDC_ISSUER"),
        ("oidc_audience", "audience-sentinel", "OIDC_AUDIENCE"),
        ("oidc_jwks_url", "https://jwks-sentinel.example", "OIDC_JWKS_URL"),
        ("mount_tenant_id", "tenant_public", "MOUNT_TENANT_ID"),
        ("allow_provider_persona", True, "ALLOW_PROVIDER_PERSONA"),
        ("baa_attested", True, "BAA_ATTESTED"),
        ("feature_athena_labs", True, "FEATURE_ATHENA_LABS"),
        ("athena_client_id", "client-id-sentinel", "ATHENA_CLIENT_ID"),
        ("athena_client_secret", "secret-value-sentinel", "ATHENA_CLIENT_SECRET"),
        (
            "sovereign_record_grant_secret",
            "record-grant-secret-value-must-not-leak",
            "SOVEREIGN_RECORD_GRANT_SECRET",
        ),
        ("labs_allow_patient_search", True, "LABS_ALLOW_PATIENT_SEARCH"),
        ("deid_provider", "azure", "DEID_PROVIDER"),
        ("azure_deid_endpoint", "https://deid-sentinel.example", "AZURE_DEID_ENDPOINT"),
        ("azure_deid_tenant_id", "tenant-secret-sentinel", "AZURE_DEID_TENANT_ID"),
        ("azure_deid_client_id", "client-secret-sentinel", "AZURE_DEID_CLIENT_ID"),
        ("azure_deid_client_secret", "deid-secret-sentinel", "AZURE_DEID_CLIENT_SECRET"),
    ],
)
def test_public_profile_refuses_forbidden_configuration_without_value_leaks(
    setting, value, env_name,
):
    settings = Settings(**{setting: value})
    with pytest.raises(DeploymentConfigurationError) as caught:
        validate_deployment_configuration(settings)
    message = str(caught.value)
    assert env_name in message
    if isinstance(value, str):
        assert value not in message


@pytest.mark.parametrize("origins", [
    ["*"],
    ["https://app.example/path"],
    ["https://*.example"],
    ["http://app.example"],
    ["https://app.example", "https://app.example"],
])
def test_startup_rejects_non_exact_or_wildcard_cors_origins(origins):
    with pytest.raises(DeploymentConfigurationError, match="CORS_ALLOW_ORIGINS"):
        validate_deployment_configuration(Settings(cors_allow_origins=origins))


def test_startup_accepts_exact_https_and_mock_loopback_cors_origins():
    validate_deployment_configuration(Settings(
        cors_allow_origins=["https://app.example:8443"],
    ))
    validate_deployment_configuration(Settings(
        mock_mode=True,
        cors_allow_origins=["http://127.0.0.1:3000"],
    ))


def test_legacy_mount_overrides_fail_boot_instead_of_widening_profile():
    settings = Settings(
        mount_surface="provider_assistant",
        mount_auth_tier="provider",
        mount_capabilities=["ehr:labs:read"],
    )
    resolved = _run(PinnedResolver.from_settings(settings).resolve(
        MountResolveRequest(client_turn_id=uuid.uuid4(), trace_id=uuid.uuid4().hex),
    )).context
    assert resolved.surface == "public"
    assert "ehr:labs:read" not in resolved.capabilities
    with pytest.raises(DeploymentConfigurationError) as caught:
        validate_deployment_configuration(settings)
    message = str(caught.value)
    assert "MOUNT_SURFACE" in message
    assert "MOUNT_AUTH_TIER" in message
    assert "MOUNT_CAPABILITIES" in message
    assert "ehr:labs:read" not in message


def test_public_service_process_refuses_to_boot_with_ehr_credentials():
    sentinel = "must-never-appear-in-startup-errors"
    env = os.environ.copy()
    for name in (
        "MOUNT_SURFACE", "MOUNT_AUTH_TIER", "MOUNT_CAPABILITIES",
        "MOUNT_TENANT_ID", "AUTH_DEV_SECRET", "OIDC_ISSUER",
        "OIDC_AUDIENCE", "OIDC_JWKS_URL",
    ):
        env.pop(name, None)
    env.update({
        "MOUNT_PROFILE": "public",
        "AUTH_MODE": "disabled",
        "ATHENA_CLIENT_SECRET": sentinel,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    })
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "ATHENA_CLIENT_SECRET" in output
    assert sentinel not in output


def test_provider_boot_requires_tenant_and_production_oidc_configuration():
    with pytest.raises(DeploymentConfigurationError, match="MOUNT_TENANT_ID"):
        validate_deployment_configuration(Settings(
            mount_profile=MountProfile.PROVIDER_ASSISTANT,
            auth_mode=AuthMode.DEV_HS256,
        ))

    with pytest.raises(DeploymentConfigurationError, match="AUTH_MODE=oidc"):
        validate_deployment_configuration(Settings(
            mount_profile=MountProfile.PROVIDER_ASSISTANT,
            mount_tenant_id="tenant_test",
            mock_mode=False,
            auth_mode=AuthMode.DEV_HS256,
            auth_dev_secret="test-secret",
        ))

    with pytest.raises(DeploymentConfigurationError, match="BAA_ATTESTED"):
        validate_deployment_configuration(Settings(
            mount_profile=MountProfile.PROVIDER_ASSISTANT,
            mount_tenant_id="tenant_test",
            mock_mode=False,
            auth_mode=AuthMode.OIDC,
            oidc_issuer="https://identity.example",
            oidc_audience="cosmaneura-sovereign",
            oidc_jwks_url="https://identity.example/.well-known/jwks.json",
        ))

    with pytest.raises(DeploymentConfigurationError) as caught:
        validate_deployment_configuration(Settings(
            mount_profile=MountProfile.PROVIDER_ASSISTANT,
            mount_tenant_id="tenant_test",
            mock_mode=False,
            auth_mode=AuthMode.OIDC,
            baa_attested=True,
        ))
    assert all(name in str(caught.value) for name in (
        "OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL",
    ))

    wrong_deid = "provider-deid-value-must-not-leak"
    with pytest.raises(DeploymentConfigurationError) as caught:
        validate_deployment_configuration(Settings(
            mount_profile=MountProfile.PROVIDER_ASSISTANT,
            mount_tenant_id="tenant_test",
            mock_mode=False,
            auth_mode=AuthMode.OIDC,
            baa_attested=True,
            oidc_issuer="https://identity.example",
            oidc_audience="cosmaneura-sovereign",
            oidc_jwks_url="https://identity.example/.well-known/jwks.json",
            deid_provider=wrong_deid,
        ))
    assert "DEID_PROVIDER" in str(caught.value)
    assert wrong_deid not in str(caught.value)

    with pytest.raises(DeploymentConfigurationError, match="AZURE_DEID_ENDPOINT"):
        validate_deployment_configuration(Settings(
            mount_profile=MountProfile.PROVIDER_ASSISTANT,
            mount_tenant_id="tenant_test",
            mock_mode=False,
            auth_mode=AuthMode.OIDC,
            baa_attested=True,
            oidc_issuer="https://identity.example",
            oidc_audience="cosmaneura-sovereign",
            oidc_jwks_url="https://identity.example/.well-known/jwks.json",
            deid_provider="azure",
        ))

    invalid_grant_keys = (
        "too-short",
        "A" * 44,
        "A" * 42 + "==",
        "!" * 43 + "=",
    )
    for invalid_grant_key in invalid_grant_keys:
        with pytest.raises(
            DeploymentConfigurationError,
            match="SOVEREIGN_RECORD_GRANT_SECRET",
        ):
            validate_deployment_configuration(Settings(
                mount_profile=MountProfile.PROVIDER_ASSISTANT,
                mount_tenant_id="tenant_test",
                mock_mode=False,
                auth_mode=AuthMode.OIDC,
                baa_attested=True,
                oidc_issuer="https://identity.example",
                oidc_audience="cosmaneura-sovereign",
                oidc_jwks_url="https://identity.example/.well-known/jwks.json",
                deid_provider="azure",
                azure_deid_endpoint="https://deid.example",
                sovereign_record_grant_secret=invalid_grant_key,
            ))

    validate_deployment_configuration(Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
        mock_mode=False,
        auth_mode=AuthMode.OIDC,
        baa_attested=True,
        oidc_issuer="https://identity.example",
        oidc_audience="cosmaneura-sovereign",
        oidc_jwks_url="https://identity.example/.well-known/jwks.json",
        deid_provider="azure",
        azure_deid_endpoint="https://deid.example",
        sovereign_record_grant_secret="A" * 43 + "=",
    ))


@pytest.mark.parametrize("ehr_settings", [
    {"feature_athena_labs": True},
    {"athena_client_id": "client-id-sentinel"},
    {"athena_client_secret": "client-secret-sentinel"},
    {"labs_allow_patient_search": True},
])
def test_startup_rejects_operational_ehr_configuration_outside_preview(
    ehr_settings,
):
    settings = Settings(athena_env="production", **ehr_settings)
    with pytest.raises(DeploymentConfigurationError) as caught:
        validate_deployment_configuration(settings)
    message = str(caught.value)
    assert "ATHENA_ENV" in message
    for value in ehr_settings.values():
        if isinstance(value, str):
            assert value not in message


def test_mock_provider_may_enable_only_preview_ehr_configuration():
    validate_deployment_configuration(Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
        auth_mode=AuthMode.DEV_HS256,
        auth_dev_secret="local-test-secret",
        feature_athena_labs=True,
        athena_env="preview",
    ))
    with pytest.raises(DeploymentConfigurationError, match="ATHENA_ENV"):
        validate_deployment_configuration(Settings(
            mount_profile=MountProfile.PROVIDER_ASSISTANT,
            mount_tenant_id="tenant_test",
            auth_mode=AuthMode.DEV_HS256,
            auth_dev_secret="local-test-secret",
            feature_athena_labs=True,
            athena_env="PREVIEW",
        ))


def test_token_bucket_refills_per_ip_and_bounds_client_memory():
    now = [100.0]
    limiter = TokenBucketRateLimiter(
        requests=2,
        window_seconds=10,
        max_clients=2,
        clock=lambda: now[0],
    )
    assert limiter.check("192.0.2.1").allowed
    assert limiter.check("192.0.2.1").allowed
    denied = limiter.check("192.0.2.1")
    assert not denied.allowed and denied.retry_after_seconds == 5
    now[0] += 5
    assert limiter.check("192.0.2.1").allowed
    assert limiter.check("192.0.2.2").allowed
    assert limiter.check("192.0.2.3").allowed
    assert len(limiter._buckets) == 2


def test_public_chat_rate_limit_uses_trusted_rightmost_ip_and_spares_emergencies():
    settings = Settings(public_chat_trust_forwarded_for=True)
    limiter = TokenBucketRateLimiter(
        requests=2,
        window_seconds=60,
        max_clients=100,
        clock=lambda: 100.0,
    )
    client = TestClient(main_module.app)
    body = {"persona": "patient", "message": "hello"}
    with patch.object(main_module, "_settings", settings):
        with patch.object(main_module, "_public_chat_limiter", limiter):
            first = client.post(
                "/chat", json=body,
                headers={"X-Forwarded-For": "198.51.100.9, 203.0.113.7"},
            )
            second = client.post(
                "/chat", json=body,
                headers={"X-Forwarded-For": "192.0.2.99, 203.0.113.7"},
            )
            denied = client.post(
                "/chat", json=body,
                headers={"X-Forwarded-For": "192.0.2.50, 203.0.113.7"},
            )
            other_ip = client.post(
                "/chat", json=body,
                headers={"X-Forwarded-For": "198.51.100.20"},
            )
            emergency = client.post(
                "/chat",
                json={"persona": "provider", "message": "crushing chest pain right now"},
                headers={"X-Forwarded-For": "203.0.113.7"},
            )

    assert first.status_code == second.status_code == 200
    assert denied.status_code == 429
    assert denied.headers["retry-after"] == "30"
    assert other_ip.status_code == 200
    assert emergency.status_code == 200
    assert "911" in emergency.json()["answer"]


def test_forwarded_ip_is_ignored_without_explicit_proxy_trust():
    settings = Settings(public_chat_trust_forwarded_for=False)
    limiter = TokenBucketRateLimiter(
        requests=1,
        window_seconds=60,
        max_clients=100,
        clock=lambda: 100.0,
    )
    client = TestClient(main_module.app)
    body = {"persona": "patient", "message": "hello"}
    with patch.object(main_module, "_settings", settings):
        with patch.object(main_module, "_public_chat_limiter", limiter):
            first = client.post(
                "/chat", json=body,
                headers={"X-Forwarded-For": "198.51.100.1"},
            )
            spoofed = client.post(
                "/chat", json=body,
                headers={"X-Forwarded-For": "198.51.100.2"},
            )
    assert first.status_code == 200
    assert spoofed.status_code == 429
