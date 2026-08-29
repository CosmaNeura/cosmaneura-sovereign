"""End-to-end regressions for the loopback named-provider walkthrough."""
from pathlib import Path
import time
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main as main_module
from app.config import AuthMode, MountProfile, Settings
from app.schemas import EmbedChatRequest
from app.security.auth import TokenVerifier
from app.sovereign.orchestrator import Sovereign


ROOT = Path(__file__).resolve().parents[1]
EMBED = {"X-Cosmaneura-Embed-Version": "1.0"}


def _settings() -> Settings:
    return Settings(
        mock_mode=True,
        demo_ui_enabled=True,
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_local_named_beta",
        auth_mode=AuthMode.DEV_HS256,
        auth_dev_secret="local-test-secret-at-least-thirty-two-bytes",
        baa_attested=False,
        feature_athena_labs=False,
        platform_agent_api_enabled=False,
    )


def _token(settings: Settings, role: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": "local-fixture", "roles": [role], "iat": now, "exp": now + 600},
        settings.auth_dev_secret,
        algorithm="HS256",
    )


def test_named_provider_completes_real_mock_orchestrator_walkthrough_offline():
    settings = _settings()
    headers = {**EMBED, "Authorization": f"Bearer {_token(settings, 'provider')}"}
    no_network = AsyncMock(side_effect=AssertionError("walkthrough attempted outbound HTTP"))

    with patch.object(main_module, "_settings", settings), \
            patch.object(main_module, "_verifier", TokenVerifier(settings)), \
            patch.object(main_module, "_sovereign", Sovereign(settings)), \
            patch.object(httpx.AsyncClient, "get", no_network), \
            patch.object(httpx.AsyncClient, "post", no_network):
        client = TestClient(main_module.app)
        welcome = client.get("/welcome", headers=headers)
        assert welcome.status_code == 200
        payload = welcome.json()
        assert payload["persona"] == "provider"
        assert payload["walkthrough_mode"] == "local_synthetic"
        assert [path["intent"] for path in payload["paths"]] == [
            "visit_prep", "referral",
        ]

        guided = payload["paths"][0]
        turn_id = str(uuid.uuid4())
        response = client.post(
            "/chat",
            headers={**headers, "X-Cosmaneura-Client-Turn-ID": turn_id},
            json={
                "client_turn_id": turn_id,
                "message": guided["message"],
                "metadata": {
                    "clientId": "synthetic_walkthrough_01",
                    "thread_ref": "thread_synthetic_walkthrough_01",
                    "context_snapshot_ref": "snapshot_synthetic_walkthrough_01",
                    "output_format": "chat",
                    "care_model": "synthetic walkthrough",
                },
            },
        )
        referral_turn_id = str(uuid.uuid4())
        referral = client.post(
            "/chat",
            headers={
                **headers,
                "X-Cosmaneura-Client-Turn-ID": referral_turn_id,
            },
            json={
                "client_turn_id": referral_turn_id,
                "message": payload["paths"][1]["message"],
                "metadata": {
                    "clientId": "synthetic_walkthrough_01",
                    "thread_ref": "thread_synthetic_walkthrough_01",
                    "context_snapshot_ref": "snapshot_synthetic_walkthrough_01",
                    "output_format": "chat",
                    "care_model": "synthetic walkthrough",
                },
            },
        )
        rejected_turn_id = str(uuid.uuid4())
        rejected = client.post(
            "/chat",
            headers={
                **headers,
                "X-Cosmaneura-Client-Turn-ID": rejected_turn_id,
            },
            json={
                "client_turn_id": rejected_turn_id,
                "message": "free-form clinical text must not enter this fixture",
                "metadata": {
                    "clientId": "synthetic_walkthrough_01",
                    "thread_ref": "thread_synthetic_walkthrough_01",
                    "context_snapshot_ref": "snapshot_synthetic_walkthrough_01",
                    "output_format": "chat",
                    "care_model": "synthetic walkthrough",
                },
            },
        )
        context_turn_id = str(uuid.uuid4())
        context_rejected = client.post(
            "/chat",
            headers={
                **headers,
                "X-Cosmaneura-Client-Turn-ID": context_turn_id,
            },
            json={
                "client_turn_id": context_turn_id,
                "message": guided["message"],
                "metadata": {
                    "clientId": "synthetic_walkthrough_01",
                    "thread_ref": "thread_synthetic_walkthrough_01",
                    "context_snapshot_ref": "snapshot_synthetic_walkthrough_01",
                    "output_format": "note",
                    "care_model": "synthetic walkthrough",
                },
            },
        )
        digest_guard = AsyncMock(
            side_effect=AssertionError("local fixture attempted digest orchestration")
        )
        with patch.object(main_module._sovereign, "digest", digest_guard):
            digest = client.post(
                "/digest",
                headers=headers,
                json={
                    "persona": "provider",
                    "sessions": ["arbitrary session text"],
                },
            )

    assert response.status_code == 200
    result = response.json()
    assert result["persona"] == "provider"
    assert result["intent"] == "visit_prep"
    assert result["structured"]["feature"] == "visit_prep"
    assert result["doctrinal_review"]["conforms"] is True
    assert referral.status_code == 200
    assert referral.json()["intent"] == "referral"
    assert referral.json()["structured"]["matches"]
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "VALIDATION_FAILED"
    assert context_rejected.status_code == 400
    assert context_rejected.json()["code"] == "VALIDATION_FAILED"
    assert digest.status_code == 503
    assert digest.json()["code"] == "SERVICE_UNAVAILABLE"
    assert digest_guard.await_count == 0
    assert no_network.await_count == 0


def test_local_welcome_remains_closed_to_unsigned_and_wrong_role_callers():
    settings = _settings()
    with patch.object(main_module, "_settings", settings), \
            patch.object(main_module, "_verifier", TokenVerifier(settings)):
        client = TestClient(main_module.app)
        unsigned = client.get("/welcome", headers=EMBED)
        wrong_role = client.get(
            "/welcome",
            headers={**EMBED, "Authorization": f"Bearer {_token(settings, 'patient')}"},
        )
    assert unsigned.status_code == 401
    assert wrong_role.status_code == 403


def test_local_launcher_is_mock_loopback_only_and_keeps_latches_off():
    source = (ROOT / "tools" / "run_demo_web.sh").read_text()
    named = source.split("# Load local demo secrets", 1)[0]
    for expected in (
        "MOCK_MODE=true", "MOUNT_PROFILE=provider_assistant",
        "AUTH_MODE=dev_hs256", "PLATFORM_AGENT_API_ENABLED=false",
        "FEATURE_ATHENA_LABS=false", "BAA_ATTESTED=false",
        "SOVEREIGN_DEPLOY_ENABLED=false", "--host 127.0.0.1",
    ):
        assert expected in named
    assert "az " not in named
    assert "--apply" not in named


def test_browser_context_hints_bound_care_model():
    with pytest.raises(ValidationError):
        EmbedChatRequest(
            client_turn_id=str(uuid.uuid4()),
            message="synthetic",
            metadata={"care_model": "x" * 81},
        )
