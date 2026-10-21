"""Total provider deadline, exact closure and known admission without model retry."""
from __future__ import annotations

import asyncio
import json
from time import monotonic
from unittest.mock import AsyncMock

import httpx
import pytest

import app.main as main_module
import app.sovereign.orchestrator as orchestrator
from app.clients.llm import LLMClient
from app.clients.platform_conversation import ConversationDeliveryError, PlatformConversationClient
from app.clients.platform_usage import (
    ProviderUsageFacts, activate_usage_context, begin_model_call,
    deactivate_usage_context, finish_model_call, settle_pending_usage,
    successful_usage_refs, usage_reporting_complete,
)
from app.config import Persona
from app.schemas import ChatRequest
from app.security.mount import MountResolutionError
from app.sovereign.router import Router
from app.sovereign.turn_deadline import ACTIVE_PROVIDER_TURN
from tests.test_provider_error_recovery import usage_sink
from tests.test_provider_question_context import TURN_ID, sovereign, turn


def budget(monkeypatch, work=0.05, total=0.15):
    monkeypatch.setattr(orchestrator, "PROVIDER_WORK_SECONDS", work)
    monkeypatch.setattr(orchestrator, "PROVIDER_TOTAL_SECONDS", total)


async def handle(service, message="Explain the synthetic chart."):
    return await service.handle(ChatRequest(persona=Persona.PROVIDER, message=message),
        mount_context_token=turn().mount_context_token, client_turn_id=TURN_ID)


@pytest.mark.parametrize("stage", ["resolve", "acceptance", "history", "provider_context"])
def test_timeout_before_dependency_ack_never_invents_final_or_retries_user(monkeypatch, stage):
    budget(monkeypatch)
    service, context, conversation = sovereign()
    async def slow(*args, **kwargs):
        await asyncio.sleep(2)
    if stage == "resolve":
        service.mount_resolver.resolve = AsyncMock(side_effect=slow)
    elif stage == "acceptance":
        conversation.write_user_accepted.side_effect = slow
    elif stage == "history":
        service.conversation_read.read_context.side_effect = slow
    else:
        context.read.side_effect = slow
    started = monotonic()
    with pytest.raises(MountResolutionError) as error:
        asyncio.run(handle(service))
    assert error.value.status_code == 503 and not error.value.retryable
    assert monotonic() - started < 0.4
    assert conversation.write_user_accepted.call_count == (0 if stage == "resolve" else 1)
    conversation.write_final_response.assert_not_called()
    service._write_platform_observation.assert_not_called()
    if stage != "provider_context":
        service.llm.complete.assert_not_called()
    assert ACTIVE_PROVIDER_TURN.get() is None


def test_deadline_includes_setup_and_cancels_real_llm_call_with_truthful_usage(monkeypatch):
    budget(monkeypatch, work=0.08, total=0.25)
    service, _, conversation = sovereign()
    resolve = service.mount_resolver.resolve
    async def setup(request):
        await asyncio.sleep(0.04)
        return await resolve(request)
    service.mount_resolver.resolve = setup
    service.llm = LLMClient(service.s)
    cancelled = []
    async def blocked(**kwargs):
        try:
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
    service.llm._complete_unreported = AsyncMock(side_effect=blocked)
    service.router = Router(service.s, llm=service.llm)
    service.platform_usage, records = usage_sink()
    started = monotonic()
    response = asyncio.run(handle(service))
    assert monotonic() - started < 0.3
    assert cancelled == [True]
    assert service.llm._complete_unreported.call_count == 1
    assert len(records) == 1 and records[0].status == "cancelled"
    assert records[0].usage_availability == "unavailable"
    assert records[0].token_usage.output_tokens is None
    assert response.safety_notice == "upstream_unavailable"
    assert response.citations == [] and response.structured == {} and response.doctrinal_review is None
    assert conversation.write_user_accepted.call_count == 1
    final = conversation.write_final_response.call_args.kwargs
    assert final["outcome"] == "unavailable"
    assert final["history_context_ref"] == "history_context_fictional"
    assert final["answer"] == response.answer
    terminal = service._write_platform_observation.call_args.kwargs
    assert terminal["event_type"] == "sovereign.turn.failed"
    assert len(terminal["platform_refs"]) == 1


