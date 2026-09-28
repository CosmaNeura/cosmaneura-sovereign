"""Transport failures cannot leak an unreviewed draft or strand a handled turn."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

import app.main as main_module
from app.clients.llm import LLMClient
from app.clients.magisterium import MagisteriumClient
from app.clients.platform_conversation import ConversationDeliveryError
from app.clients.platform_usage import (
    UsageWriteReceipt, activate_usage_context, deactivate_usage_context,
)
from app.clients.pubmed import PubMedClient
from app.compliance.netsec import UpstreamCallError
from app.config import Persona, Settings
from app.schemas import ChatRequest, DoctrinalSensitivity, Intent
from app.sovereign.arbiter import DoctrinalArbiter
from app.sovereign.router import Router
from tests.test_conversation_read import memory
from tests.test_provider_question_context import TURN_ID, sovereign, turn

ORIGIN = "https://synthetic.example.invalid"
DRAFT = "# UNREVIEWED_DRAFT_SENTINEL\nFictional contact synthetic@example.invalid"
REVIEW = "UNPARSED_REVIEW_SENTINEL"
RAW_ERROR = "UNSAFE_PROVIDER_ERROR_SENTINEL"


def real_settings():
    return Settings(
        _env_file=None, mock_mode=False, baa_attested=True,
        llm_provider="azure_openai", azure_openai_endpoint="https://synthetic.openai.azure.com",
        azure_openai_api_key="synthetic-key", anthropic_api_key="synthetic-key",
        anthropic_base_url="https://anthropic.example.invalid",
        magisterium_api_key="synthetic-key", magisterium_base_url="https://magisterium.example.invalid",
    )


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs,
    ))
    # The transport is synthetic; do not resolve a public provider hostname.
    monkeypatch.setattr("app.clients.magisterium.assert_safe_outbound_url", lambda _url: None)


def usage_sink():
    records = []

    async def report(*, turn, request):
        records.append(request)
        return UsageWriteReceipt(record_ref=f"usage_synthetic_{request.call_sequence}",
                                 recorded_at=datetime.now(UTC))

    return SimpleNamespace(report=AsyncMock(side_effect=report)), records


@pytest.mark.parametrize("provider", ["anthropic", "azure_openai", "magisterium"])
@pytest.mark.parametrize("failure", ["timeout", "connect", "status"])
def test_real_provider_errors_preserve_failed_usage_and_safe_optional_response(monkeypatch, provider, failure):
    def handler(request):
        if failure == "status":
            return httpx.Response(503, headers={"x-request-id": "request_synthetic"},
                                  text=RAW_ERROR, request=request)
        error = httpx.ReadTimeout if failure == "timeout" else httpx.ConnectError
        raise error(RAW_ERROR, request=request)

    transport(monkeypatch, handler)
    settings = real_settings()
    settings.llm_provider = provider if provider != "magisterium" else "azure_openai"
    usage, records = usage_sink()

    async def run():
        activate_usage_context(turn=turn(), client=usage)
        try:
            with pytest.raises(UpstreamCallError) as caught:
                if provider == "magisterium":
                    await MagisteriumClient(settings).ask(system="Synthetic", user_content="Fictional ethics")
                else:
                    await LLMClient(settings).complete(system="Synthetic", prompt="Fictional question")
            assert RAW_ERROR not in str(caught.value)
            assert "AttributeError" not in str(caught.value)
            assert type(caught.value).__name__ == "UpstreamCallError"
        finally:
            deactivate_usage_context()

    asyncio.run(run())
    assert len(records) == 1 and records[0].status == "failed"
    assert records[0].usage_availability == "unavailable"
    assert records[0].provider_request_id == ("request_synthetic" if failure == "status" else None)
    assert RAW_ERROR not in records[0].model_dump_json()


@pytest.mark.parametrize("provider", ["anthropic", "azure_openai", "magisterium"])
def test_provider_cancellation_is_accounted_and_not_reclassified(monkeypatch, provider):
    def handler(request):
        raise asyncio.CancelledError()

    transport(monkeypatch, handler)
    settings = real_settings()
    settings.llm_provider = provider if provider != "magisterium" else "azure_openai"
    usage, records = usage_sink()

    async def run():
        activate_usage_context(turn=turn(), client=usage)
        try:
            with pytest.raises(asyncio.CancelledError):
                if provider == "magisterium":
                    await MagisteriumClient(settings).ask(system="Synthetic", user_content="Fictional ethics")
                else:
                    await LLMClient(settings).complete(system="Synthetic", prompt="Fictional question")
        finally:
            deactivate_usage_context()

    asyncio.run(run())
    assert [record.status for record in records] == ["cancelled"]


def test_pubmed_timeout_is_typed_and_value_free(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout(RAW_ERROR, request=request)

    transport(monkeypatch, handler)
    with pytest.raises(UpstreamCallError) as caught:
        asyncio.run(PubMedClient(real_settings()).search("clinical ethics"))
    assert RAW_ERROR not in str(caught.value)


def ethics_service(monkeypatch, failure):
    service, context, conversation = sovereign(needs_context=True)
    for key in ("mock_mode", "baa_attested", "llm_provider", "azure_openai_endpoint",
                "azure_openai_api_key", "magisterium_api_key", "magisterium_base_url"):
        setattr(service.s, key, getattr(real_settings(), key))
    service.llm = LLMClient(service.s)
    service.magisterium = MagisteriumClient(service.s)
    service.arbiter = DoctrinalArbiter(service.s, llm=service.llm, magisterium=service.magisterium)
    service.router.route = AsyncMock(return_value=(Intent.ETHICS_CONSULT, DoctrinalSensitivity.HIGH, ""))
    if failure == "route":
        service.router = Router(service.s, llm=service.llm)
    elif failure == "selector":
        service.router.needs_provider_context = Router(service.s, llm=service.llm).needs_provider_context
    service.conversation_read.read_context.return_value = memory()
    agent = service.agents[Intent.ETHICS_CONSULT]
    agent.llm = service.llm
    agent.pubmed.search = AsyncMock(return_value=[])
    service.platform_usage, usage = usage_sink()
    requests = []

    def handler(request):
        body = json.loads(request.content)
        prompt = body["messages"][-1]["content"]
        if request.url.host == "magisterium.example.invalid":
            stage = "arbitration"
            assert "synthetic@example.invalid" not in prompt
            assert "[REDACTED:EMAIL]" in prompt
        elif "Reviewer assessment:" in prompt:
            stage = "verdict"
        elif "Fields: topic" in prompt:
            stage = "topic"
        elif "Produce the structured consult prep." in prompt:
            stage = "draft"
        else:
            stage = failure  # The deliberately exercised route/selector call.
        requests.append(stage)
        if stage == failure or (failure == "cancel" and stage == "arbitration"):
            if failure == "cancel":
                raise asyncio.CancelledError()
            raise httpx.ReadTimeout(RAW_ERROR, request=request)
        text = {"topic": '{"topic":"clinical ethics"}', "draft": DRAFT,
                "arbitration": REVIEW, "verdict": '{"conforms":true}'}[stage]
        return httpx.Response(200, json={
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 4},
            "citations": [{"title": "Unreviewed citation", "url": "https://example.invalid/source"}],
        })

    transport(monkeypatch, handler)
    monkeypatch.setattr(main_module, "_settings", service.s)
    monkeypatch.setattr(main_module, "_sovereign", service)
    # Actual registered routes/handlers/security dispatch, with the production
    # middleware order and one fictional allowed origin; no app reload/hooks.
    app = FastAPI(routes=main_module.app.routes, exception_handlers=main_module.app.exception_handlers,
                  middleware=[Middleware(BaseHTTPMiddleware, dispatch=main_module._security_middleware),
                              Middleware(CORSMiddleware, allow_origins=[ORIGIN],
                                         allow_credentials=True,
                                         allow_methods=main_module._CORS_ALLOW_METHODS,
                                         allow_headers=main_module._CORS_ALLOW_HEADERS,
                                         expose_headers=main_module._CORS_EXPOSE_HEADERS)])
    return service, context, conversation, usage, requests, TestClient(app, raise_server_exceptions=False)


def post(client):
    return client.post("/chat", headers={
        "Authorization": "Bearer mount_synthetic_001", "Origin": ORIGIN,
        "X-Cosmaneura-Embed-Version": "1.0", "X-Cosmaneura-Client-Turn-ID": str(TURN_ID),
    }, json={"client_turn_id": str(TURN_ID), "message": "Prepare a fictional ERD ethics consult.",
             "metadata": {"output_format": "document"}})


@pytest.mark.parametrize("stage", ["route", "selector", "topic", "draft", "arbitration", "verdict"])
def test_actual_chat_failure_records_unavailable_with_dependencies_and_no_unreviewed_egress(monkeypatch, stage):
    service, context, conversation, usage, requests, client = ethics_service(monkeypatch, stage)
    response = post(client)
    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["x-cosmaneura-embed-version"] == "1.0"
    body = response.json()
    assert body["safety_notice"] == "upstream_unavailable"
    assert "No reviewed answer or document is available" in body["answer"]
    assert body["citations"] == [] and body["structured"] == {} and body["doctrinal_review"] is None
    for sentinel in ["UNREVIEWED_DRAFT_SENTINEL", REVIEW, RAW_ERROR, "Unreviewed citation"]:
        assert sentinel not in response.text
    assert "intent_badge" in body["presentation"]["suppress"]
    assert requests[-1] == stage and usage[-1].status == "failed"
    assert len(usage) == len(requests)
    final = conversation.write_final_response.call_args.kwargs
    assert final["answer"] == body["answer"] and final["presentation"] == body["presentation"]
    assert final["outcome"] == "unavailable" and final["history_context_ref"] == memory().tool_call_ref
    if stage in {"route", "selector"}:
        assert "provider_context_ref" not in final
    else:
        assert final["provider_context_ref"] == context.read.return_value.tool_call_ref
    assert conversation.write_final_response.call_count == 1
    terminal = service._write_platform_observation.call_args.kwargs
    assert terminal["status"] == "failed" and terminal["event_type"] == "sovereign.turn.failed"
    assert terminal["intent"] == (None if stage == "route" else "ethics_consult")
    assert len(terminal["platform_refs"]) == len(usage)
    if stage in {"arbitration", "verdict"}:
        assert "draft" in requests  # A real completed draft was withheld.


@pytest.mark.parametrize("category,status", [("auth", 503), ("conflict", 409), ("unavailable", 503)])
def test_unavailable_final_write_refusal_is_closed_and_not_retryable(monkeypatch, category, status):
    service, context, conversation, usage, requests, client = ethics_service(monkeypatch, "arbitration")
    conversation.write_final_response.side_effect = ConversationDeliveryError(category, retryable=True)
    response = post(client)
    assert response.status_code == status, response.text
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["x-cosmaneura-embed-version"] == "1.0"
    assert response.json()["retryable"] is False
    assert "answer" not in response.json() and "safety_fallback" not in response.json()
    assert "UNREVIEWED_DRAFT_SENTINEL" not in response.text and RAW_ERROR not in response.text
    assert conversation.write_final_response.call_count == 1
    assert conversation.write_final_response.call_args.kwargs["provider_context_ref"] == context.read.return_value.tool_call_ref
    service._write_platform_observation.assert_not_called()
    assert usage[-1].status == "failed"


def test_accepted_uuid_replay_never_redrafts_or_overwrites_unavailable(monkeypatch):
    service, _, conversation, usage, requests, client = ethics_service(monkeypatch, "arbitration")
    assert post(client).status_code == 200
    count = len(requests)
    conversation.write_user_accepted.return_value = replace(
        conversation.write_user_accepted.return_value, replayed=True,
    )
    response = post(client)
    assert response.status_code == 503 and response.json()["retryable"] is False
    assert len(requests) == count and len(usage) == count
    assert conversation.write_final_response.call_count == 1


def test_arbitration_cancellation_never_becomes_an_unavailable_final(monkeypatch):
    service, _, conversation, usage, requests, _ = ethics_service(monkeypatch, "cancel")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.handle(
            ChatRequest(persona=Persona.PROVIDER, client_turn_id=TURN_ID,
                        message="Prepare a fictional ERD ethics consult.", metadata={"output_format": "document"}),
            mount_context_token="mount_synthetic_001", client_turn_id=TURN_ID,
        ))
    assert requests[-1] == "arbitration" and usage[-1].status == "cancelled"
    conversation.write_final_response.assert_not_called()
    service._write_platform_observation.assert_not_called()
