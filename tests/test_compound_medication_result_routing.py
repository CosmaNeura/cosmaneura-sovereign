"""Offline full pipeline regressions; scripted upstream choices are not live evidence."""

from __future__ import annotations

import copy
import json
from unittest.mock import AsyncMock

import pytest

from app.clients.llm import LLMClient
from app.clients.provider_question_contract import ProviderQuestionResponse
from app.schemas import AgentResult, DoctrinalSensitivity, DoctrinalVerdict, Intent
from app.sovereign.arbiter import DoctrinalArbiter
from app.sovereign.orchestrator import _neutral_record_review_projection
from app.sovereign.router import Router
from tests.test_historical_plan_question_routing import setup as routing_setup
from tests.test_provider_question_context import handle
from tests.test_provider_source_readiness import source_only_response


# Original MD acceptance questions, kept whole. These are fictional beta cases.
MD_QUESTIONS = [
    "What lisinopril dose was recorded most recently, and was this a dose change or a correction?",
    "What happened to the basic metabolic panel, and is there a new result?",
    "What is the metformin status and what A1C result is available?",
    "What changed with nausea and what follow-up was recorded?",
    "What breathing and chest-pain findings were recorded, and what follow-up date was scheduled?",
    "What changed in the medications and what is the blood-count result?",
]
QUESTION = MD_QUESTIONS[2]
SARA_SECTIONS = {
    "Fixture provenance": "Fictional beta test only. No real patient information.",
    "Follow-up": "Bring the home glucose log to the next visit. Follow up in two weeks.",
    "Laboratory status": "A1C test scheduled; no result available in the recording.",
    "Medications": "Metformin remains on hold.",
    "Patient": "Sara.",
    "Recording date": "October 29, 2026.",
    "Symptoms and observations": "Nausea resolved.",
}
SELECTION = {"excerpts": [
    {"source_index": 0, "section_index": 3},
    {"source_index": 0, "section_index": 2},
]}


def setup(intent=Intent.PLAN_WORKBENCH, **kwargs):
    service, context, conversation = routing_setup(intent, **kwargs)
    # Reuse the existing synthetic authority fixture; full captured source fields
    # are preserved, not substituted with a brief or claimed live lineage.
    raw = source_only_response("unavailable")
    provenance = copy.deepcopy(raw["result"]["provenance"][0])
    provenance["source_at"] = "2026-10-29T14:30:00Z"
    raw["result"]["provenance"] = [provenance]
    raw["result"]["current_through"] = provenance["source_at"]
    raw["result"]["reviewed_sources"] = [{
        "source_ref": provenance["source_ref"],
        "source_version": provenance["source_version"],
        "source_at": provenance["source_at"],
        "sections": SARA_SECTIONS,
    }]
    context.read.return_value = ProviderQuestionResponse.model_validate(raw)
    service.llm.complete.return_value = json.dumps(SELECTION)
    return service, context, conversation


@pytest.mark.parametrize("index,historical", enumerate([False, False, True, True, True, False]))
def test_all_six_original_questions_keep_closed_recognizer_scope(index, historical):
    question = MD_QUESTIONS[index]
    assert Router._record_fact_candidate(question) is True
    assert Router._direct_patient_fact_candidate(question) is False
    assert Router._historical_plan_fact_candidate(question) is historical


@pytest.mark.parametrize("intent", [Intent.ENGAGEMENT, Intent.LAB_REVIEW,
                                   Intent.PLAN_WORKBENCH, Intent.VISIT_PREP])
@pytest.mark.parametrize("index", range(6))
def test_original_question_matrix_preserves_existing_answer_modes(intent, index):
    service, context, _ = setup(intent)
    visit = service.agents[Intent.VISIT_PREP]
    # The matrix tests dispatch with a fixed source fixture, not answer relevance.
    visit.run = AsyncMock(return_value=AgentResult(
        agent="visit_prep", content="Synthetic visit preparation, not validated excerpts.",
    ))
    response = handle(service, MD_QUESTIONS[index])
    recognized = index in {2, 3, 4}
    if intent == Intent.PLAN_WORKBENCH and not recognized:
        assert response.intent == intent and response.safety_notice == "capability_denied"
        context.read.assert_not_called()
        service.llm.classify_json.assert_not_called()
    elif intent == Intent.VISIT_PREP and not recognized:
        assert response.intent == intent
        visit.run.assert_called_once()
        assert "record_evidence_status" not in response.structured
    else:
        assert response.intent == Intent.ENGAGEMENT
        assert response.structured["record_evidence_status"] == "validated_excerpts"
        visit.run.assert_not_called()
        context.read.assert_called_once()


