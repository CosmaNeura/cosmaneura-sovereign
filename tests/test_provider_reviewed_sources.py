"""Reviewed-note evidence reaches drafting without broadening record selection."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.agents.engagement_agent import EngagementAgent
from app.agents.ethics_agent import EthicsConsultAgent
from app.agents.visit_prep_agent import VisitPrepAgent
from app.clients.provider_question import ProviderQuestionClient
from app.clients.provider_question_contract import (
    ProviderQuestionArguments, ProviderQuestionRequest, ProviderQuestionResponse,
    ReviewedProviderQuestionResult,
)
from app.clients.session_prep import SessionPrepDeliveryError
from app.config import ComplianceProfile, Persona, Settings
from app.schemas import ChatRequest, DoctrinalVerdict
from tests.test_provider_question_context import fixture, handle, headers, sovereign, turn


def reviewed_response(state="ready"):
    raw = fixture(f"response-{state}.json")
    result = raw["result"]
    result["schema_version"] = "1.1.0"
    result["reviewed_sources"] = []
    if state != "ready":
        return raw
    # The lossy brief intentionally omits the old measured BP. The original
    # sections retain it and distinguish an unmeasured later office BP.
    result["prepared_brief"]["recap"]["text"] = (
        "Home blood pressures improved. A dose increase was planned."
    )
    result["provenance"][0]["source_at"] = "2026-09-30T14:30:00Z"
    later = copy.deepcopy(result["provenance"][0])
    later.update(source_ref="note_synthetic_002", source_at="2026-10-07T14:30:00Z")
    result["provenance"].append(later)
    result["current_through"] = "2026-10-07T14:30:00Z"
    result["generated_at"] = "2026-10-20T12:00:00Z"
    result["reviewed_sources"] = [
        {
            "source_ref": item["source_ref"], "source_version": item["source_version"],
            "source_at": item["source_at"], "sections": sections,
        }
        for item, sections in zip(result["provenance"], [
            {"O - OBJECTIVE": "Office blood pressure is 148/92.",
             "P - PLAN": "Start losartan 25 mg daily. No potassium or creatinine result documented."},
            {"O - OBJECTIVE": "Home BP 128–136/78–84. Office BP not measured today.",
             "P - PLAN": "Plan: increase losartan to 50 mg daily. CBC ordered; completion/result undocumented."},
        ], strict=True)
    ]
    return raw


def reviewed_request():
    return ProviderQuestionRequest(tool_version="1.1.0", arguments=ProviderQuestionArguments(
        source_turn_ref="turn_synthetic_001", detail="reviewed_sources",
    ))


@pytest.mark.parametrize("state", ["ready", "pending", "unavailable"])
def test_shared_core_v11_fixtures_validate_and_roundtrip(state):
    path = (Path(__file__).parents[1] / "contracts/fixtures/provider-question-context-v1.1"
            / f"response-{state}.json")
    raw = json.loads(path.read_text())
    assert ProviderQuestionResponse.model_validate(raw).model_dump(mode="json") == raw


@pytest.mark.parametrize("state", ["ready", "pending", "unavailable"])
def test_versioned_result_roundtrip_keeps_legacy_wire_unchanged(state):
    legacy = fixture(f"response-{state}.json")
    assert ProviderQuestionResponse.model_validate(legacy).model_dump(mode="json") == legacy
    new = reviewed_response(state)
    parsed = ProviderQuestionResponse.model_validate(new)
    assert isinstance(parsed.result, ReviewedProviderQuestionResult)
    assert parsed.model_dump(mode="json") == new


@pytest.mark.parametrize("version,detail", [("1.0.0", "reviewed_sources"), ("1.1.0", "prep_summary")])
def test_version_cannot_silently_change_read_scope(version, detail):
    with pytest.raises(ValidationError):
        ProviderQuestionRequest(tool_version=version, arguments=ProviderQuestionArguments(
            source_turn_ref="turn_synthetic_001", detail=detail,
        ))


@pytest.mark.parametrize("field,value", [
    ("source_ref", "foreign_note_0001"), ("source_version", 2),
    ("source_at", "2026-09-29T14:30:00Z"),
    ("sections", {"plan": "x" * 18_000}),
    ("sections", {"plan": "\x00"}), ("sections", {"plan": " "}),
    ("sections", {" ": "Some fact"}),
])
def test_missing_foreign_changed_or_unbounded_source_cannot_enter_context(field, value):
    raw = reviewed_response()
    raw["result"]["reviewed_sources"][0][field] = value
    with pytest.raises(ValidationError):
        ProviderQuestionResponse.model_validate(raw)


def test_ready_evidence_requires_exact_selected_source_set_and_nonready_has_none():
    for state in ("ready", "pending"):
        raw = reviewed_response(state)
        raw["result"]["reviewed_sources"] = reviewed_response()["result"]["reviewed_sources"][:1]
        with pytest.raises(ValidationError):
            ProviderQuestionResponse.model_validate(raw)


def test_lossy_summary_does_not_drop_dated_original_facts_from_model_context():
    result = ProviderQuestionResponse.model_validate(reviewed_response()).result
    projection = json.loads(result.prompt_text())
    assert projection["source_scope"] == "selected_reviewed_notes"
    assert "148/92" not in projection["summary"]["recap"]["text"]
    earlier, later = projection["reviewed_sources"]
    assert earlier["note_date"] == "2026-09-30T14:30:00+00:00"
    assert earlier["sections"]["O - OBJECTIVE"] == "Office blood pressure is 148/92."
    assert later["note_date"] == "2026-10-07T14:30:00+00:00"
    assert "not measured today" in later["sections"]["O - OBJECTIVE"]
    assert "Plan: increase" in later["sections"]["P - PLAN"]
    assert "CBC ordered; completion/result undocumented" in later["sections"]["P - PLAN"]
    serialized = result.prompt_text()
    assert "note_synthetic_" not in serialized
    assert "sha256_" not in serialized


def test_summary_only_projection_keeps_citation_dates_without_claiming_exhaustive_facts():
    result = ProviderQuestionResponse.model_validate(fixture()).result
    projection = json.loads(result.prompt_text())
    assert projection["source_scope"] == "prepared_summary"
    assert projection["source_selection_completeness"] == "complete"
    assert projection["summary"]["recap"]["cited_note_dates"] == ["2026-08-12T14:30:00+00:00"]
    assert "reviewed_sources" not in projection
    assert "Full notes were not read" in result.source_notice()


@pytest.mark.parametrize("agent_class", [EngagementAgent, VisitPrepAgent, EthicsConsultAgent])
def test_all_record_drafting_agents_receive_original_sections_without_browser_or_public_egress(agent_class):
    agent = agent_class(Settings(_env_file=None, mock_mode=True))
    agent.llm.complete = AsyncMock(return_value="A synthetic answer.")
    agent.llm.classify_json = AsyncMock(return_value={"topic": "clinical ethics"})
    if isinstance(agent, EthicsConsultAgent):
        agent.pubmed.search = AsyncMock(return_value=[])
    request = ChatRequest(persona=Persona.PROVIDER,
                          message="Compare the measured office BP at both prior visits.",
                          metadata={"prior_sessions": "browser_sentinel"})
    context = ProviderQuestionResponse.model_validate(reviewed_response()).result
    asyncio.run(agent.run(request, ComplianceProfile(Persona.PROVIDER), provider_context=context))
    call = agent.llm.complete.call_args.kwargs
    assert "148/92" in call["prompt"] and "not measured today" in call["prompt"]
    assert "browser_sentinel" not in call["prompt"]
    assert "note_synthetic_" not in call["prompt"]
    assert not call.get("use_doctrinal_mcp")
    if isinstance(agent, EthicsConsultAgent):
        assert "148/92" not in str(agent.llm.classify_json.call_args)
        assert "148/92" not in str(agent.pubmed.search.call_args_list)


def test_record_answer_system_does_not_require_patient_education_question_list():
    agent = EngagementAgent(Settings(_env_file=None, mock_mode=True))
    agent.llm.complete = AsyncMock(return_value="A synthetic answer.")
    context = ProviderQuestionResponse.model_validate(reviewed_response()).result
    record_answer = asyncio.run(agent.run(
        ChatRequest(persona=Persona.PROVIDER, message="What was the earlier office BP?"),
        ComplianceProfile(Persona.PROVIDER), provider_context=context,
    ))
    record_system = agent.llm.complete.call_args.kwargs["system"]
    assert "only when the clinician requests them" in record_system
    assert "End with 3-5" not in record_system
    assert "ALWAYS frame next steps" not in record_system
    assert record_answer.citations == []

    patient_answer = asyncio.run(agent.run(
        ChatRequest(persona=Persona.PATIENT, message="Help me understand blood pressure."),
        ComplianceProfile(Persona.PATIENT),
    ))
    assert "End with 3-5" in agent.llm.complete.call_args.kwargs["system"]
    assert patient_answer.citations[0].source == "MedlinePlus (suggested reference)"


def read_with_handler(handler, request=None):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = ProviderQuestionClient(platform_base_url="https://platform.example",
                                            client=http, workload_token_provider=AsyncMock(return_value="synthetic-token"))
            return await client.read(turn=turn(), request=request or reviewed_request())
    return asyncio.run(scenario())


def test_old_core_explicit_unregistered_tool_falls_back_once_with_distinct_key():
    sent = []

    def handler(request):
        sent.append(request)
        if len(sent) == 1:
            return httpx.Response(404, json={"code": "RESOURCE_NOT_FOUND",
                                            "detail": "The requested tool is not available."})
        return httpx.Response(200, headers=headers(), json=fixture())

    response = read_with_handler(handler)
    assert response.result.schema_version == "1.0.0"
    assert [json.loads(r.content)["tool_version"] for r in sent] == ["1.1.0", "1.0.0"]
    assert sent[0].headers["idempotency-key"] != sent[1].headers["idempotency-key"]
    assert all(r.headers["x-cosmaneura-mount-context"] == turn().mount_context_token for r in sent)
    assert all(json.loads(r.content)["arguments"]["source_turn_ref"] == "turn_synthetic_001" for r in sent)


def test_legacy_fallback_cannot_extend_the_combined_deadline():
    sent = []
    clock_value = [0.0]

    def handler(request):
        sent.append(request)
        clock_value[0] = 11.0
        return httpx.Response(404, json={"code": "RESOURCE_NOT_FOUND",
                                        "detail": "The requested tool is not available."})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = ProviderQuestionClient(
                platform_base_url="https://platform.example", client=http,
                workload_token_provider=AsyncMock(return_value="synthetic-token"),
            )
            client._clock = lambda: clock_value[0]
            return await client.read(turn=turn(), request=reviewed_request())

    with pytest.raises(SessionPrepDeliveryError) as caught:
        asyncio.run(scenario())
    assert caught.value.status_code == 504
    assert len(sent) == 1


@pytest.mark.parametrize("status,body", [
    (403, {"code": "RESOURCE_NOT_FOUND", "detail": "The requested tool is not available."}),
    (409, {}), (503, {}), (404, {"code": "RESOURCE_NOT_FOUND", "detail": "Record unavailable"}),
    (404, {"code": "OTHER", "detail": "The requested tool is not available."}),
])
def test_auth_conflict_outage_or_record_absence_never_downgrades(status, body):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(status, json=body)

    with pytest.raises(SessionPrepDeliveryError):
        read_with_handler(handler)
    assert len(sent) == 1


def test_response_version_mismatch_and_new_payload_on_legacy_read_fail_closed():
    for request, raw in ((reviewed_request(), fixture()), (ProviderQuestionRequest(
            arguments=ProviderQuestionArguments(source_turn_ref="turn_synthetic_001")), reviewed_response())):
        with pytest.raises(SessionPrepDeliveryError):
            read_with_handler(lambda _: httpx.Response(200, headers=headers(), json=raw), request)


@pytest.mark.parametrize("full_sources", [False, True])
def test_actual_read_scope_survives_doctrinal_replacement_and_canonical_acceptance(full_sources):
    service, context, conversation = sovereign()
    if full_sources:
        context.read.return_value = ProviderQuestionResponse.model_validate(reviewed_response())
    service.arbiter.review.return_value = DoctrinalVerdict(
        conforms=False, rationale="Synthetic policy review", revised_content="Synthetic reviewed replacement.",
    )
    response = handle(service)
    canonical = conversation.write_final_response.call_args.kwargs["answer"]
    assert "Synthetic reviewed replacement." in canonical
    assert context.read.call_args.kwargs["request"].tool_version == "1.2.0"
    assert "Full notes were not read" in canonical if not full_sources else "Full notes were not read" not in canonical
    assert "Source:" in response.answer
    assert "note_synthetic_" not in response.model_dump_json()