def test_hard_deadline_bounds_cancellation_usage_settlement_and_does_not_forge_closure(monkeypatch):
    budget(monkeypatch, work=0.04, total=0.10)
    service, _, conversation = sovereign()
    service.llm = LLMClient(service.s)
    async def blocked(**kwargs):
        await asyncio.sleep(2)
    service.llm._complete_unreported = AsyncMock(side_effect=blocked)
    service.router = Router(service.s, llm=service.llm)
    pending = []
    async def report(**kwargs):
        pending.append(kwargs["request"])
        await asyncio.sleep(2)
    service.platform_usage.report.side_effect = report
    started = monotonic()
    with pytest.raises(MountResolutionError) as error:
        asyncio.run(handle(service))
    assert error.value.status_code == 503 and not error.value.retryable
    assert monotonic() - started < 0.3
    assert len(pending) == 1 and pending[0].status == "cancelled"
    conversation.write_final_response.assert_not_called()
    service._write_platform_observation.assert_not_called()
    assert ACTIVE_PROVIDER_TURN.get() is None


def test_ambiguous_final_retries_exact_payload_and_never_substitutes_timeout_answer(monkeypatch):
    budget(monkeypatch)
    service, _, conversation = sovereign()
    delivered = []
    async def final(**kwargs):
        delivered.append(kwargs)
        if len(delivered) == 1:
            await asyncio.sleep(2)
        from dataclasses import replace
        return replace(conversation.write_user_accepted.return_value, phase="final_response", state="completed")
    conversation.write_final_response.side_effect = final
    with pytest.raises(MountResolutionError) as error:
        asyncio.run(handle(service))
    assert error.value.status_code == 503 and not error.value.retryable
    assert len(delivered) == 2 and delivered[0] == delivered[1]
    assert delivered[0]["outcome"] == "completed"
    assert "time limit" not in delivered[1]["answer"]
    assert conversation.write_user_accepted.call_count == 1
    service._write_platform_observation.assert_not_called()


def test_external_cancellation_still_propagates_and_emergency_precedes_authority(monkeypatch):
    budget(monkeypatch)
    service, _, conversation = sovereign()
    service.router.route.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(handle(service))
    conversation.write_final_response.assert_not_called()
    service.mount_resolver.resolve = AsyncMock(side_effect=AssertionError("Must not resolve"))
    response = asyncio.run(handle(service, "I am about to kill myself right now"))
    assert response.safety_notice == "emergency"
    service.mount_resolver.resolve.assert_not_called()


def test_cancelled_usage_delivery_reuses_exact_success_facts_on_settlement():
    requests = []
    class Writer:
        async def report(self, *, turn, request):
            from app.clients.platform_usage import UsageWriteReceipt
            from datetime import UTC, datetime
            requests.append(request)
            if len(requests) == 1:
                raise asyncio.CancelledError()
            return UsageWriteReceipt(record_ref="usage_synthetic_confirmed", recorded_at=datetime.now(UTC))
    async def scenario():
        activate_usage_context(turn=turn(), client=Writer())
        try:
            span = begin_model_call(call_kind="drafting", provider="azure_openai",
                model="synthetic", deployment=None, purpose="response_drafting")
            with pytest.raises(asyncio.CancelledError):
                await finish_model_call(span, status="succeeded",
                    facts=ProviderUsageFacts(output_tokens=7, usage_source="provider_response"))
            assert not usage_reporting_complete()
            await settle_pending_usage()
            assert usage_reporting_complete()
            assert successful_usage_refs() == ("usage_synthetic_confirmed",)
        finally:
            deactivate_usage_context()
    asyncio.run(scenario())
    assert requests[0] is requests[1]
    assert requests[1].status == "succeeded" and requests[1].token_usage.output_tokens == 7


