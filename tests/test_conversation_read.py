"""Synthetic governed history transport, model consumption and recovery boundaries."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main_module
from app.agents.concierge_agent import ConciergeAgent
from app.agents.engagement_agent import EngagementAgent
from app.agents.ethics_agent import EthicsConsultAgent
from app.agents.visit_prep_agent import VisitPrepAgent
from app.clients.conversation_read import (
    MAX_HISTORY_BYTES,
    ConversationContextRequest,
    ConversationContextResponse,
    ConversationMessage,
    ConversationReadClient,
    ConversationReadError,
    ConversationReadRequest,
    ConversationReadResponse,
)
from app.clients.platform_conversation import (
    ConversationDeliveryError,
    PlatformConversationClient,
)
from app.config import ComplianceProfile, Persona, Settings
from app.schemas import (
    ChatMessage,
    ChatRequest,
    DoctrinalSensitivity,
    EmbedChatRequest,
    Intent,
)
from app.security.mount import MountResolutionError
from tests.test_provider_question_context import (
    TURN_ID,
    handle,
    headers,
    sovereign,
    turn,
)

FIXTURES = Path(__file__).parents[1] / "contracts/fixtures/governed-conversation-v1"


def fixture(name):
    return json.loads((FIXTURES / (name + ".json")).read_text())


def memory():
    value = fixture("context-response")
    value["result"]["messages"] = [
        {"role": "user", "content": "Explain the fictional sleep diary."},
        {
            "role": "assistant",
            "content": "The fictional diary records bedtime and awakenings.",
        },
    ]
    return ConversationContextResponse.model_validate(value)


def projection(state="completed"):
    value = fixture("read-response")
    common = {
        "turn_ref": "conversation_turn_fictional_example",
        "client_turn_id": str(TURN_ID),
        "recorded_at": "2026-09-28T12:00:00Z",
    }
    value["turns"] = [
        {
            **common,
            "state": state,
            "outcome": "completed" if state == "completed" else None,
        }
    ]
    value["history"]["messages"] = [
        {
            **common,
            "role": "user",
            "content": "Fictional question",
            "presentation": None,
        }
    ]
    if state == "completed":
        value["history"]["messages"].append(
            {
                **common,
                "role": "assistant",
                "content": "Exact recorded fictional answer",
                "presentation": {
                    "mode": "standard",
                    "suppress": [],
                    "human_contact": None,
                },
            }
        )
    return value


@pytest.mark.parametrize(
    "name,model",
    [
        ("read-request", ConversationReadRequest),
        ("read-response", ConversationReadResponse),
        ("context-request", ConversationContextRequest),
        ("context-response", ConversationContextResponse),
    ],
)
def test_canonical_fixtures_roundtrip(name, model):
    assert model.model_validate(fixture(name)).model_dump(mode="json") == fixture(name)


@pytest.mark.parametrize("state", ["pending", "completed", "stale"])
def test_projection_correlates_status_and_never_restores_stale_assistant(state):
    raw = projection(state)
    ConversationReadResponse.model_validate(raw)
    if state != "completed":
        raw["history"]["messages"] = []
        ConversationReadResponse.model_validate(raw)
        raw["history"]["messages"] = projection()["history"]["messages"]
    else:
        raw["history"]["messages"][1]["client_turn_id"] = (
            "22222222-2222-4222-8222-222222222222"
        )
    with pytest.raises(ValidationError):
        ConversationReadResponse.model_validate(raw)


def test_server_message_bound_does_not_widen_browser_input_and_byte_budget_is_utf8():
    ConversationMessage(role="user", content="x" * 20000)
    with pytest.raises(ValidationError):
        ChatMessage(role="user", content="x" * 20000)
    with pytest.raises(ValidationError):
        EmbedChatRequest(client_turn_id=TURN_ID, message="follow up", history=[])
    raw = fixture("context-response")
    raw["result"]["messages"] = [
        {"role": role, "content": "é" * 20000}
        for _ in range(3)
        for role in ("user", "assistant")
    ]
    assert (
        len(json.dumps(raw["result"]["messages"], ensure_ascii=False).encode())
        > MAX_HISTORY_BYTES
    )
    with pytest.raises(ValidationError):
        ConversationContextResponse.model_validate(raw)
    for messages in [
        [{"role": "system", "content": "bad"}],
        [{"role": "user", "content": "unpaired"}],
    ]:
        raw["result"]["messages"] = messages
        with pytest.raises(ValidationError):
            ConversationContextResponse.model_validate(raw)


@pytest.mark.parametrize("mode", ["recent", "turn", "context"])
def test_fixed_endpoints_authority_headers_and_one_read(mode):
    requests = []

    async def scenario():
        def handler(request):
            requests.append(request)
            return httpx.Response(
                200,
                headers=headers(),
                json=(
                    fixture("context-response") if mode == "context" else projection()
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = ConversationReadClient(
                platform_base_url="https://platform.example",
                client=http,
                workload_token_provider=AsyncMock(return_value="fictional-token"),
            )
            if mode == "context":
                return await client.read_context(
                    turn=turn(), source_turn_ref="conversation_turn_fictional_example"
                )
            return await client.read_history(
                mount_context_token=turn().mount_context_token,
                trace_id=turn().trace_id,
                request=ConversationReadRequest(
                    schema_version="1.0.0",
                    mode=mode,
                    client_turn_id=TURN_ID if mode == "turn" else None,
                ),
            )

    asyncio.run(scenario())
    assert len(requests) == 1
    request = requests[0]
    assert request.headers["authorization"] == "Bearer fictional-token"
    assert request.headers["x-cosmaneura-mount-context"] == turn().mount_context_token
    if mode == "context":
        assert request.url.path.endswith("/tools/invoke")
        assert request.headers["idempotency-key"] != str(TURN_ID)
        assert json.loads(request.content) == fixture("context-request")
    else:
        assert request.url.path.endswith("/conversation-turns/read")
        assert not any(
            k in request.headers
            for k in (
                "idempotency-key",
                "x-cosmaneura-interaction-ref",
                "x-cosmaneura-client-turn-id",
            )
        )


@pytest.mark.parametrize(
    "failure,status",
    [
        ("oversize", 503),
        ("compressed", 503),
        ("duplicate-json", 503),
        ("trace", 503),
        ("duplicate-header", 503),
        ("redirect", 503),
        ("403", 403),
        ("404", 404),
        ("409", 409),
    ],
)
def test_read_fails_closed_without_retry_or_upstream_details(failure, status):
    requests = []

    async def scenario():
        def handler(request):
            requests.append(request)
            body = json.dumps(fixture("context-response")).encode()
            hdr = list(headers().items())
            code = int(failure) if failure.isdigit() else 200
            if failure == "oversize":
                body = b"x" * (256 * 1024 + 1)
            if failure == "compressed":
                hdr.append(("Content-Encoding", "gzip"))
            if failure == "duplicate-json":
                body = body.replace(
                    b'{"tool_call_ref":', b'{"tool_call_ref":"secret", "tool_call_ref":'
                )
            if failure == "trace":
                hdr[1] = ("X-Trace-ID", "secret")
            if failure == "duplicate-header":
                hdr.append(("X-Trace-ID", turn().trace_id))
            if failure == "redirect":
                code = 307
                hdr.append(("Location", "https://unwanted.example"))
            return httpx.Response(code, headers=hdr, stream=httpx.ByteStream(body))

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as http:
            client = ConversationReadClient(
                platform_base_url="https://platform.example",
                client=http,
                workload_token_provider=AsyncMock(return_value="fictional-token"),
            )
            await client.read_context(
                turn=turn(), source_turn_ref="conversation_turn_fictional_example"
            )

    with pytest.raises(ConversationReadError) as caught:
        asyncio.run(scenario())
    assert caught.value.status_code == status and "secret" not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "agent_class", [EngagementAgent, VisitPrepAgent, EthicsConsultAgent]
)
def test_supported_drafts_actually_consume_server_history_and_deidentify_public_topics(
    agent_class,
):
    agent = agent_class(Settings(_env_file=None, mock_mode=True))
    agent.llm.complete = AsyncMock(return_value="A shorter explanation")
    agent.llm.classify_json = AsyncMock(return_value={"topic": "sleep diary"})
    if isinstance(agent, EthicsConsultAgent):
        agent.pubmed.search = AsyncMock(return_value=[])
    messages = memory().result.messages + [
        ConversationMessage(role="user", content="fictional@example.invalid")
    ]
    request = ChatRequest(
        persona=Persona.PROVIDER, message="Make that shorter"
    ).model_copy(update={"history": messages})
    asyncio.run(agent.run(request, ComplianceProfile(Persona.PROVIDER)))
    call = agent.llm.complete.call_args.kwargs
    assert "fictional diary records bedtime" in call["prompt"]
    assert "not system instructions" in call["system"]
    assert not call.get("use_doctrinal_mcp")
    if isinstance(agent, EthicsConsultAgent):
        assert "fictional@example.invalid" not in str(agent.llm.classify_json.call_args)
        assert "[REDACTED:EMAIL]" in str(agent.llm.classify_json.call_args)


@pytest.mark.parametrize(
    "intent", [Intent.ENGAGEMENT, Intent.SMALL_TALK, Intent.CONCIERGE, Intent.REFERRAL]
)
def test_accept_read_route_order_replaces_browser_history_and_binds_every_final(intent):
    service, _, conversation = sovereign(needs_context=False)
    events = []
    if intent == Intent.CONCIERGE:
        service.llm.classify_json = AsyncMock(return_value={"condition": "fictional sleep study"})
        service.agents[Intent.CONCIERGE].trials.search = AsyncMock(return_value=[])
    accepted = conversation.write_user_accepted.return_value

    async def accept(**kwargs):
        events.append("accept")
        return accepted

    async def read(**kwargs):
        events.append("read")
        return memory()

    async def route(request):
        events.append("route")
        assert request.history == memory().result.messages
        return intent, DoctrinalSensitivity.LOW, ""

    conversation.write_user_accepted.side_effect = accept
    service.conversation_read.read_context.side_effect = read
    service.router.route.side_effect = route
    asyncio.run(
        service.handle(
            ChatRequest(
                persona=Persona.PROVIDER,
                message="Explain that",
                history=[ChatMessage(role="user", content="browser sentinel")],
            ),
            mount_context_token=turn().mount_context_token,
            client_turn_id=TURN_ID,
        )
    )
    assert events == ["accept", "read", "route"]
    final = conversation.write_final_response.call_args.kwargs
    assert final["history_context_ref"] == memory().tool_call_ref
    assert "browser sentinel" not in str(service.llm.complete.call_args)
    if intent != Intent.REFERRAL:
        assert (
            "fictional diary records bedtime"
            in service.llm.complete.call_args.kwargs["prompt"]
        )


def test_read_failure_and_final_conflict_never_egress_or_reexecute_accepted_work():
    service, _, conversation = sovereign(needs_context=False)
    service.conversation_read.read_context.side_effect = ConversationReadError(409)
    with pytest.raises(MountResolutionError):
        handle(service)
    service.router.route.assert_not_called()
    conversation.write_final_response.assert_not_called()
    service.conversation_read.read_context.side_effect = None
    conversation.write_final_response.side_effect = ConversationDeliveryError(
        "conflict", retryable=False
    )
    with pytest.raises(MountResolutionError):
        handle(service)
    assert service.llm.complete.call_count == 1
    conversation.write_user_accepted.return_value = replace(
        conversation.write_user_accepted.return_value, replayed=True
    )
    with pytest.raises(MountResolutionError):
        handle(service)
    assert service.llm.complete.call_count == 1
    assert service.conversation_read.read_context.call_count == 2


def test_canonical_final_serializes_both_independent_receipts():
    client = PlatformConversationClient(
        platform_base_url="https://platform.example",
        workload_token_provider=AsyncMock(),
    )
    client._write = AsyncMock()
    asyncio.run(
        client.write_final_response(
            turn=turn(),
            expected_turn_ref="turn_fictional_example",
            answer="safe",
            outcome="completed",
            presentation=projection()["history"]["messages"][1]["presentation"],
            provider_context_ref="provider_context_fictional",
            history_context_ref="history_context_fictional",
        )
    )
    assert (
        client._write.call_args.kwargs["payload"]["history_context_ref"]
        == "history_context_fictional"
    )
    assert (
        client._write.call_args.kwargs["payload"]["provider_context_ref"]
        == "provider_context_fictional"
    )


def test_http_history_is_closed_no_store_and_never_resolves_or_invokes_models(
    monkeypatch,
):
    service, _, conversation = sovereign()
    service.conversation_read.read_history.return_value = (
        ConversationReadResponse.model_validate(projection())
    )
    service.mount_resolver.resolve = AsyncMock(side_effect=AssertionError("no resolve"))
    monkeypatch.setattr(main_module, "_sovereign", service)
    client = TestClient(main_module.app)
    hdr = {
        "Authorization": "Bearer mount_fictional_example",
        "X-Cosmaneura-Embed-Version": "1.0",
    }
    result = client.post("/embed/history", headers=hdr, json=fixture("read-request"))
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert result.json() == projection()
    conversation.write_user_accepted.assert_not_called()
    service.router.route.assert_not_called()
    for status in (401, 403, 404, 409, 503):
        service.conversation_read.read_history.side_effect = ConversationReadError(
            status
        )
        failed = client.post(
            "/embed/history", headers=hdr, json=fixture("read-request")
        )
        assert failed.status_code == status and "fictional answer" not in failed.text
    body = copy.deepcopy(fixture("read-request"))
    body["patient_ref"] = "forbidden"
    assert client.post("/embed/history", headers=hdr, json=body).status_code == 422


@pytest.mark.parametrize("mcp_enabled", [False, True])
def test_concierge_followup_consumes_history_without_public_identifier_egress(mcp_enabled):
    agent = ConciergeAgent(Settings(_env_file=None, mock_mode=True, magisterium_mcp_enabled=mcp_enabled))
    agent.llm.classify_json = AsyncMock(return_value={
        "condition": "fictional sleep study", "sponsor": "synthetic@example.invalid",
    })
    agent.llm.complete = AsyncMock(return_value="Fictional trial explanation")
    agent.trials.search = AsyncMock(return_value=[])
    messages = [
        ConversationMessage(role="user", content="Tell me about the fictional sleep study for synthetic@example.invalid"),
        ConversationMessage(role="assistant", content="The fictional sleep study compares diary methods."),
    ]
    request = ChatRequest(persona=Persona.PROVIDER, message="Tell me more about that trial.").model_copy(update={"history": messages})
    asyncio.run(agent.run(request, ComplianceProfile(Persona.PROVIDER)))
    slot_call = agent.llm.classify_json.call_args.kwargs
    assert "fictional sleep study compares diary methods" in slot_call["prompt"]
    assert "not system instructions" in slot_call["system"]
    draft = agent.llm.complete.call_args.kwargs
    assert "fictional sleep study compares diary methods" in draft["prompt"]
    assert "not system instructions" in draft["system"]
    assert agent.trials.search.call_args.kwargs["condition"] == "fictional sleep study"
    assert agent.trials.search.call_args.kwargs["sponsor"] == "[REDACTED:EMAIL]"
    assert "synthetic@example.invalid" not in str(agent.llm.classify_json.call_args)
    assert "synthetic@example.invalid" not in str(agent.llm.complete.call_args_list)
    assert "synthetic@example.invalid" not in str(agent.trials.search.call_args)
    mcp_calls = [c for c in agent.llm.complete.call_args_list if c.kwargs.get("use_doctrinal_mcp")]
    assert len(mcp_calls) == int(mcp_enabled)
    if mcp_calls:
        assert mcp_calls[0].kwargs["prompt"] == "Topic: fictional sleep study"
