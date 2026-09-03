"""Offline tests for the deployed synthetic Session Prep canary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from tools import smoke_session_prep
from tools.deployed_smoke import SmokeFailure


PROVIDER_URL = "https://provider.test"
PLATFORM_URL = "https://platform.test"
PROVIDER_TOKEN = "provider-header.provider-payload.provider-signature"
MOUNT_TOKEN = "opaque-platform-mount-token-123456"
PLATFORM_RECORD_REF = "record_synthetic_session_prep"
TRACE_ID = "trace_session_prep_canary"


def _protected_headers(*, trace: bool = False) -> dict[str, str]:
    headers = {
        "X-Cosmaneura-Embed-Version": "1.0",
        "Cache-Control": "private, no-store",
    }
    if trace:
        headers["X-Trace-ID"] = TRACE_ID
    return headers


def _ready_payload(client_turn_id: str) -> dict:
    return {
        "client_turn_id": client_turn_id,
        "state": "ready",
        "freshness": "current",
        "current_through": "2026-09-03T12:00:00Z",
        "source_count": 1,
        "prepared_brief": {
            "recap": "Synthetic finalized-note recap.",
            "trajectory_and_material_changes": ["Synthetic trajectory."],
            "talking_points": ["Review the synthetic goal."],
            "open_threads": ["Synthetic follow-up question."],
            "cautions": [],
            "suggested_outline": ["Confirm the synthetic agenda."],
            "patient_reported": None,
        },
        "retry_after_seconds": None,
        "reason_code": None,
    }


def _problem_payload() -> dict:
    return {
        "type": "https://contracts.cosmaneura.com/problems/capability_denied",
        "title": "Action unavailable",
        "status": 403,
        "code": "CAPABILITY_DENIED",
        "detail": "This action is not available in the current context.",
        "trace_id": TRACE_ID,
        "retryable": False,
    }


SessionResponse = Callable[[httpx.Request], httpx.Response]


class SyntheticDeployment:
    def __init__(
        self,
        *,
        enabled: bool,
        session_response: SessionResponse | None = None,
        exchange_headers: dict[str, str] | None = None,
    ) -> None:
        self.enabled = enabled
        self.session_response = session_response
        self.exchange_headers = exchange_headers or _protected_headers()
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "platform.test":
            assert request.url.path == "/embed/v1/context-tokens/exchange"
            assert request.headers["authorization"] == f"Bearer {PROVIDER_TOKEN}"
            assert request.headers["X-Cosmaneura-Embed-Version"] == "1.0"
            assert json.loads(request.content) == {
                "requested_surface": "provider_assistant",
                "record_ref": PLATFORM_RECORD_REF,
            }
            return httpx.Response(
                200,
                json={
                    "context_token": MOUNT_TOKEN,
                    "token_type": "Bearer",
                    "issued_at": "2026-09-03T12:00:00Z",
                    "refresh_after": "2026-09-03T12:04:00Z",
                    "expires_at": "2026-09-03T12:05:00Z",
                },
                headers=self.exchange_headers,
            )

        assert request.url.host == "provider.test"
        if request.url.path == "/healthz":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "mock_mode": False,
                    "mount_profile": "provider_assistant",
                    "auth_mode": "disabled",
                    "platform_agent_api_enabled": True,
                },
            )
        if request.url.path == "/welcome":
            assert request.headers["authorization"] == f"Bearer {MOUNT_TOKEN}"
            assert request.headers["X-Cosmaneura-Embed-Version"] == "1.0"
            assert "x-cosmaneura-client-turn-id" not in request.headers
            paths = [{"label": "Explain", "message": "Explain", "intent": "engagement"}]
            if self.enabled:
                paths.insert(
                    0,
                    {
                        "label": "Open Session Prep",
                        "message": "Open Session Prep",
                        "intent": "session_prep",
                    },
                )
            return httpx.Response(
                200,
                json={"persona": "provider", "paths": paths},
                headers=_protected_headers(),
            )
        assert request.url.path == "/session-prep"
        assert request.headers["authorization"] == f"Bearer {MOUNT_TOKEN}"
        assert request.headers["X-Cosmaneura-Embed-Version"] == "1.0"
        body = json.loads(request.content)
        assert set(body) == {"client_turn_id"}
        assert request.headers["X-Cosmaneura-Client-Turn-ID"] == body["client_turn_id"]
        if self.session_response is not None:
            return self.session_response(request)
        if not self.enabled:
            return httpx.Response(
                403,
                json=_problem_payload(),
                headers={
                    **_protected_headers(trace=True),
                    "Content-Type": "application/problem+json",
                },
            )
        return httpx.Response(
            200,
            json=_ready_payload(body["client_turn_id"]),
            headers=_protected_headers(trace=True),
        )

    @property
    def session_requests(self) -> list[httpx.Request]:
        return [
            request for request in self.requests if request.url.path == "/session-prep"
        ]


def _exercise(
    deployment: SyntheticDeployment,
    expectation: smoke_session_prep.ExpectedState,
    *,
    synthetic_confirmed: bool = True,
    emit: Callable[[str], None] = lambda _message: None,
) -> None:
    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(deployment),
            follow_redirects=False,
        ) as client:
            await smoke_session_prep.run(
                client,
                PROVIDER_URL,
                PLATFORM_URL,
                PROVIDER_TOKEN,
                PLATFORM_RECORD_REF,
                expectation,
                synthetic_confirmed=synthetic_confirmed,
                emit=emit,
            )

    asyncio.run(exercise())


def test_ready_canary_uses_one_minimized_request_and_emits_no_content() -> None:
    deployment = SyntheticDeployment(enabled=True)
    emitted: list[str] = []

    _exercise(deployment, "synthetic-ready", emit=emitted.append)

    assert len(deployment.session_requests) == 1
    body = json.loads(deployment.session_requests[0].content)
    assert set(body) == {"client_turn_id"}
    output = "\n".join(emitted)
    assert "prepared brief passed" in output
    for hidden in (
        PROVIDER_TOKEN,
        MOUNT_TOKEN,
        PLATFORM_RECORD_REF,
        "Synthetic finalized-note recap.",
    ):
        assert hidden not in output


def test_disabled_canary_proves_path_absence_and_closed_denial() -> None:
    deployment = SyntheticDeployment(enabled=False)
    emitted: list[str] = []

    _exercise(deployment, "disabled", emit=emitted.append)

    assert len(deployment.session_requests) == 1
    assert any("absent and denied closed" in message for message in emitted)


def test_canary_requires_confirmation_before_any_request() -> None:
    deployment = SyntheticDeployment(enabled=False)

    with pytest.raises(SmokeFailure, match="synthetic record confirmation"):
        _exercise(deployment, "disabled", synthetic_confirmed=False)

    assert deployment.requests == []


def test_confirmation_environment_value_is_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED", raising=False)
    with pytest.raises(SmokeFailure, match="must be exactly true"):
        smoke_session_prep.required_synthetic_confirmation()
    monkeypatch.setenv("SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED", "True")
    with pytest.raises(SmokeFailure, match="must be exactly true"):
        smoke_session_prep.required_synthetic_confirmation()
    monkeypatch.setenv("SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED", "true")
    assert smoke_session_prep.required_synthetic_confirmation() is True


def test_canary_rejects_same_provider_and_platform_host_before_request() -> None:
    deployment = SyntheticDeployment(enabled=False)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(deployment)
        ) as client:
            await smoke_session_prep.run(
                client,
                PROVIDER_URL,
                "https://PROVIDER.test:443",
                PROVIDER_TOKEN,
                PLATFORM_RECORD_REF,
                "disabled",
                synthetic_confirmed=True,
            )

    with pytest.raises(SmokeFailure, match="different HTTPS hostnames"):
        asyncio.run(exercise())
    assert deployment.requests == []


def test_ready_canary_rejects_nonready_and_server_only_responses() -> None:
    def refreshing(request: httpx.Request) -> httpx.Response:
        payload = _ready_payload(json.loads(request.content)["client_turn_id"])
        payload.update(
            state="refreshing",
            freshness="stale",
            retry_after_seconds=5,
            reason_code="refresh_in_progress",
        )
        return httpx.Response(
            200,
            json=payload,
            headers=_protected_headers(trace=True),
        )

    with pytest.raises(SmokeFailure, match="current brief"):
        _exercise(
            SyntheticDeployment(enabled=True, session_response=refreshing),
            "synthetic-ready",
        )

    def server_only(request: httpx.Request) -> httpx.Response:
        payload = _ready_payload(json.loads(request.content)["client_turn_id"])
        payload["context_snapshot_ref"] = "context_synthetic_must_not_cross"
        return httpx.Response(
            200,
            json=payload,
            headers=_protected_headers(trace=True),
        )

    with pytest.raises(SmokeFailure, match="server-only fields"):
        _exercise(
            SyntheticDeployment(enabled=True, session_response=server_only),
            "synthetic-ready",
        )

    def identifier_in_text(request: httpx.Request) -> httpx.Response:
        payload = _ready_payload(json.loads(request.content)["client_turn_id"])
        payload["prepared_brief"]["recap"] = PLATFORM_RECORD_REF
        return httpx.Response(
            200,
            json=payload,
            headers=_protected_headers(trace=True),
        )

    with pytest.raises(SmokeFailure, match="sensitive identifier"):
        _exercise(
            SyntheticDeployment(enabled=True, session_response=identifier_in_text),
            "synthetic-ready",
        )


def test_ready_canary_rejects_correlation_and_closed_schema_mismatch() -> None:
    def wrong_turn(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_ready_payload("8baea186-2f51-4826-9692-121ae8ef5cb8"),
            headers=_protected_headers(trace=True),
        )

    with pytest.raises(SmokeFailure, match="changed client-turn identity"):
        _exercise(
            SyntheticDeployment(enabled=True, session_response=wrong_turn),
            "synthetic-ready",
        )

    def malformed(request: httpx.Request) -> httpx.Response:
        payload = _ready_payload(json.loads(request.content)["client_turn_id"])
        del payload["prepared_brief"]["suggested_outline"]
        return httpx.Response(
            200,
            json=payload,
            headers=_protected_headers(trace=True),
        )

    with pytest.raises(SmokeFailure, match="invalid browser response"):
        _exercise(
            SyntheticDeployment(enabled=True, session_response=malformed),
            "synthetic-ready",
        )


@pytest.mark.parametrize("failure", ["redirect", "oversized"])
def test_canary_rejects_redirects_and_oversized_responses(failure: str) -> None:
    def unsafe(request: httpx.Request) -> httpx.Response:
        if failure == "redirect":
            return httpx.Response(
                307,
                headers={"Location": "https://redirect.test/session-prep"},
            )
        return httpx.Response(
            200,
            content=b"{" + b"x" * smoke_session_prep.MAX_CANARY_RESPONSE_BYTES,
            headers={
                **_protected_headers(trace=True),
                "Content-Type": "application/json",
            },
        )

    deployment = SyntheticDeployment(enabled=True, session_response=unsafe)
    with pytest.raises(SmokeFailure):
        _exercise(deployment, "synthetic-ready")
    assert len(deployment.session_requests) == 1


@pytest.mark.parametrize(
    "headers",
    [
        {"Cache-Control": "no-store", "X-Trace-ID": TRACE_ID},
        {"X-Cosmaneura-Embed-Version": "1.0", "X-Trace-ID": TRACE_ID},
        {"X-Cosmaneura-Embed-Version": "1.0", "Cache-Control": "no-store"},
    ],
)
def test_canary_requires_protected_response_headers(headers: dict[str, str]) -> None:
    def missing_header(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_ready_payload(json.loads(request.content)["client_turn_id"]),
            headers=headers,
        )

    with pytest.raises(SmokeFailure):
        _exercise(
            SyntheticDeployment(enabled=True, session_response=missing_header),
            "synthetic-ready",
        )


@pytest.mark.parametrize(
    "headers",
    [
        {"Cache-Control": "no-store"},
        {"X-Cosmaneura-Embed-Version": "1.0"},
    ],
)
def test_canary_requires_protected_exchange_headers(headers: dict[str, str]) -> None:
    deployment = SyntheticDeployment(enabled=True, exchange_headers=headers)

    with pytest.raises(SmokeFailure):
        _exercise(deployment, "synthetic-ready")

    assert deployment.session_requests == []


def test_failure_does_not_echo_credentials_record_or_response_body() -> None:
    upstream_body = "clinical-body-must-never-print"

    def unavailable(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=upstream_body)

    deployment = SyntheticDeployment(enabled=True, session_response=unavailable)
    emitted: list[str] = []
    with pytest.raises(SmokeFailure) as caught:
        _exercise(deployment, "synthetic-ready", emit=emitted.append)

    visible = f"{caught.value}\n" + "\n".join(emitted)
    for hidden in (
        upstream_body,
        PROVIDER_TOKEN,
        MOUNT_TOKEN,
        PLATFORM_RECORD_REF,
    ):
        assert hidden not in visible
    assert len(deployment.session_requests) == 1
