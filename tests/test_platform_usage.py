from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import httpx

from app.clients.platform_conversation import PlatformTurn
from app.clients.platform_usage import (
    ModelUsageRequest,
    PlatformUsageClient,
    ProviderUsageFacts,
    TokenUsage,
    UsageWriteReceipt,
    activate_usage_context,
    begin_model_call,
    deactivate_usage_context,
    finish_model_call,
    successful_usage_refs,
    usage_reporting_complete,
)


TURN_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


def _turn() -> PlatformTurn:
    return PlatformTurn(
        interaction_ref="interaction_ref_123456",
        client_turn_id=TURN_ID,
        trace_id="trace_identifier_123456",
        mount_context_token="mount_context_token_123456",
    )


def test_context_sequences_calls_and_returns_ordered_receipts() -> None:
    class Writer:
        def __init__(self):
            self.requests = []

        async def report(self, *, turn, request):
            self.requests.append(request)
            return UsageWriteReceipt(
                record_ref=f"model_usage_{request.call_sequence:06d}",
                recorded_at=datetime(2026, 8, 19, tzinfo=UTC),
            )

    async def scenario():
        writer = Writer()
        activate_usage_context(turn=_turn(), client=writer)  # type: ignore[arg-type]
        first = begin_model_call(
            call_kind="routing",
            provider="azure_openai",
            model="gpt-5",
            deployment="deployment-a",
            purpose="intent_routing",
        )
        second = begin_model_call(
            call_kind="doctrinal_arbitration",
            provider="magisterium",
            model="magisterium-1",
            deployment=None,
            purpose="doctrinal_review",
        )
        await finish_model_call(
            second,
            status="succeeded",
            facts=ProviderUsageFacts(output_tokens=7, usage_source="provider_response"),
        )
        await finish_model_call(first, status="failed")
        result = (
            writer.requests,
            successful_usage_refs(),
            usage_reporting_complete(),
        )
        deactivate_usage_context()
        return result

    requests, refs, complete = asyncio.run(scenario())
    assert [request.call_sequence for request in requests] == [2, 1]
    assert refs == ("model_usage_000001", "model_usage_000002")
    assert complete is True
    assert requests[0].usage_availability == "partial"
    assert requests[1].usage_availability == "unavailable"
    assert all(request.started_at is not None for request in requests)


def test_failed_usage_delivery_leaves_terminal_closure_incomplete() -> None:
    class FailingWriter:
        async def report(self, **kwargs):
            from app.clients.platform_usage import UsageDeliveryError

            raise UsageDeliveryError("unavailable", retryable=True)

    async def scenario():
        activate_usage_context(
            turn=_turn(),
            client=FailingWriter(),  # type: ignore[arg-type]
        )
        span = begin_model_call(
            call_kind="drafting",
            provider="anthropic",
            model="claude-test",
            deployment=None,
            purpose="response_drafting",
        )
        await finish_model_call(span, status="succeeded")
        result = successful_usage_refs(), usage_reporting_complete()
        deactivate_usage_context()
        return result

    refs, complete = asyncio.run(scenario())
    assert refs == ()
    assert complete is False


def test_usage_writer_uses_stable_sequence_domain_and_closed_payload() -> None:
    requests: list[httpx.Request] = []
    turn = _turn()
    request = ModelUsageRequest(
        client_event_id=uuid.UUID("33333333-3333-4333-8333-333333333333"),
        call_kind="drafting",
        call_sequence=1,
        workstream="sovereign",
        provider="azure_openai",
        provider_request_id="provider-request-1",
        model="gpt-5",
        deployment="deployment-a",
        purpose="response_drafting",
        started_at=datetime(2026, 8, 19, tzinfo=UTC),
        completed_at=datetime(2026, 8, 19, tzinfo=UTC),
        latency_ms=10,
        status="succeeded",
        usage_availability="partial",
        usage_source="provider_response",
        token_usage=TokenUsage(
            input_tokens=12,
            output_tokens=4,
            cached_input_tokens=None,
            reasoning_tokens=None,
        ),
    )

    def handler(http_request: httpx.Request) -> httpx.Response:
        requests.append(http_request)
        return httpx.Response(
            201,
            headers={
                "X-Trace-ID": turn.trace_id,
                "Idempotency-Replayed": "false",
            },
            json={
                "record_ref": "model_usage_123456",
                "recorded_at": datetime(2026, 8, 19, tzinfo=UTC).isoformat(),
            },
        )

    async def scenario():
        async def token() -> str:
            return "workload-token"

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = PlatformUsageClient(
                platform_base_url="https://platform.example",
                workload_token_provider=token,
                client=http_client,
            )
            return await client.report(turn=turn, request=request)

    receipt = asyncio.run(scenario())
    assert receipt.record_ref == "model_usage_123456"
    assert len(requests) == 1
    sent = requests[0]
    assert sent.url.path == "/service/v1/agent/model-usage-events/report"
    assert str(TURN_ID) not in sent.headers["idempotency-key"]
    assert sent.headers["x-cosmaneura-mount-context"] == (
        "mount_context_token_123456"
    )
    body = json.loads(sent.content)
    assert set(body) == {
        "client_event_id",
        "call_kind",
        "call_sequence",
        "workstream",
        "provider",
        "provider_request_id",
        "model",
        "deployment",
        "purpose",
        "started_at",
        "completed_at",
        "latency_ms",
        "status",
        "usage_availability",
        "usage_source",
        "token_usage",
    }
    assert "prompt" not in str(body).lower()
    assert "response" not in body