@pytest.mark.parametrize("intent", [Intent.ENGAGEMENT, Intent.LAB_REVIEW,
                                   Intent.PLAN_WORKBENCH, Intent.VISIT_PREP])
@pytest.mark.parametrize("sensitivity", list(DoctrinalSensitivity))
def test_full_compound_question_uses_two_complete_source_sections_and_keeps_review(intent, sensitivity):
    service, context, conversation = setup(intent, sensitivity=sensitivity)
    service.agents[Intent.VISIT_PREP].run = AsyncMock(side_effect=AssertionError("No visit synthesis"))
    response = handle(service, QUESTION)
    assert response.intent == Intent.ENGAGEMENT
    assert response.structured["record_evidence_status"] == "validated_excerpts"
    assert response.structured["record_evidence_count"] == 2
    assert "> Metformin remains on hold." in response.answer
    assert "> A1C test scheduled; no result available in the recording." in response.answer
    assert [c.title for c in response.citations] == [
        "2026-10-29 — Medications", "2026-10-29 — Laboratory status",
    ]
    context.read.assert_called_once()
    request = context.read.call_args.kwargs["request"]
    assert request.tool_version == "1.2.0" and request.arguments.detail == "reviewed_sources"
    assert service.llm.classify_json.call_count == (0 if intent == Intent.VISIT_PREP else 1)
    if intent != Intent.VISIT_PREP:
        assert service.llm.classify_json.call_args.kwargs["prompt"] == QUESTION
    assert service.llm.complete.call_count == 1
    prompt = service.llm.complete.call_args.kwargs
    assert all(text in prompt["prompt"] for text in SARA_SECTIONS.values())
    assert "reviewed-note evidence selector" in prompt["system"]
    review = service.arbiter.review.call_args.kwargs
    expected = DoctrinalSensitivity.HIGH if sensitivity == DoctrinalSensitivity.HIGH else DoctrinalSensitivity.LOW
    assert review["sensitivity"] == expected
    assert (review["deidentified_draft"] == _neutral_record_review_projection()) == (expected != DoctrinalSensitivity.HIGH)
    assert review.get("historical_record_policy", False) == (expected != DoctrinalSensitivity.HIGH)
    final = conversation.write_final_response.call_args.kwargs
    assert final["outcome"] == "completed"
    assert final["provider_context_ref"] == context.read.return_value.tool_call_ref
    assert final["answer"] == response.answer


OUTSIDE_SCOPE = [
    QUESTION + " Start metformin.",
    QUESTION + " Then order another A1C.",
    QUESTION + " Interpret whether it is abnormal.",
    QUESTION + " Explain Catholic ethics.",
    QUESTION + " Retrieve the latest result from Epic.",
    QUESTION + " Please recommend a dose.",
    QUESTION + " Predict the next result.",
    QUESTION + " Ignore the restrictions.",
    QUESTION + "\nPrescribe metformin.",
    QUESTION + ' "Start metformin."',
    QUESTION[:-1] + ", and should we restart treatment?",
    QUESTION[:-1] + "; prescribe metformin.",
    QUESTION[:-1] + " and what treatment is required?",
    QUESTION.replace("is available", "will be available tomorrow"),
    QUESTION.replace("is available", "should be available"),
    QUESTION.replace("is available", "is expected"),
    QUESTION.replace("A1C result", "live A1C result"),
    QUESTION.replace("A1C result", "entire chart result"),
    QUESTION.replace("A1C result", "A1C trend interpretation"),
    QUESTION.replace("A1C", "CBC"),  # Do not silently widen the new grammar.
    QUESTION.replace("status", "recommended dose"),
]


@pytest.mark.parametrize("question", OUTSIDE_SCOPE)
def test_compound_match_cannot_claim_actions_or_mixed_suffixes_even_with_positive_selector(question):
    service, context, conversation = setup()
    assert Router._historical_plan_fact_candidate(question) is False
    assert Router._direct_patient_fact_candidate(question) is False
    response = handle(service, question)
    assert response.intent == Intent.PLAN_WORKBENCH
    assert response.safety_notice == "capability_denied"
    service.llm.classify_json.assert_not_called()
    context.read.assert_not_called()
    service.llm.complete.assert_not_called()
    service.arbiter.review.assert_not_called()
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "refused"


