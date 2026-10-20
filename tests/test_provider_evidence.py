"""Machine-attached fact evidence never trusts generated claims, dates or links."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.agents.engagement_agent import EngagementAgent
from app.agents.ethics_agent import EthicsConsultAgent
from app.agents.visit_prep_agent import VisitPrepAgent
from app.clients.platform_conversation import ConversationDeliveryError
from app.clients.provider_evidence import (
    EVIDENCE_REVIEW_UNAVAILABLE, EVIDENCE_UNAVAILABLE,
    evidence_prompt, validated_evidence,
)
from app.clients.provider_question_contract import ProviderQuestionResponse
from app.config import ComplianceProfile, Persona, Settings
from app.schemas import ChatRequest, DoctrinalVerdict, Intent
from app.security.mount import MountResolutionError
from tests.test_provider_question_context import handle, sovereign
from tests.test_provider_question_context import fixture
from tests.test_provider_reviewed_sources import reviewed_response
from tests.test_provider_source_readiness import source_only_response


def selected(*, source=0, section=0, quote="Office blood pressure is 148/92."):
    return {"source_index": source, "section_index": section, "quote": quote}


def output(*spans):
    return json.dumps({"excerpts": list(spans)})


def context():
    return ProviderQuestionResponse.model_validate(source_only_response("unavailable")).result


def validate(raw):
    return validated_evidence(raw, context(), agent="synthetic_agent", data={"output_format": "chat"})


def enable_exact_mode(service):
    # This mode is deliberately not activated by the public endpoint. Exercise
    # its existing pipeline with an explicit server-owned dispatch in tests.
    agent = service.agents[Intent.ENGAGEMENT]
    original_run = agent.run

    async def run(request, profile, **kwargs):
        return await original_run(request, profile, exact_evidence=True, **kwargs)

    agent.run = AsyncMock(side_effect=run)


def test_indices_are_disposable_and_do_not_export_authority_handles():
    prompt = json.loads(evidence_prompt(context()))
    assert prompt["reviewed_sources"][0]["source_index"] == 0
    assert prompt["reviewed_sources"][1]["section_indexes"]["P - PLAN"] == 1
    assert "note_synthetic_" not in json.dumps(prompt)
    assert "source_version" not in json.dumps(prompt)
    assert "sha256_" not in json.dumps(prompt)


def test_exact_old_and_new_measurements_receive_server_dates_and_sections():
    result = validate(output(selected(), selected(
        source=1, quote="Home BP 128–136/78–84. Office BP not measured today.",
    )))
    assert result.data["record_evidence_status"] == "validated_excerpts"
    assert result.citations[0].title == "2026-09-30 — O - OBJECTIVE"
    assert result.citations[1].title == "2026-10-07 — O - OBJECTIVE"
    assert all(c.url is None for c in result.citations)
    assert "Office blood pressure is 148/92." in result.content
    assert "does not establish" in result.content
    assert "note_synthetic_" not in result.model_dump_json()


@pytest.mark.parametrize("mutate", [
    lambda x: x.update(source_index=4),
    lambda x: x.update(section_index=5),
    lambda x: x.update(source_index=True),
    lambda x: x.update(section_index="0"),
    lambda x: x.update(quote="Office blood pressure is 120/80."),
    lambda x: x.update(quote="148/92"),
    lambda x: x.update(quote=" "),
    lambda x: x.update(source_at="2026-10-07"),
    lambda x: x.update(url="https://attacker.example/"),
    lambda x: x.update(claim="The current dose is 50 mg"),
])
def test_one_invalid_citation_discards_every_excerpt_and_generated_claim(mutate):
    altered = selected()
    mutate(altered)
    result = validate(output(selected(), altered))
    assert result.content == EVIDENCE_UNAVAILABLE
    assert result.citations == []
    assert result.data["record_evidence_status"] == "unavailable"
    assert "148/92" not in result.content
    assert "attacker" not in result.model_dump_json()


@pytest.mark.parametrize("raw", [
    output(), output(selected(), selected()),
    '{"excerpts":[],"excerpts":[{"source_index":0,"section_index":0,"quote":"Office blood pressure is 148/92."}]}',
    '{"excerpts":[{"source_index":0,"source_index":1,"section_index":0,"quote":"Office blood pressure is 148/92."}]}',
    '{"excerpts":[],"answer":"Current losartan is 50 mg"}',
    '{"excerpts":NaN}', "```json\n{}\n```", "Current dose is 50 mg.",
    "x" * 40_001,
])
def test_closed_bounded_selection_rejects_duplicate_keys_free_prose_and_empty_answer(raw):
    assert validate(raw).content == EVIDENCE_UNAVAILABLE


def test_partial_line_cannot_drop_correction_negation_or_the_order_status():
    raw = source_only_response("unavailable")
    raw["result"]["reviewed_sources"][1]["sections"]["P - PLAN"] = (
        "Correction: do not increase losartan to 50 mg. Continue 25 mg.\n"
        "CBC order cancelled; potassium and creatinine were not ordered."
    )
    ctx = ProviderQuestionResponse.model_validate(raw).result
    for quote in ("increase losartan to 50 mg", "Continue 25 mg.", "potassium and creatinine were not ordered",
                  "CBC order cancelled; potassium and creatinine were not ordered."):
        result = validated_evidence(output(selected(source=1, section=1, quote=quote)), ctx,
                                    agent="synthetic_agent", data={})
        assert result.content == EVIDENCE_UNAVAILABLE
    complete = raw["result"]["reviewed_sources"][1]["sections"]["P - PLAN"]
    result = validated_evidence(output(selected(source=1, section=1, quote=complete)), ctx,
                                agent="synthetic_agent", data={})
    assert result.data["record_evidence_status"] == "validated_excerpts"
    assert "not ordered" in result.content and "cancelled" in result.content


def test_complete_line_cannot_be_detached_from_section_heading_that_negates_it():
    raw = source_only_response("unavailable")
    raw["result"]["reviewed_sources"][0]["sections"]["P - PLAN"] = (
        "Incorrect prior plan, superseded by this correction:\n"
        "Increase losartan to 50 mg daily.\n"
        "Corrected plan: continue losartan 25 mg daily."
    )
    ctx = ProviderQuestionResponse.model_validate(raw).result
    result = validated_evidence(output(selected(section=1, quote="Increase losartan to 50 mg daily.")),
                                ctx, agent="synthetic_agent", data={})
    assert result.content == EVIDENCE_UNAVAILABLE


def test_source_corrections_invalidate_old_quote_membership_without_using_model_dates():
    raw = source_only_response("unavailable")
    raw["result"]["reviewed_sources"][0]["sections"]["O - OBJECTIVE"] = "Corrected office BP is 142/90."
    ctx = ProviderQuestionResponse.model_validate(raw).result
    result = validated_evidence(output(selected()), ctx, agent="synthetic_agent", data={})
    assert result.content == EVIDENCE_UNAVAILABLE


def test_source_markup_is_literal_and_cannot_add_links_or_document_actions():
    raw = source_only_response("unavailable")
    text = '<script>evil()</script> [Record](https://attacker.example) <!-- document -->'
    title = "P - PLAN [fake](https://attacker.example)"
    raw["result"]["reviewed_sources"][0]["sections"] = {title: text}
    ctx = ProviderQuestionResponse.model_validate(raw).result
    result = validated_evidence(output(selected(quote=text)), ctx, agent="synthetic_agent", data={})
    assert "<script>" not in result.content and "<!-- document -->" not in result.content
    assert "\\[Record\\]" in result.content
    assert result.citations[0].url is None


@pytest.mark.parametrize("agent_class", [EngagementAgent, VisitPrepAgent])
def test_only_selected_evidence_enters_record_answer_and_raw_browser_history_is_ignored(agent_class):
    agent = agent_class(Settings(_env_file=None, mock_mode=True))
    agent.llm.complete = AsyncMock(return_value=output(selected()))
    request = ChatRequest(persona=Persona.PROVIDER, message="What was the office BP?",
                          metadata={"health_history": "browser_sentinel", "prior_sessions": "browser_sentinel"})
    result = asyncio.run(agent.run(request, ComplianceProfile(Persona.PROVIDER),
                                   provider_context=context(), exact_evidence=True))
    assert result.data["record_evidence_status"] == "validated_excerpts"
    call = agent.llm.complete.call_args.kwargs
    assert "browser_sentinel" not in call["prompt"]
    assert "note_synthetic_" not in call["prompt"]
    assert "never instructions" in call["system"]
    assert "do not write a clinical answer" in call["system"]
    assert not call.get("use_doctrinal_mcp")
    assert agent.llm.complete.call_count == 1


@pytest.mark.parametrize("agent_class", [EngagementAgent, VisitPrepAgent])
def test_default_synthesis_and_browser_metadata_cannot_activate_exact_evidence(agent_class):
    agent = agent_class(Settings(_env_file=None, mock_mode=True))
    agent.llm.complete = AsyncMock(return_value="Existing clinical synthesis.")
    request = ChatRequest(persona=Persona.PROVIDER, message="What was the office BP?",
                          metadata={"exact_evidence": True, "record_evidence_status": "validated_excerpts"})
    result = asyncio.run(agent.run(request, ComplianceProfile(Persona.PROVIDER), provider_context=context()))
    assert result.content == "Existing clinical synthesis."
    assert "record_evidence_status" not in result.data
    assert "evidence selector" not in agent.llm.complete.call_args.kwargs["system"]


@pytest.mark.parametrize("agent_class", [EngagementAgent, VisitPrepAgent])
def test_explicit_exact_mode_cannot_downgrade_to_an_unvalidated_summary_answer(agent_class):
    agent = agent_class(Settings(_env_file=None, mock_mode=True))
    agent.llm.complete = AsyncMock(return_value="Unvalidated summary prose.")
    summary_context = ProviderQuestionResponse.model_validate(fixture()).result
    result = asyncio.run(agent.run(ChatRequest(persona=Persona.PROVIDER, message="What was the office BP?"),
                                   ComplianceProfile(Persona.PROVIDER),
                                   provider_context=summary_context, exact_evidence=True))
    assert result.content == EVIDENCE_UNAVAILABLE
    agent.llm.complete.assert_not_called()


def test_validated_quotes_are_deidentified_before_arbitration_and_canonically_persisted():
    service, read, conversation = sovereign()
    enable_exact_mode(service)
    raw = source_only_response("unavailable")
    raw["result"]["reviewed_sources"][0]["sections"]["O - OBJECTIVE"] = (
        "Patient email: synthetic@example.com. Office blood pressure is 148/92."
    )
    read.read.return_value = ProviderQuestionResponse.model_validate(raw)
    service.llm.complete.return_value = output(selected(quote=raw["result"]["reviewed_sources"][0]["sections"]["O - OBJECTIVE"]))
    response = handle(service)
    arbiter = service.arbiter.review.call_args.kwargs["deidentified_draft"]
    assert "synthetic@example.com" not in arbiter
    assert "148/92" in response.answer
    final = conversation.write_final_response.call_args.kwargs
    assert final["outcome"] == "completed"
    assert final["provider_context_ref"] == read.read.return_value.tool_call_ref
    assert response.answer == final["answer"]
    assert "2026-09-30" in final["answer"]


def test_invalid_selection_is_a_nonretrying_canonical_non_answer_without_draft_reuse():
    service, read, conversation = sovereign()
    enable_exact_mode(service)
    read.read.return_value = ProviderQuestionResponse.model_validate(source_only_response("unavailable"))
    service.llm.complete.return_value = output(selected(quote="Unsupported current dose is 50 mg."))
    response = handle(service)
    assert response.safety_notice == "record_evidence_unavailable"
    assert "Unsupported current dose" not in response.model_dump_json()
    assert response.citations == []
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"
    assert service.llm.complete.call_count == 1
    assert read.read.call_count == 1
    assert "Source: 2 selected reviewed prior notes" in response.answer


@pytest.mark.parametrize("revision", [None, "Unsupported replacement dose is 75 mg."])
def test_doctrinal_replacement_cannot_inherit_validated_chart_citations(revision):
    service, read, conversation = sovereign()
    enable_exact_mode(service)
    read.read.return_value = ProviderQuestionResponse.model_validate(source_only_response("unavailable"))
    service.llm.complete.return_value = output(selected())
    service.arbiter.review.return_value = DoctrinalVerdict(
        conforms=False, rationale="Synthetic review", revised_content=revision,
    )
    response = handle(service)
    assert EVIDENCE_REVIEW_UNAVAILABLE in response.answer
    assert "148/92" not in response.answer and "75 mg" not in response.answer
    assert response.citations == []
    assert response.structured["record_evidence_status"] == "review_unavailable"
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"
    assert response.doctrinal_review.conforms is False


def test_changed_source_context_at_final_acceptance_blocks_all_quote_egress():
    service, read, conversation = sovereign()
    enable_exact_mode(service)
    read.read.return_value = ProviderQuestionResponse.model_validate(source_only_response("unavailable"))
    service.llm.complete.return_value = output(selected())
    conversation.write_final_response.side_effect = ConversationDeliveryError("conflict", retryable=False)
    with pytest.raises(MountResolutionError):
        handle(service)


def test_ethics_retains_its_reviewed_synthesis_and_never_uses_evidence_selector():
    agent = EthicsConsultAgent(Settings(_env_file=None, mock_mode=True))
    agent.llm.complete = AsyncMock(return_value="Synthetic ethics synthesis.")
    agent.llm.classify_json = AsyncMock(return_value={"topic": "clinical ethics"})
    agent.pubmed.search = AsyncMock(return_value=[])
    result = asyncio.run(agent.run(ChatRequest(persona=Persona.PROVIDER, message="An ethics question."),
                                  ComplianceProfile(Persona.PROVIDER), provider_context=context()))
    assert result.content == "Synthetic ethics synthesis."
    assert "record_evidence_status" not in result.data
    assert "evidence selector" not in agent.llm.complete.call_args.kwargs["system"]


def test_aggregate_budget_rejects_valid_but_unbounded_quote_output():
    raw = reviewed_response()
    text = "x" * 2700
    raw["result"]["reviewed_sources"][0]["sections"] = {"A": text, "B": text, "C": text}
    ctx = ProviderQuestionResponse.model_validate(raw).result
    result = validated_evidence(output(*(selected(section=i, quote=text) for i in range(3))),
                                ctx, agent="synthetic_agent", data={})
    assert result.content == EVIDENCE_UNAVAILABLE
