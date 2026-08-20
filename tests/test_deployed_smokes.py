"""Offline contract tests for the deployed, credential-safe smoke clients."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.clients.labs import SANDBOX_LABEL
from tools import smoke_labs, smoke_live
from tools.deployed_smoke import SmokeFailure, distinct_origins, request_json


PROVIDER_TOKEN = "provider-header.provider-payload.provider-signature"
NON_PROVIDER_TOKEN = "member-header.member-payload.member-signature"
RECORD_GRANT = "gAAAAA" + "a" * 100 + "=="
MOUNT_TOKEN = "opaque-platform-mount-token-123456"
PLATFORM_RECORD_REF = "record_synthetic_123456"


def _health(mount: str, auth_mode: str) -> dict:
    return {
        "status": "ok",
        "mock_mode": False,
        "mount_profile": mount,
        "auth_mode": auth_mode,
        "platform_agent_api_enabled": mount == "provider_assistant",
    }


def _welcome(provider: bool, *, labs: bool = True) -> dict:
    if provider:
        return {
            "persona": "provider",
            "paths": (
                [{"intent": "engagement"}, {"intent": "lab_review"}]
                if labs
                else [{"intent": "engagement"}]
            ),
            "labs_tier": "sandbox" if labs else None,
            "labs_label": SANDBOX_LABEL if labs else None,
        }
    return {
        "persona": "patient",
        "paths": [{"intent": "engagement"}],
        "labs_tier": None,
        "labs_label": None,
    }


def _chat(
    persona: str,
    intent: str,
    client_turn_id: str,
    *,
    denied: bool = False,
) -> dict:
    return {
        "answer": "synthetic response",
        "persona": persona,
        "intent": intent,
        "structured": {},
        "safety_notice": "capability_denied" if denied else None,
        "trace_id": "trace-synthetic",
        "client_turn_id": client_turn_id,
    }


def test_live_smoke_checks_mounts_roles_and_public_capability_denial() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "platform.test":
            assert request.url.path == "/embed/v1/context-tokens/exchange"
            assert request.headers["X-Cosmaneura-Embed-Version"] == "1.0"
            if request.headers["authorization"] == f"Bearer {NON_PROVIDER_TOKEN}":
                return httpx.Response(403, json={"detail": "Unavailable."})
            assert request.headers["authorization"] == f"Bearer {PROVIDER_TOKEN}"
            return httpx.Response(200, json={
                "context_token": MOUNT_TOKEN,
                "token_type": "Bearer",
                "issued_at": "2026-08-19T12:00:00Z",
                "refresh_after": "2026-08-19T12:04:00Z",
                "expires_at": "2026-08-19T12:05:00Z",
            })
        provider = request.url.host == "provider.test"
        if request.url.path == "/healthz":
            return httpx.Response(
                200,
                json=_health(
                    "provider_assistant" if provider else "public",
                    "disabled",
                ),
            )
        if request.url.path == "/welcome":
            assert request.headers["X-Cosmaneura-Embed-Version"] == "1.0"
            if provider:
                if "authorization" not in request.headers:
                    return httpx.Response(
                        401,
                        json={"code": "CONTEXT_REQUIRED"},
                        headers={"X-Cosmaneura-Embed-Version": "1.0"},
                    )
                assert request.headers["authorization"] == f"Bearer {MOUNT_TOKEN}"
                assert "x-cosmaneura-client-turn-id" not in request.headers
                assert "x-cosmaneura-mount-context" not in request.headers
            return httpx.Response(
                200,
                json=_welcome(provider, labs=False),
                headers={"X-Cosmaneura-Embed-Version": "1.0"},
            )
        assert request.url.path == "/chat"
        assert request.headers["X-Cosmaneura-Embed-Version"] == "1.0"
        authorization = request.headers.get("authorization")
        body = __import__("json").loads(request.content)
        turn_id = body["client_turn_id"]
        if provider:
            if authorization is None:
                return httpx.Response(
                    401,
                    json={"detail": "Not authenticated."},
                    headers={"X-Cosmaneura-Embed-Version": "1.0"},
                )
            if "x-cosmaneura-client-turn-id" not in request.headers:
                return httpx.Response(
                    422,
                    json={"detail": "Unavailable."},
                    headers={"X-Cosmaneura-Embed-Version": "1.0"},
                )
            if request.headers["x-cosmaneura-client-turn-id"] == "not-a-uuid":
                return httpx.Response(
                    422,
                    json={"detail": "Unavailable."},
                    headers={"X-Cosmaneura-Embed-Version": "1.0"},
                )
            assert authorization == f"Bearer {MOUNT_TOKEN}"
            assert request.headers["x-cosmaneura-client-turn-id"] == turn_id
            return httpx.Response(
                200,
                json=_chat("provider", "engagement", turn_id),
                headers={"X-Cosmaneura-Embed-Version": "1.0"},
            )
        if "lab results" in body["message"]:
            return httpx.Response(
                200,
                json=_chat("patient", "lab_review", turn_id, denied=True),
                headers={"X-Cosmaneura-Embed-Version": "1.0"},
            )
        return httpx.Response(
            200,
            json=_chat("patient", "engagement", turn_id),
            headers={"X-Cosmaneura-Embed-Version": "1.0"},
        )

    emitted: list[str] = []
    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
        ) as client:
            await smoke_live.run(
                client,
                "https://public.test",
                "https://provider.test",
                "https://platform.test",
                PROVIDER_TOKEN,
                NON_PROVIDER_TOKEN,
                emit=emitted.append,
            )

    asyncio.run(exercise())
    assert any("without provider authority" in line for line in emitted)
    assert any("cannot reach the provider labs capability" in line for line in emitted)


def test_labs_smoke_checks_one_host_bound_synthetic_preview_read() -> None:
    record_hash = "0123456789abcdef"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "platform.test":
            assert request.url.path == "/embed/v1/context-tokens/exchange"
            assert __import__("json").loads(request.content)["record_ref"] == (
                PLATFORM_RECORD_REF
            )
            return httpx.Response(200, json={
                "context_token": MOUNT_TOKEN,
                "token_type": "Bearer",
                "issued_at": "2026-08-19T12:00:00Z",
                "refresh_after": "2026-08-19T12:04:00Z",
                "expires_at": "2026-08-19T12:05:00Z",
            })
        if request.url.path == "/healthz":
            return httpx.Response(200, json=_health("provider_assistant", "disabled"))
        if request.url.path == "/welcome":
            assert request.headers["authorization"] == f"Bearer {MOUNT_TOKEN}"
            assert request.headers["X-Cosmaneura-Embed-Version"] == "1.0"
            assert "x-cosmaneura-client-turn-id" not in request.headers
            assert "x-cosmaneura-mount-context" not in request.headers
            return httpx.Response(
                200,
                json=_welcome(True),
                headers={"X-Cosmaneura-Embed-Version": "1.0"},
            )
        assert request.headers["authorization"] == f"Bearer {MOUNT_TOKEN}"
        assert "x-cosmaneura-client-turn-id" in request.headers
        body = __import__("json").loads(request.content)
        assert body["client_turn_id"] == request.headers[
            "x-cosmaneura-client-turn-id"
        ]
        return httpx.Response(200, json={
            "answer": f"{SANDBOX_LABEL}.\n\nSynthetic summary",
            "persona": "provider",
            "intent": "lab_review",
            "structured": {
                "feature": "lab_review",
                "tier": "sandbox",
                "sandbox_label": SANDBOX_LABEL,
                "result_count": 2,
                "access": {
                    "record_ref_sha256_16": record_hash,
                    "resolved_by": "platform_signed_open_record",
                    "sandbox": True,
                },
            },
            "safety_notice": None,
            "trace_id": "trace-lab-synthetic",
            "client_turn_id": body["client_turn_id"],
            "doctrinal_review": {"conforms": True},
            "citations": [{"source": "athenahealth Preview (synthetic EMR)"}],
        }, headers={"X-Cosmaneura-Embed-Version": "1.0"})

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
        ) as client:
            await smoke_labs.run(
                client,
                "https://provider.test",
                "https://platform.test",
                PROVIDER_TOKEN,
                PLATFORM_RECORD_REF,
                RECORD_GRANT,
                emit=lambda _message: None,
            )

    asyncio.run(exercise())


def test_labs_smoke_requires_a_fresh_grant_not_a_raw_record_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED", "true")
    monkeypatch.setenv("SOVEREIGN_SYNTHETIC_RECORD_GRANT", "a-195900.E-60178")
    with pytest.raises(SmokeFailure, match="fresh host-issued Fernet token"):
        smoke_labs.required_synthetic_record_grant()

    monkeypatch.setenv("SOVEREIGN_SYNTHETIC_RECORD_GRANT", RECORD_GRANT)
    assert smoke_labs.required_synthetic_record_grant() == RECORD_GRANT


def test_smoke_failures_do_not_echo_token_or_response_body() -> None:
    sentinel = "clinical-body-must-never-print"

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(500, text=sentinel)
            )
        ) as client:
            with pytest.raises(SmokeFailure) as caught:
                await request_json(
                    client,
                    "GET",
                    "https://provider.test",
                    "/healthz",
                    headers={"Authorization": f"Bearer {PROVIDER_TOKEN}"},
                )
        message = str(caught.value)
        assert sentinel not in message
        assert PROVIDER_TOKEN not in message

    asyncio.run(exercise())


def test_smoke_requires_physically_distinct_hostnames() -> None:
    with pytest.raises(SmokeFailure, match="different HTTPS hostnames"):
        distinct_origins("https://EXAMPLE.test", "https://example.test:443")
