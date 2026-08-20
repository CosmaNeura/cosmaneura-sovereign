from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from app.clients.platform_conversation import (
    ConversationDeliveryError,
    PlatformConversationClient,
    PlatformTurn,
)


TURN_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _turn() -> PlatformTurn:
    return PlatformTurn(
        interaction_ref="interaction_ref_123456",
        client_turn_id=TURN_ID,
        trace_id="trace_identifier_123456",
        mount_context_token="mount_context_token_123456",
    )


def test_operation_keys_are_stable_separate_and_not_raw_turn_uuid() -> None:
    turn = _turn()
    accepted = turn.idempotency_key("user_accepted")
    final = turn.idempotency_key("final_response")

    assert accepted == turn.idempotency_key("user_accepted")
    assert accepted != final
    assert str(TURN_ID) not in accepted
    assert 16 <= len(accepted) <= 128


def test_conversation_writer_retries_only_retryable_with_exact_request() -> None:
    turn = _turn()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                500,
                headers={"Retry-After": "0"},
                json={"retryable": True},
            )
        return httpx.Response(
            201,
            headers={
                "X-Trace-ID": turn.trace_id,
                "X-Cosmaneura-Client-Turn-ID": str(turn.client_turn_id),
                "Idempotency-Replayed": "false",
            },
            json={
                "client_turn_id": str(turn.client_turn_id),
                "turn_ref": "conversation_turn_123456",
                "phase": "user_accepted",
                "state": "accepted",
                "recorded_at": datetime(2026, 8, 19, tzinfo=UTC).isoformat(),
            },
        )

    async def scenario():
        async def token() -> str:
            return "workload-token"

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = PlatformConversationClient(
                platform_base_url="https://platform.example",
                workload_token_provider=token,
                client=http_client,
            )
            return await client.write_user_accepted(
                turn=turn,
                message="synthetic prompt sentinel",
            )

    receipt = asyncio.run(scenario())
    assert receipt.turn_ref == "conversation_turn_123456"
    assert len(requests) == 2
    assert requests[0].content == requests[1].content
    assert dict(requests[0].headers) == dict(requests[1].headers)
    assert requests[0].headers["authorization"] == "Bearer workload-token"
    assert requests[0].headers["x-cosmaneura-mount-context"] == (
        "mount_context_token_123456"
    )
    body = json.loads(requests[0].content)
    assert body == {
        "phase": "user_accepted",
        "ingress_kind": "chat_text",
        "action_code": None,
        "message": {
            "content_type": "text/plain",
            "content": "synthetic prompt sentinel",
        },
    }


@pytest.mark.parametrize(
    "status,expected_category",
    [(401, "auth"), (409, "conflict"), (422, "schema"), (500, "unavailable")],
)
def test_conversation_failure_categories_are_value_free(
    status: int,
    expected_category: str,
) -> None:
    sentinel = "token-or-phi-sentinel"

    async def scenario():
        async def token() -> str:
            return "workload-token"

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(status, json={"detail": sentinel})
            )
        ) as http_client:
            client = PlatformConversationClient(
                platform_base_url="https://platform.example",
                workload_token_provider=token,
                client=http_client,
            )
            await client.write_user_accepted(turn=_turn(), message="safe")

    with pytest.raises(ConversationDeliveryError) as caught:
        asyncio.run(scenario())
    assert caught.value.category == expected_category
    assert caught.value.retryable is False
    assert sentinel not in str(caught.value)
