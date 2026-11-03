"""Observed factual imperative uses exact sources despite a Visit Prep label."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.clients.provider_question_contract import ProviderQuestionResponse
from app.schemas import DoctrinalSensitivity, Intent
from app.sovereign.router import Router
from tests.test_provider_question_context import handle, sovereign
from tests.test_provider_source_readiness import source_only_response
from tests.test_record_review_context import descriptor


QUESTION = (
    "Show the recorded blood pressures and medication instructions "
    "from both reviewed prior visits."
)


def setup(brief_state="ready", intent=Intent.VISIT_PREP):
    service, context, conversation = sovereign()
    service.router = Router(service.s, service.llm)
    service.router.route = AsyncMock(return_value=(intent, DoctrinalSensitivity.NONE, ""))
    service.llm.classify_json = AsyncMock(return_value={"requires_prepared_context": True})
    raw = source_only_response(brief_state)
    raw["result"]["current_through"] = "2026-10-29T14:30:00Z"
    if brief_state == "ready":
        raw["result"]["generated_at"] = "2026-11-03T12:00:00Z"
    for index, date in enumerate(("2026-10-20", "2026-10-29")):
        raw["result"]["provenance"][index]["source_at"] = date + "T14:30:00Z"
        raw["result"]["reviewed_sources"][index]["source_at"] = date + "T14:30:00Z"
    raw["result"]["reviewed_sources"][0]["sections"] = {
        "Recorded blood pressure": "Blood pressure 126/78.",
        "Medication instructions": "Lisinopril 10 mg daily was recorded.",
    }
    raw["result"]["reviewed_sources"][1]["sections"] = {
        "Recorded blood pressure": "Blood pressure 126/78.",
        "Medication correction": "Correction: lisinopril 5 mg daily; the prior 10 mg instruction was erroneous.",
    }
    context.read.return_value = ProviderQuestionResponse.model_validate(raw)
    context.read.return_value.result._reviewed_excerpts_version = "1.0.0"
    spans = ((0, 0), (0, 1), (1, 0), (1, 1))
    service.llm.complete = AsyncMock(return_value=json.dumps({
        "excerpts": [{"source_index": source, "section_index": section} for source, section in spans],
        "review_context": descriptor(context.read.return_value.result, spans),
    }))
    return service, context, conversation, spans


@pytest.mark.parametrize("brief_state", ["ready", "pending", "unavailable"])
@pytest.mark.parametrize("question", [
    QUESTION,
    "Quote the documented blood pressures and medication instructions from both reviewed prior visits.",
    "List recorded blood pressure and medication instructions from both reviewed prior visits.",
])
def test_factual_imperative_reroutes_to_exact_dated_sources_and_retained_citations(brief_state, question):
    service, context, conversation, spans = setup(brief_state)
    service.agents[Intent.VISIT_PREP].run = AsyncMock(side_effect=AssertionError("No narrative fallback"))
    response = handle(service, question)

    assert response.intent == Intent.ENGAGEMENT
    assert response.structured["record_evidence_status"] == "validated_excerpts"
    assert "Selected reviewed-note excerpts for your question:" in response.answer
    for source in context.read.return_value.result.reviewed_sources:
        for heading, text in source.sections.items():
            assert f"{source.source_at.date()} — {heading}" in response.answer
            assert "> " + text in response.answer
    assert len(response.citations) == 4
    assert {citation.title[:10] for citation in response.citations} == {"2026-10-20", "2026-10-29"}
    assert "Source: 2 selected reviewed prior notes through 2026-10-29." in response.answer
    service.agents[Intent.VISIT_PREP].run.assert_not_called()
    service.llm.classify_json.assert_not_called()
    service.session_prep.read.assert_not_called()
    assert context.read.call_count == 1
    assert service.arbiter.review.call_args.kwargs["sensitivity"] == DoctrinalSensitivity.LOW
    assert set(response.presentation.suppress) == {"intent_badge", "doctrinal_badge"}
    final = conversation.write_final_response.call_args.kwargs
    assert final["outcome"] == "completed" and final["answer"] == response.answer
    assert final["provider_context_ref"] == context.read.return_value.tool_call_ref
    assert [(item.source_index, item.section_index) for item in final["reviewed_excerpts"].excerpts] == list(spans)


@pytest.mark.parametrize("question", [
    QUESTION + " Then prescribe lisinopril 10 mg.",
    QUESTION + " Fetch the current Athena record.",
    QUESTION + " Query Epic for any new medication orders.",
    QUESTION + " Recommend the dose I should prescribe today.",
    "Should I increase lisinopril based on the recorded blood pressures?",
])
def test_advisory_action_or_external_request_cannot_enter_factual_override(question):
    service, context, _, _ = setup(intent=Intent.PLAN_WORKBENCH)
    response = handle(service, question)
    assert response.intent == Intent.PLAN_WORKBENCH
    assert response.safety_notice == "capability_denied"
    service.llm.classify_json.assert_not_called()
    context.read.assert_not_called()
    service.llm.complete.assert_not_called()
    service.arbiter.review.assert_not_called()


def test_general_visit_overview_retains_narrative_with_actual_reviewed_source_footer():
    service, context, conversation, _ = setup()
    service.llm.complete.return_value = "Fictional visit overview marker."
    response = handle(service, "Prepare a concise overview for the next visit.")
    assert response.intent == Intent.VISIT_PREP
    assert response.answer.startswith("Fictional visit overview marker.")
    assert "Source: 2 selected reviewed prior notes through 2026-10-29." in response.answer
    assert "Selected reviewed-note excerpts" not in response.answer
    assert "record_evidence_status" not in response.structured
    assert "reviewed_excerpts" not in conversation.write_final_response.call_args.kwargs
    assert context.read.call_count == 1