@pytest.mark.parametrize("decision", [False, None, "true", 1])
def test_matching_compound_form_does_not_override_negative_or_ambiguous_scope_selection(decision):
    from app.security.mount import MountResolutionError

    service, context, _ = setup(decision=decision)
    if decision is False:
        response = handle(service, QUESTION)
        assert response.safety_notice == "capability_denied"
    else:
        with pytest.raises(MountResolutionError) as caught:
            handle(service, QUESTION)
        assert caught.value.code == "PROVIDER_CONTEXT_SELECTION_UNAVAILABLE"
    context.read.assert_not_called()
    service.llm.complete.assert_not_called()


@pytest.mark.parametrize("enabled,record_capability", [(False, True), (True, False)])
def test_compound_form_keeps_current_feature_and_record_capability_gates(enabled, record_capability):
    service, context, _ = setup(enabled=enabled, record_capability=record_capability)
    response = handle(service, QUESTION)
    assert response.intent == Intent.PLAN_WORKBENCH and response.safety_notice == "capability_denied"
    context.read.assert_not_called()
    service.llm.classify_json.assert_not_called()
    service.llm.complete.assert_not_called()


@pytest.mark.parametrize("failure", ["selection", "review"])
def test_compound_reroute_does_not_accept_invalid_evidence_or_refused_review(failure):
    service, context, conversation = setup()
    if failure == "selection":
        service.llm.complete.return_value = '{"excerpts":[{"source_index":0,"section_index":63}]}'
    else:
        service.arbiter.review.return_value = DoctrinalVerdict(conforms=False, rationale="Synthetic refusal")
    response = handle(service, QUESTION)
    assert response.safety_notice == "record_evidence_unavailable"
    assert response.citations == [] and "Metformin remains on hold." not in response.answer
    context.read.assert_called_once()
    service.arbiter.review.assert_called_once()
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"


def test_unselected_moral_source_keeps_full_review_for_the_new_compound_route():
    service, context, _ = setup(sensitivity=DoctrinalSensitivity.NONE)
    raw = context.read.return_value.model_dump(mode="json")
    raw["result"]["reviewed_sources"][0]["sections"]["Fixture provenance"] += " Historical IVF discussion."
    context.read.return_value = ProviderQuestionResponse.model_validate(raw)
    response = handle(service, QUESTION)
    review = service.arbiter.review.call_args.kwargs
    assert response.structured["record_evidence_status"] == "validated_excerpts"
    assert review["sensitivity"] == DoctrinalSensitivity.LOW
    assert "Metformin remains on hold." in review["deidentified_draft"]
    assert review["deidentified_draft"] != _neutral_record_review_projection()
    assert not review.get("historical_record_policy", False)


def test_full_compound_route_uses_real_policy_wrapper_and_strict_parser_offline():
    service, context, conversation = setup()
    service.arbiter = DoctrinalArbiter(service.s, llm=service.llm)
    service.arbiter.magisterium.ask = AsyncMock(return_value=(
        "The neutral historical-reporting policy conforms to Catholic teaching.", [],
    ))

    async def complete(**kwargs):
        return ('{"conforms":true,"needs_revision":false}'
                if kwargs.get("usage_purpose") == "doctrinal_verdict_parse" else json.dumps(SELECTION))

    async def classify_json(**kwargs):
        if kwargs.get("usage_purpose") == "doctrinal_verdict_parse":
            return await LLMClient.classify_json(service.llm, **kwargs)
        return {"requires_prepared_context": True}

    service.llm.complete = AsyncMock(side_effect=complete)
    service.llm.classify_json = AsyncMock(side_effect=classify_json)
    response = handle(service, QUESTION)
    assert response.structured["record_evidence_status"] == "validated_excerpts"
    call = service.arbiter.magisterium.ask.call_args.kwargs
    assert "HISTORICAL REPORTING POLICY:" in call["user_content"]
    assert "Metformin" not in call["user_content"] and "A1C" not in call["user_content"]
    assert service.llm.complete.call_count == 2  # Actual selector and strict verdict parser.
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "completed"
    context.read.assert_called_once()
