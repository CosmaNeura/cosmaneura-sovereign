from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from app.clients.platform_audit import (
    AuditDeliveryError,
    AuditTurn,
    PlatformAuditClient,
)


def _run(coro):
    return asyncio.run(coro)


def _turn() -> AuditTurn:
    return AuditTurn(
        interaction_ref="interaction_ref_123456",
        client_turn_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        trace_id="trace_identifier_123456",
        observed_at=datetime(2026, 8, 19, tzinfo=UTC),
        conversation_turn_ref="conversation_turn_123456",
        mount_context_token="mount_context_token_123456",
    )


def test_observation_is_closed_and_hash_only():
    turn = _turn()
    observation = turn.observation(
        event_type="sovereign.turn.completed",
        status="completed",
        route="lab_review",
        intent="lab_review",
        content="sentinel patient content",
    )

    body = observation.model_dump(mode="json")
    assert set(body) == {
        "client_event_id",
        "observed_at",
        "event_type",
        "observation_status",
        "route",
        "intent",
        "content_sha256",
        "platform_refs",
    }
    assert body["content_sha256"] == (
        "eb7bbbc74e9791541bc3328c26e6f4a05724f0ada8a90c155d44d54de8ccec8c"
    )
    assert "sentinel" not in str(body)


def test_writer_retries_with_exact_key_and_validates_receipt():
    turn = _turn()
    observation = turn.observation(
        event_type="sovereign.turn.completed",
        status="completed",
        route="engagement",
        intent="engagement",
        content="safe result",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                503,
                json={"retryable": True, "detail": "unavailable"},
            )
        return httpx.Response(
            201,
            headers={
                "X-Trace-ID": turn.trace_id,
                "Idempotency-Replayed": "false",
            },
            json={
                "record_ref": "audit_event_123456",
                "recorded_at": datetime.now(UTC).isoformat(),
            },
        )

    async def token() -> str:
        return "workload-token"

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = PlatformAuditClient(
                platform_base_url="https://platform.example",
                workload_token_provider=token,
                client=http_client,
            )
            return await client.write(turn=turn, observation=observation)

    receipt = _run(scenario())
    assert receipt.record_ref == "audit_event_123456"
    assert len(requests) == 2
    assert {request.headers["Idempotency-Key"] for request in requests} == {
        turn.idempotency_key(observation.event_type)
    }
    for request in requests:
        assert request.headers["Authorization"] == "Bearer workload-token"
        assert request.headers["X-Cosmaneura-Interaction-Ref"] == turn.interaction_ref
        assert (
            request.headers["X-Cosmaneura-Mount-Context"]
            == "mount_context_token_123456"
        )
        assert b"safe result" not in request.content


def test_writer_does_not_retry_a_conflict_or_expose_body():
    turn = _turn()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(409, json={"detail": "sentinel-secret"})

    async def token() -> str:
        return "workload-token"

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = PlatformAuditClient(
                platform_base_url="https://platform.example",
                workload_token_provider=token,
                client=http_client,
            )
            await client.write(
                turn=turn,
                observation=turn.observation(
                    event_type="sovereign.turn.refused",
                    status="refused",
                    route="lab_review",
                    intent="lab_review",
                    content=None,
                ),
            )

    with pytest.raises(AuditDeliveryError) as caught:
        _run(scenario())
    assert str(caught.value) == "platform audit conflict"
    assert caught.value.category == "conflict"
    assert caught.value.retryable is False
    assert "sentinel" not in str(caught.value)
    assert calls == 1


@pytest.mark.parametrize(
    "interaction_ref,trace_id,mount_context",
    [
        ("short", "trace_identifier_123456", None),
        ("interaction_ref_123456", "short", None),
        ("interaction_ref_123456", "trace_identifier_123456", "short"),
    ],
)
def test_turn_rejects_invalid_transport_values(
    interaction_ref: str,
    trace_id: str,
    mount_context: str | None,
):
    with pytest.raises(ValueError):
        AuditTurn(
            interaction_ref=interaction_ref,
            client_turn_id=uuid.uuid4(),
            trace_id=trace_id,
            observed_at=datetime.now(UTC),
            conversation_turn_ref="conversation_turn_123456",
            mount_context_token=mount_context,
        )
