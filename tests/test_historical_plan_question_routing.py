"""Offline scripted routing regressions; not a live-model relevance evaluation."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.config import Persona
from app.compliance.netsec import UpstreamCallError
from app.schemas import ChatRequest, DoctrinalSensitivity, Intent
from app.security.mount import MountResolutionError
from app.sovereign.router import Router
from tests.test_provider_question_context import handle, sovereign
from tests.test_record_policy_review import corrected_context


# Exact six existing staging questions. MD/RN are workflow perspectives, not
# role delegation or an authorization change. Upstream decisions are scripted.
QUESTIONS = [
    "What office blood pressure was documented on September 30, 2026? Include the visit date.",
    "What home blood pressure range was documented on October 7, 2026? Was an office BP documented at that visit?",
    "What does the October 8 correction say about the losartan dose? Distinguish it from the October 7 proposed change.",
    "What is the documented CBC order status across the October 7 and October 8 notes? Are there any CBC results?",
    "Were potassium or creatinine ordered or resulted in the reviewed notes? Use the October 8 correction.",
    "Summarize the reviewed visit timeline and identify the medication correction and outstanding documentation gaps.",
]


def setup(intent=Intent.PLAN_WORKBENCH, *, decision=True, sensitivity=DoctrinalSensitivity.LOW,
          record_capability=True, enabled=True):
    service, context, conversation = sovereign(record_capability=record_capability, enabled=enabled)
    service.router = Router(service.s, service.llm)
    service.router.route = AsyncMock(return_value=(intent, sensitivity, ""))
    service.llm.classify_json = AsyncMock(return_value={"requires_prepared_context": decision})
    context.read.return_value = corrected_context()
    # Select complete sections of all three dated synthetic sources. This is
    # scripted selection; it verifies dispatch/provenance, not live relevance.
    service.llm.complete = AsyncMock(return_value=json.dumps({"excerpts": [
        {"source_index": source_index, "section_index": section_index, "quote": quote}
        for source_index, source in enumerate(context.read.return_value.result.reviewed_sources)
        for section_index, quote in enumerate(source.sections.values())
    ]}))
    return service, context, conversation


@pytest.mark.parametrize("intent", [Intent.PLAN_WORKBENCH, Intent.LAB_REVIEW, Intent.ENGAGEMENT])
@pytest.mark.parametrize("question", QUESTIONS)
def test_six_exact_history_questions_reach_existing_source_answer_lane(intent, question):
    service, context, conversation = setup(intent)
    response = handle(service, question)
    assert response.intent == Intent.ENGAGEMENT
    assert response.safety_notice is None
    assert response.structured["record_evidence_status"] == "validated_excerpts"
    assert service.llm.classify_json.call_count == 1
    assert service.llm.classify_json.call_args.kwargs["prompt"] == question
    assert context.read.call_count == 1
    assert service.llm.complete.call_count == 1
    assert "reviewed-note evidence selector" in service.llm.complete.call_args.kwargs["system"]
    assert "148/92" in response.answer and "25 mg" in response.answer
    assert {citation.title[:10] for citation in response.citations} == {
        "2026-09-30", "2026-10-07", "2026-10-08",
    }
    service.arbiter.review.assert_called_once()
    final = conversation.write_final_response.call_args.kwargs
    assert final["outcome"] == "completed"
    assert final["provider_context_ref"] == context.read.return_value.tool_call_ref
    assert final["answer"] == response.answer
    assert set(response.presentation.suppress) == {"intent_badge", "doctrinal_badge"}


ACTION_OR_OTHER_SCOPE = [
    "What does the October 8 correction say about losartan? Should we increase the dose?",
    "What is the documented dose? Increase losartan to 50 mg.",
    "What is the documented dose: decrease it to 12.5 mg.",
    "What is the documented dose?\nStop losartan.",
    "What is the documented dose and then start losartan 50 mg?",
    "What dose was recorded? Continue losartan 50 mg.",
    "What was the previous dose? Titrate losartan to 50 mg.",
    "What was the previous dose? Taper losartan.",
    "What was the previous dose? Lower it to 12.5 mg.",
    "What was the previous dose? Raise it to 50 mg.",
    "What was the previous dose? Reduce it by half.",
    "What is the documented dose? Can you recommend a new dose?",
    "What is the documented dose? Advise me which dose to take.",
    "What is the documented dose? Switch to amlodipine.",
    "What is the documented dose? Update the care plan.",
    "What is the documented dose? Make the dose 50 mg.",
    "What is the documented dose? Put the patient on 50 mg.",
    "What is the documented dose? Go with 50 mg.",
    "What is the documented dose? Would you use 50 mg?",
    "What is the documented dose? Use the October 8 correction to change treatment.",
    "What is the documented CBC order status? Please order another CBC.",
    "What is the documented CBC order status: order another CBC.",
    "What is the documented CBC order status? Cancel the order.",
    "Which prior notes mention fatigue? Then prescribe iron.",
    "Which prior notes mention fatigue? Refer the patient to cardiology.",
    "What does the prior note say? Send a new prescription.",
    "What does the prior note say? Schedule a follow-up.",
    "What is the documented dose? Fetch the current Athena record.",
    "What was the prior dose? Query the entire chart for changes.",
    "What do the reviewed labs show? Interpret whether they are abnormal.",
    "What is the appropriate dose based on the reviewed notes?",
    "What is the best dose based on the prior notes?",
    "What is the safest dose based on the recorded history?",
    "What is the dose to take today based on the prior notes?",
    "What is the correct dose based on the prior notes?",
    "What is the recorded dose and would you use 50 mg?",
    "What is the recorded dose and could we try 50 mg?",
    "What is the documented dose? Skip losartan.",
    "What should the current plan be based on the prior notes?",
    "What do the prior notes say about Catholic ethics?",
    "Explain losartan and hypertension.",
    "What is losartan used for?",
    "Summarize a new care plan for hypertension.",
    "Prepare the patient's next care plan from reviewed notes.",
    "Change losartan to 50 mg based on the previous notes.",
]


@pytest.mark.parametrize("question", ACTION_OR_OTHER_SCOPE)
def test_plan_actions_and_other_requests_keep_hidden_tool_refusal_even_with_positive_selector(question):
    service, context, conversation = setup()
    response = handle(service, question)
    assert response.intent == Intent.PLAN_WORKBENCH
    assert response.safety_notice == "capability_denied"
    service.llm.classify_json.assert_not_called()
    context.read.assert_not_called()
    service.llm.complete.assert_not_called()
    service.arbiter.review.assert_not_called()
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "refused"


@pytest.mark.parametrize("decision", [False, None, "true", 1])
def test_false_or_ambiguous_secondary_decision_never_reads_a_record(decision):
    service, context, conversation = setup(decision=decision)
    if decision is False:
        response = handle(service, QUESTIONS[2])
        assert response.intent == Intent.PLAN_WORKBENCH
        assert response.safety_notice == "capability_denied"
        assert conversation.write_final_response.call_args.kwargs["outcome"] == "refused"
    else:
        with pytest.raises(MountResolutionError) as caught:
            handle(service, QUESTIONS[2])
        assert caught.value.code == "PROVIDER_CONTEXT_SELECTION_UNAVAILABLE"
        conversation.write_final_response.assert_not_called()
    context.read.assert_not_called()
    service.llm.complete.assert_not_called()
    service.arbiter.review.assert_not_called()


@pytest.mark.parametrize("record_capability,enabled", [(False, True), (True, False)])
def test_original_canonical_record_grant_and_feature_are_required(record_capability, enabled):
    service, context, _ = setup(record_capability=record_capability, enabled=enabled)
    response = handle(service, QUESTIONS[2])
    assert response.intent == Intent.PLAN_WORKBENCH
    assert response.safety_notice == "capability_denied"
    service.llm.classify_json.assert_not_called()
    context.read.assert_not_called()
    service.llm.complete.assert_not_called()


def test_secondary_upstream_failure_is_content_free_without_record_read():
    service, context, conversation = setup()
    service.llm.classify_json.side_effect = UpstreamCallError("Synthetic upstream unavailable")
    response = handle(service, QUESTIONS[2])
    assert response.safety_notice == "upstream_unavailable"
    assert response.structured == {} and response.citations == []
    assert "25 mg" not in response.answer and "50 mg" not in response.answer
    context.read.assert_not_called()
    service.llm.complete.assert_not_called()
    service.arbiter.review.assert_not_called()
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"


def test_rerouting_does_not_bypass_invalid_selection_or_review_rejection():
    from app.schemas import DoctrinalVerdict

    for failure in ("selection", "review"):
        service, context, conversation = setup()
        if failure == "selection":
            service.llm.complete.return_value = "An unsupported new dose is 75 mg."
        else:
            service.arbiter.review.return_value = DoctrinalVerdict(
                conforms=False, rationale="Synthetic required-review rejection",
            )
        response = handle(service, QUESTIONS[2])
        assert response.intent == Intent.ENGAGEMENT
        assert response.safety_notice == "record_evidence_unavailable"
        assert "25 mg" not in response.answer and "75 mg" not in response.answer
        assert response.citations == []
        assert context.read.call_count == 1
        service.arbiter.review.assert_called_once()
        assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"


@pytest.mark.parametrize("sensitivity,expected", [
    (DoctrinalSensitivity.NONE, DoctrinalSensitivity.LOW),
    (DoctrinalSensitivity.LOW, DoctrinalSensitivity.LOW),
    (DoctrinalSensitivity.HIGH, DoctrinalSensitivity.HIGH),
])
def test_rerouted_question_preserves_required_review(sensitivity, expected):
    service, context, _ = setup(sensitivity=sensitivity)
    response = handle(service, QUESTIONS[2])
    assert response.intent == Intent.ENGAGEMENT
    assert context.read.call_count == 1
    assert service.arbiter.review.call_args.kwargs["sensitivity"] == expected


def test_secondary_selector_uses_current_question_and_never_browser_authority_hints():
    service, _, _ = setup()
    request = ChatRequest(persona=Persona.PROVIDER, message=QUESTIONS[2], metadata={
        "requires_prepared_context": True, "plan-workbench:run": True,
        "source_ref": "browser_sentinel", "prior_notes": "browser_sentinel",
    })
    assert asyncio.run(service.router.needs_provider_context(request, Intent.PLAN_WORKBENCH)) is True
    call = service.llm.classify_json.call_args.kwargs
    assert call["prompt"] == QUESTIONS[2]
    assert "browser_sentinel" not in str(call)
    assert "a new or revised care plan" in call["system"]
    assert "cannot perform the external skill" in call["system"]


def test_emergency_still_precedes_misclassified_plan_and_record_reads():
    service, context, conversation = setup()
    response = handle(service, "I have chest pain and cannot breathe. What was the prior plan?")
    assert response.intent == Intent.EMERGENCY
    service.router.route.assert_not_called()
    service.llm.classify_json.assert_not_called()
    context.read.assert_not_called()
    conversation.write_user_accepted.assert_not_called()