@pytest.mark.parametrize("invalid", [None, True, 0, 61, "5"])
def test_known_core_rate_response_is_validated_without_hidden_retry(invalid):
    requests = []
    def response(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After":"5"}, json={
            "code":"RATE_LIMITED", "status":429, "retryable":True,
            "retry_after_seconds":invalid, "detail":"UNSAFE_UPSTREAM_SENTINEL",
        })
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
            client = PlatformConversationClient(platform_base_url="https://platform.example",
                workload_token_provider=AsyncMock(return_value="synthetic-workload"), client=http)
            await client.write_user_accepted(turn=turn(), message="Synthetic question")
    with pytest.raises(ConversationDeliveryError) as error:
        asyncio.run(scenario())
    assert error.value.category == "schema"
    assert len(requests) == 1 and "UNSAFE_UPSTREAM_SENTINEL" not in str(error.value)


def test_known_admission_maps_to_existing_public_429_and_retry_after_without_models():
    service, _, conversation = sovereign()
    conversation.write_user_accepted.side_effect = ConversationDeliveryError(
        "rate_limited", retryable=True, retry_after_seconds=5)
    async def scenario():
        with pytest.raises(MountResolutionError) as error:
            await handle(service)
        return await main_module._mount_resolution_error(None, error.value)
    response = asyncio.run(scenario())
    body = json.loads(response.body)
    assert response.status_code == 429 and body["code"] == "RATE_LIMITED"
    assert body["retryable"] is True and body["retry_after_seconds"] == 5
    assert response.headers["Retry-After"] == "5"
    assert "answer" not in body
    service.router.route.assert_not_called()
    conversation.write_final_response.assert_not_called()


def test_valid_core_rate_response_causes_one_write_and_no_model_work():
    requests = []
    def response(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After":"5"}, json={
            "code":"RATE_LIMITED", "status":429, "retryable":True,
            "retry_after_seconds":5, "detail":"UNSAFE_UPSTREAM_SENTINEL",
        })
    async def scenario():
        service, _, _ = sovereign()
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
            service.platform_conversation = PlatformConversationClient(
                platform_base_url="https://platform.example",
                workload_token_provider=AsyncMock(return_value="synthetic-workload"), client=http)
            with pytest.raises(MountResolutionError) as error:
                await handle(service)
            assert error.value.status_code == 429 and error.value.retry_after_seconds == 5
            assert "UNSAFE_UPSTREAM_SENTINEL" not in str(error.value)
            service.router.route.assert_not_called()
    asyncio.run(scenario())
    assert len(requests) == 1


def test_concurrent_turn_deadline_state_never_crosses_requests(monkeypatch):
    from dataclasses import replace
    from uuid import uuid4
    from app.schemas import DoctrinalSensitivity, Intent
    budget(monkeypatch)
    service, _, conversation = sovereign(needs_context=False)
    original = service.mount_resolver.resolve
    async def resolve(request):
        result = await original(request)
        return replace(result, interaction_ref="interaction_" + request.client_turn_id.hex)
    service.mount_resolver.resolve = resolve
    async def accepted(*, turn, message):
        return replace(conversation.write_user_accepted.return_value, client_turn_id=turn.client_turn_id)
    conversation.write_user_accepted.side_effect = accepted
    async def classify(request):
        if request.message == "Slow synthetic question":
            await asyncio.sleep(2)
        return Intent.ENGAGEMENT, DoctrinalSensitivity.LOW, ""
    service.router.route.side_effect = classify
    fast, slow = uuid4(), uuid4()
    async def scenario():
        return await asyncio.gather(*[
            service.handle(ChatRequest(persona=Persona.PROVIDER, message=message),
                mount_context_token=turn().mount_context_token, client_turn_id=identifier)
            for identifier, message in [(fast,"Fast synthetic question"),(slow,"Slow synthetic question")]
        ])
    results = asyncio.run(scenario())
    assert results[0].safety_notice != "upstream_unavailable"
    assert results[1].safety_notice == "upstream_unavailable"
    finals = {call.kwargs["turn"].client_turn_id: call.kwargs for call in conversation.write_final_response.call_args_list}
    assert set(finals) == {fast,slow}
    assert finals[fast]["outcome"] == "completed"
    assert finals[slow]["outcome"] == "unavailable"
    assert ACTIVE_PROVIDER_TURN.get() is None
