"""Offline boundaries for canonical, reviewed-summary provider answers."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.agents.engagement_agent import EngagementAgent
from app.agents.ethics_agent import EthicsConsultAgent
from app.agents.visit_prep_agent import VisitPrepAgent
from app.clients.platform_conversation import (
    ConversationDeliveryError, ConversationReceipt, PlatformConversationClient, PlatformTurn,
)
from app.clients.conversation_read import ConversationContextResponse
from app.clients.provider_question import ProviderQuestionClient
from app.clients.platform_usage import (
    PlatformUsageClient, activate_usage_context, deactivate_usage_context,
    successful_usage_refs, usage_reporting_complete,
)
from app.clients.provider_question_contract import ProviderQuestionRequest, ProviderQuestionResponse
from app.clients.session_prep import MAX_SESSION_PREP_RESPONSE_BYTES, SessionPrepDeliveryError
from app.config import AuthMode, ComplianceProfile, MountProfile, Persona, Settings
from app.schemas import ChatRequest, DoctrinalSensitivity, Intent
from app.security.mount import MountContext, MountResolution, MountResolutionError, MountResolver
from app.sovereign.orchestrator import Sovereign
from app.sovereign.router import Router


FIXTURES = Path(__file__).parents[1] / "contracts/fixtures/provider-question-context-v1"
TURN_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def fixture(name="response-ready.json"):
    return json.loads((FIXTURES / name).read_text())


def turn():
    return PlatformTurn(
        interaction_ref="interaction_synthetic_001", client_turn_id=TURN_ID,
        trace_id="trace_synthetic_001", mount_context_token="mount_synthetic_001",
    )


def headers():
    return {
        "Content-Type": "application/json", "X-Trace-ID": turn().trace_id,
        "X-Cosmaneura-Client-Turn-ID": str(TURN_ID), "Idempotency-Replayed": "false",
    }


async def read(handler, token_value="synthetic-workload-token"):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ProviderQuestionClient(
            platform_base_url="https://platform.example", client=http,
            workload_token_provider=AsyncMock(return_value=token_value),
        )
        return await client.read(
            turn=turn(), request=ProviderQuestionRequest.model_validate(fixture("request.json")),
        )


@pytest.mark.parametrize("state", ["ready", "pending", "unavailable"])
def test_closed_canonical_fixtures_and_single_read(state):
    raw = fixture(f"response-{state}.json")
    assert ProviderQuestionResponse.model_validate(raw).model_dump(mode="json") == raw
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, headers=headers(), json=raw)

    response = asyncio.run(read(handler))
    assert response.result.state == state
    assert len(sent) == 1  # pending is immutable, never logically polled
    assert str(sent[0].url) == "https://platform.example/service/v1/agent/tools/invoke"
    assert json.loads(sent[0].content) == fixture("request.json")
    assert sent[0].headers["authorization"] == "Bearer synthetic-workload-token"
    assert sent[0].headers["x-cosmaneura-mount-context"] == turn().mount_context_token
    assert sent[0].headers["idempotency-key"] != str(TURN_ID)


@pytest.mark.parametrize("path,value", [
    (("raw_note",), "forbidden"),
    (("tool_call_ref",), "bad ref"),
    (("result", "provenance"), []),
    (("result", "state"), "pending"),
    (("result", "generated_at"), "2026-09-02T12:00:02"),
    (("result", "current_through"), "2027-09-02T12:00:02Z"),
    (("result", "prepared_brief", "recap", "text"), "x" * 4001),
    (("result", "prepared_brief", "recap", "source_refs"), ["unselected_source"]),
    (("result", "provenance", 0, "source_class"), "patient_reported_visit_prep"),
])
def test_invalid_or_unbounded_context_is_rejected(path, value):
    raw = fixture()
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        ProviderQuestionResponse.model_validate(raw)


@pytest.mark.parametrize("selector", ["patient_ref", "context_snapshot_ref", "raw_notes"])
def test_request_cannot_select_a_record(selector):
    request = fixture("request.json")
    request["arguments"][selector] = "browser_sentinel"
    with pytest.raises(ValidationError):
        ProviderQuestionRequest.model_validate(request)


@pytest.mark.parametrize("failure", [
    "oversize", "duplicate-json", "wrong-turn", "wrong-trace", "duplicate-header",
    "compressed", "redirect", "malformed", "authorization", "conflict",
])
def test_transport_fails_closed_without_exposing_payload_or_following_redirects(failure):
    sent = []

    def handler(request):
        sent.append(request)
        body = json.dumps(fixture()).encode()
        hdr = list(headers().items())
        status = 200
        if failure == "oversize":
            body = b"x" * (MAX_SESSION_PREP_RESPONSE_BYTES + 1)
        elif failure == "duplicate-json":
            body = body.replace(b'{"tool_call_ref":', b'{"tool_call_ref":"sentinel", "tool_call_ref":')
        elif failure in {"wrong-turn", "wrong-trace"}:
            hdr[2 if failure == "wrong-turn" else 1] = (hdr[2 if failure == "wrong-turn" else 1][0], "mismatch")
        elif failure == "duplicate-header":
            hdr.append(("X-Trace-ID", turn().trace_id))
        elif failure == "compressed":
            hdr.append(("Content-Encoding", "gzip"))
        elif failure == "redirect":
            status = 307
            hdr.append(("Location", "https://unwanted.example/"))
        elif failure == "malformed":
            body = b"sentinel invalid JSON"
        else:
            status = 403 if failure == "authorization" else 409
            body = b'{"detail":"sentinel"}'
        return httpx.Response(status, headers=hdr, stream=httpx.ByteStream(body))

    with pytest.raises(SessionPrepDeliveryError) as caught:
        asyncio.run(read(handler))
    assert "sentinel" not in str(caught.value)
    assert len(sent) == 1


def test_invalid_workload_token_never_leaves_client():
    handler = AsyncMock()
    with pytest.raises(SessionPrepDeliveryError):
        asyncio.run(read(handler, token_value="token\r\nforbidden"))
    handler.assert_not_called()


@pytest.mark.parametrize("agent_class", [EngagementAgent, VisitPrepAgent, EthicsConsultAgent])
def test_only_baa_drafting_receives_summary_and_omitted_facts_instruction(agent_class):
    agent = agent_class(Settings(_env_file=None, mock_mode=True))
    agent.llm.complete = AsyncMock(return_value="The dose is unavailable in the prepared summary.")
    agent.llm.classify_json = AsyncMock(return_value={"topic": "clinical ethics"})
    if isinstance(agent, EthicsConsultAgent):
        agent.pubmed.search = AsyncMock(return_value=[])
    result = ProviderQuestionResponse.model_validate(fixture()).result
    request = ChatRequest(
        persona=Persona.PROVIDER, message="Which medication dose was documented in prior visits?",
        metadata={key: "browser_sentinel" for key in (
            "health_history", "case_context", "prior_sessions", "journey_digest",
            "intake_summary", "patient_profile",
        )},
    )
    asyncio.run(agent.run(request, ComplianceProfile(Persona.PROVIDER), provider_context=result))
    call = agent.llm.complete.call_args.kwargs
    assert "A synthetic finalized note" in call["prompt"]
    assert "browser_sentinel" not in call["prompt"]
    assert "note_synthetic_001" not in call["prompt"]
    assert "sha256_" not in call["prompt"]
    assert "unavailable in this summary" in call["system"]
    assert "not full notes or chat history" in call["system"]
    assert not call.get("use_doctrinal_mcp")
    if isinstance(agent, EthicsConsultAgent):
        assert "A synthetic finalized note" not in str(agent.llm.classify_json.call_args)
        assert all(c.args[0] == "clinical ethics" for c in agent.pubmed.search.call_args_list)


@pytest.mark.parametrize("decision", [True, False])
def test_record_selection_uses_current_question_without_browser_metadata(decision):
    router = Router(Settings(_env_file=None, mock_mode=True))
    router.llm.classify_json = AsyncMock(return_value={"requires_prepared_context": decision})
    request = ChatRequest(persona=Persona.PROVIDER, message="Explain insomnia.",
                          metadata={"context": "browser_sentinel"})
    assert asyncio.run(router.needs_provider_context(request, Intent.ENGAGEMENT)) is decision
    assert router.llm.classify_json.call_args.kwargs["prompt"] == request.message
    assert asyncio.run(router.needs_provider_context(request, Intent.VISIT_PREP)) is True
    assert asyncio.run(router.needs_provider_context(request, Intent.REFERRAL)) is False
    assert router.llm.classify_json.call_count == 1
    router.llm.classify_json.return_value = {"requires_prepared_context": "true"}
    with pytest.raises(ValueError):
        asyncio.run(router.needs_provider_context(request, Intent.ENGAGEMENT))


class Resolver(MountResolver):
    def __init__(self, record_capability=True):
        self.record_capability = record_capability

    async def resolve(self, request):
        capabilities = ("education:answer", "ethics:consult", "visit-prep:run")
        if self.record_capability:
            capabilities += ("session-prep:read",)
        return MountResolution(
            context=MountContext(surface="provider_assistant", auth_tier="provider",
                                 capabilities=capabilities, tenant_id="tenant_synthetic",
                                 actor_id="actor_synthetic"),
            client_turn_id=request.client_turn_id, trace_id=request.trace_id,
            interaction_ref="interaction_synthetic_001",
        )


def sovereign(state="ready", *, needs_context=True, record_capability=True, enabled=True):
    receipt = ConversationReceipt(
        client_turn_id=TURN_ID, turn_ref="turn_synthetic_001", phase="user_accepted",
        state="accepted", recorded_at=datetime.now(UTC), replayed=False,
    )
    conversation = AsyncMock()
    conversation.write_user_accepted.return_value = receipt
    context = AsyncMock()
    context.read.return_value = ProviderQuestionResponse.model_validate(fixture(f"response-{state}.json"))
    settings = Settings(
        _env_file=None, mock_mode=True, mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_synthetic", auth_mode=AuthMode.DISABLED,
        platform_agent_api_enabled=True, feature_session_prep=enabled,
        platform_agent_api_base_url="https://platform.example",
        platform_agent_api_scope="api://synthetic/.default", platform_workload_client_id="synthetic-client",
    )
    history = AsyncMock()
    history.read_context.return_value = ConversationContextResponse.model_validate({
        "tool_call_ref": "history_context_fictional", "status": "completed",
        "result": {"schema_version": "1.0.0", "messages": [], "source_version_digest": None},
    })
    service = Sovereign(
        settings, mount_resolver=Resolver(record_capability), platform_audit_client=AsyncMock(),
        platform_usage_client=AsyncMock(), platform_conversation_client=conversation,
        session_prep_client=AsyncMock(), provider_question_client=context,
        conversation_read_client=history,
    )
    service.router.route = AsyncMock(return_value=(Intent.ENGAGEMENT, DoctrinalSensitivity.LOW, ""))
    service.router.needs_provider_context = AsyncMock(return_value=needs_context)
    service.llm.complete = AsyncMock(return_value="Prepared summary: planned a sleep diary follow-up.")
    service.arbiter.review = AsyncMock()
    service.arbiter.review.return_value.conforms = True
    service.arbiter.review.return_value.citations = []
    # Use the real verdict type in ChatResponse.
    from app.schemas import DoctrinalVerdict
    service.arbiter.review.return_value = DoctrinalVerdict(conforms=True, rationale="Synthetic review")
    service._write_platform_observation = AsyncMock()
    return service, context, conversation


def handle(service, message="From the prior visits, what follow-up was planned for this patient?"):
    return asyncio.run(service.handle(
        ChatRequest(persona=Persona.PROVIDER, client_turn_id=TURN_ID,
                    message=message),
        mount_context_token="mount_synthetic_001", client_turn_id=TURN_ID,
    ))


def test_ready_context_is_bound_to_accepted_turn_and_final_but_not_browser_response():
    service, context, conversation = sovereign()
    response = handle(service)
    assert context.read.call_args.kwargs["request"].arguments.source_turn_ref == "turn_synthetic_001"
    assert conversation.write_final_response.call_args.kwargs["provider_context_ref"] == fixture()["tool_call_ref"]
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "completed"
    assert "prepared summary" in response.answer.lower()
    assert fixture()["tool_call_ref"] not in response.model_dump_json()
    assert "source_version_digest" not in response.model_dump_json()


@pytest.mark.parametrize("state", ["pending", "unavailable"])
def test_nonready_context_finalizes_unavailable_with_exact_dependency_without_drafting(state):
    service, context, conversation = sovereign(state)
    response = handle(service)
    assert "cannot confirm facts" in response.answer
    assert "Open Session Prep to build or refresh" in response.answer
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"
    assert conversation.write_final_response.call_args.kwargs["provider_context_ref"] == fixture()["tool_call_ref"]
    service.llm.complete.assert_not_called()
    service.arbiter.review.assert_not_called()
    assert context.read.call_count == 1
    assert service._write_platform_observation.call_args.kwargs["status"] == "failed"


@pytest.mark.parametrize("enabled,needs_context", [(True, False), (False, True)])
def test_general_education_and_disabled_lane_skip_context(enabled, needs_context):
    service, context, conversation = sovereign(enabled=enabled, needs_context=needs_context)
    handle(service, "Explain what insomnia means.")
    context.read.assert_not_called()
    assert "provider_context_ref" not in conversation.write_final_response.call_args.kwargs


def test_missing_record_capability_cannot_draft_a_record_answer():
    service, context, conversation = sovereign(record_capability=False)
    handle(service)
    context.read.assert_not_called()
    service.llm.complete.assert_not_called()
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"


def test_changed_context_at_final_acceptance_blocks_answer_egress():
    service, _, conversation = sovereign()
    conversation.write_final_response.side_effect = ConversationDeliveryError("conflict", retryable=False)
    with pytest.raises(MountResolutionError) as error:
        handle(service)
    assert error.value.status_code == 409
    assert "sleep diary" not in str(error.value)


def test_context_draft_is_deidentified_before_non_baa_arbitration():
    service, _, _ = sovereign()
    service.llm.complete.return_value = "Synthetic contact: synthetic.patient@example.invalid"
    handle(service)
    reviewed = service.arbiter.review.call_args.kwargs["deidentified_draft"]
    assert "synthetic.patient@example.invalid" not in reviewed
    assert "[REDACTED:EMAIL]" in reviewed


@pytest.mark.parametrize("message,expected", [
    ("From the prior visits, what follow-up was planned for this patient?", True),
    ("Which medication dose was documented in the prior visits?", True),
    ("Explain what insomnia means.", False),
])
def test_offline_classifier_supports_record_and_general_question_smokes(message, expected):
    router = Router(Settings(_env_file=None, mock_mode=True))
    request = ChatRequest(persona=Persona.PROVIDER, message=message)
    assert asyncio.run(router.needs_provider_context(request, Intent.ENGAGEMENT)) is expected


def test_context_selection_uses_real_usage_writer_with_content_free_payload():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(201, headers=headers(), json={
            "record_ref": "usage_synthetic_001", "recorded_at": datetime.now(UTC).isoformat(),
        })

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            writer = PlatformUsageClient(
                platform_base_url="https://platform.example", client=http,
                workload_token_provider=AsyncMock(return_value="synthetic-workload-token"),
            )
            activate_usage_context(turn=turn(), client=writer)
            try:
                router = Router(Settings(_env_file=None, mock_mode=True))
                await router.needs_provider_context(ChatRequest(
                    persona=Persona.PROVIDER, message="What was documented in prior visits?",
                ), Intent.ENGAGEMENT)
                assert usage_reporting_complete()
                assert successful_usage_refs() == ("usage_synthetic_001",)
            finally:
                deactivate_usage_context()

    asyncio.run(scenario())
    assert len(sent) == 1
    assert sent[0]["purpose"] == "provider_context_selection"
    assert sent[0]["call_kind"] == "routing"
    assert "prior visits" not in json.dumps(sent)


def test_canonical_writer_includes_only_explicit_server_dependency():
    client = PlatformConversationClient(platform_base_url="https://platform.example",
                                        workload_token_provider=AsyncMock())
    client._write = AsyncMock()
    kwargs = dict(turn=turn(), expected_turn_ref="turn_synthetic_001", answer="Synthetic answer",
                  outcome="completed", presentation={"mode": "standard"})
    asyncio.run(client.write_final_response(**kwargs))
    assert "provider_context_ref" not in client._write.call_args.kwargs["payload"]
    asyncio.run(client.write_final_response(**kwargs, provider_context_ref="context_synthetic_001"))
    assert client._write.call_args.kwargs["payload"]["provider_context_ref"] == "context_synthetic_001"
    with pytest.raises(ValueError):
        asyncio.run(client.write_final_response(**kwargs, provider_context_ref="invalid reference"))
