"""Offline prompt alignment; scripted assessments do not prove vendor acceptance."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.clients.provider_evidence import (
    EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE, EVIDENCE_REVIEW_UNAVAILABLE, EVIDENCE_UNAVAILABLE,
)
from app.clients.provider_question_contract import ProviderQuestionResponse
from app.config import Persona, Settings
from app.schemas import AgentResult, ChatRequest, DoctrinalSensitivity, Intent
from app.sovereign.arbiter import (
    DoctrinalArbiter, _HISTORICAL_POLICY_REVIEW_QUESTION, _REVIEW_SYSTEM,
)
from app.sovereign.orchestrator import _neutral_record_review_projection
from tests.test_historical_plan_question_routing import setup as routing_setup
from tests.test_provider_question_context import TURN_ID, fixture, handle, sovereign
from tests.test_provider_source_readiness import source_only_response

CBC_QUESTION = (
    "What is the documented CBC order status across the October 7 and October 8 notes? "
    "Are there any CBC results?"
)
ASSESSMENT = "The neutral historical-reporting policy conforms to Catholic teaching."
SELECTION = {"excerpts": [
    {"source_index": 1, "section_index": 2},
    {"source_index": 1, "section_index": 5},
    {"source_index": 2, "section_index": 3},
    {"source_index": 2, "section_index": 5},
]}


def captured_context():
    """Preserve every captured clinical section, with mocked authority handles."""
    notes = json.loads((Path(__file__).parent / "fixtures"
                       / "rendered-synthetic-reviewed-notes-20261020.json").read_text())["notes"]
    raw = source_only_response("unavailable")
    prototype = raw["result"]["provenance"][0]
    raw["result"]["provenance"] = []
    raw["result"]["reviewed_sources"] = []
    for index, note in enumerate(notes):
        provenance = copy.deepcopy(prototype)
        provenance.update(source_ref=f"note_synthetic_{index}", source_at=note["date"] + "T14:30:00Z")
        raw["result"]["provenance"].append(provenance)
        raw["result"]["reviewed_sources"].append({
            "source_ref": provenance["source_ref"], "source_version": provenance["source_version"],
            "source_at": provenance["source_at"], "sections": note["sections"],
        })
    raw["result"]["current_through"] = raw["result"]["provenance"][-1]["source_at"]
    return raw


def setup(*, tier=DoctrinalSensitivity.LOW, verdict='{"conforms":true,"needs_revision":false}',
          assessment=ASSESSMENT, selection=SELECTION, raw=None):
    service, context, conversation = sovereign()
    context.read.return_value = ProviderQuestionResponse.model_validate(raw or captured_context())
    service.router.route.return_value = (Intent.ENGAGEMENT, tier, "")

    async def complete(**kwargs):
        return verdict if kwargs.get("usage_purpose") == "doctrinal_verdict_parse" else json.dumps(selection)

    service.llm.complete = AsyncMock(side_effect=complete)
    service.arbiter = DoctrinalArbiter(service.s, llm=service.llm)
    service.arbiter.magisterium.ask = AsyncMock(return_value=(assessment, []))
    return service, context, conversation


@pytest.mark.parametrize("tier", [DoctrinalSensitivity.LOW, DoctrinalSensitivity.NONE])
def test_actual_cbc_sections_use_policy_question_with_one_required_review(tier):
    service, context, conversation = setup(tier=tier)
    response = handle(service, CBC_QUESTION)
    call = service.arbiter.magisterium.ask.call_args.kwargs
    assert call == {
        "system": _REVIEW_SYSTEM,
        "user_content": ("HISTORICAL REPORTING POLICY:\n" + _neutral_record_review_projection()
                         + "\n\n" + _HISTORICAL_POLICY_REVIEW_QUESTION),
        "usage_purpose": "doctrinal_review",
    }
    service.arbiter.magisterium.ask.assert_called_once()
    assert service.llm.complete.call_count == 2  # One selector plus one actual strict parser.
    parser = service.llm.complete.call_args.kwargs
    assert parser["usage_purpose"] == "doctrinal_verdict_parse" and parser["max_tokens"] == 300
    assert parser["prompt"] == f"Reviewer assessment:\n{ASSESSMENT}\n\nFields: conforms (bool), needs_revision (bool)."
    for absent in ("DRAFT ANSWER:", "give the corrected version", "CBC", "losartan", "148/92",
                   "2026-10-07", "2026-10-08", "note_synthetic_", context.read.return_value.tool_call_ref):
        assert absent not in call["user_content"]
    assert response.structured["record_evidence_status"] == "validated_excerpts"
    assert response.structured["record_evidence_count"] == 4
    assert "Order CBC to evaluate ongoing fatigue." in response.answer
    assert "CBC order cancelled; no completion or result documented" in response.answer
    assert "No CBC result is documented." in response.answer
    assert "Cancel CBC order to avoid unnecessary testing" in response.answer
    assert "does not establish" in response.answer  # A quotation does not assert current order state.
    assert [c.title.split(" — ")[0] for c in response.citations] == [
        "2026-10-07", "2026-10-07", "2026-10-08", "2026-10-08",
    ]
    assert response.citations[2].title == "2026-10-08 — Labs - fasting, 02/12/2026"
    assert all(c.url is None for c in response.citations)
    assert response.doctrinal_review.conforms is True
    final = conversation.write_final_response.call_args.kwargs
    assert final["outcome"] == "completed" and final["answer"] == response.answer
    assert final["provider_context_ref"] == context.read.return_value.tool_call_ref
    assert final["history_context_ref"] == "history_context_fictional"


@pytest.mark.parametrize("veto", ["high", "question", "selected", "unselected", "title"])
def test_cbc_moral_or_high_boundary_retains_full_review_and_original_wrapper(veto):
    raw = captured_context()
    question = CBC_QUESTION
    if veto == "question":
        question += " Explain Catholic ethics."
    elif veto == "selected":
        raw["result"]["reviewed_sources"][2]["sections"]["P - PLAN"] += " Historical IVF discussion."
    elif veto == "unselected":
        raw["result"]["reviewed_sources"][0]["sections"]["OTHER"] = "Historical end-of-life discussion."
    elif veto == "title":
        raw["result"]["reviewed_sources"][0]["sections"]["Catholic teaching"] = "Historical topic."
    # NONE must become LOW before moral vetoes, rather than skip the arbiter.
    service, _, _ = setup(raw=raw, tier=DoctrinalSensitivity.HIGH if veto == "high" else DoctrinalSensitivity.NONE)
    response = handle(service, question)
    call = service.arbiter.magisterium.ask.call_args.kwargs
    assert call["system"] == _REVIEW_SYSTEM
    assert call["user_content"].startswith("DRAFT ANSWER:\nSelected reviewed-note excerpts")
    assert call["user_content"].endswith("Does this conform? If not, give the corrected version.")
    assert "CBC order cancelled" in call["user_content"]
    assert "HISTORICAL REPORTING POLICY:" not in call["user_content"]
    service.arbiter.magisterium.ask.assert_called_once()
    assert response.doctrinal_review.conforms is True


@pytest.mark.parametrize("verdict,assessment,lead,parse_calls", [
    ('{"conforms":false,"needs_revision":true}', "The reporting policy must be revised.", EVIDENCE_REVIEW_UNAVAILABLE, 1),
    ('{"conforms":true}', "I cannot review this clinical question.", EVIDENCE_REVIEW_UNAVAILABLE, 0),
    ('{"conforms":"true"}', ASSESSMENT, EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE, 1),
    ('{"conforms":true,"needs_revision":true}', ASSESSMENT, EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE, 1),
    ('{"conforms":false,"conforms":true}', ASSESSMENT, EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE, 1),
    ('[]', ASSESSMENT, EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE, 1),
    ('{}', ASSESSMENT, EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE, 1),
    ('{"conforms":true}', " \t\n", EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE, 0),
    ('{"conforms":true}', None, EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE, 0),
])
def test_policy_wrapper_never_accepts_negative_refused_or_unusable_review(verdict, assessment, lead, parse_calls):
    service, context, conversation = setup(verdict=verdict, assessment=assessment)
    response = handle(service, CBC_QUESTION)
    assert service.arbiter.magisterium.ask.call_count == 1
    assert service.llm.complete.call_count == 1 + parse_calls
    assert service.arbiter.magisterium.ask.call_args.kwargs["user_content"].startswith("HISTORICAL REPORTING POLICY:")
    assert response.answer.startswith(lead)
    assert response.safety_notice == "record_evidence_unavailable"
    assert response.doctrinal_review is None and response.citations == []
    assert set(response.presentation.suppress) == {"intent_badge", "doctrinal_badge", "citations", "structured_cards"}
    assert "CBC order cancelled" not in response.answer and "Order CBC" not in response.answer
    assert context.read.return_value.result.source_notice() in response.answer
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"


@pytest.mark.parametrize("assessment,verdict", [
    (ASSESSMENT, '{"conforms":true}'),
    ("The reporting policy must be revised.", '{"conforms":false}'),
    ("I cannot review this clinical question.", '{"conforms":true}'),
    (ASSESSMENT, '{"conforms":"true"}'),
    (None, '{"conforms":true}'),
])
def test_failed_selection_keeps_original_wrapper_required_review_and_primary_failure(assessment, verdict):
    service, _, conversation = setup(selection={"excerpts": []}, assessment=assessment, verdict=verdict)
    response = handle(service, CBC_QUESTION)
    call = service.arbiter.magisterium.ask.call_args.kwargs
    assert call["user_content"].startswith("DRAFT ANSWER:\n" + EVIDENCE_UNAVAILABLE)
    assert response.answer.startswith(EVIDENCE_UNAVAILABLE)
    assert response.safety_notice == "record_evidence_unavailable"
    service.arbiter.magisterium.ask.assert_called_once()
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"


@pytest.mark.parametrize("intent,question", [
    (Intent.PLAN_WORKBENCH, "Order a CBC now."),
    (Intent.PLAN_WORKBENCH, "What CBC should I order next?"),
    (Intent.PLAN_WORKBENCH, "What plan is needed based on prior notes?"),
    (Intent.PLAN_WORKBENCH, CBC_QUESTION + " Then order a CBC."),
    (Intent.LAB_REVIEW, "Fetch the CBC result from the live EHR."),
])
def test_new_care_or_external_skill_does_not_recover_into_historical_policy_review(intent, question):
    service, context, _ = routing_setup(intent)
    response = handle(service, question)
    assert response.intent == intent and response.safety_notice == "capability_denied"
    context.read.assert_not_called()
    service.arbiter.review.assert_not_called()
    service.llm.complete.assert_not_called()


@pytest.mark.parametrize("intent", [Intent.ENGAGEMENT, Intent.VISIT_PREP, Intent.ETHICS_CONSULT])
def test_summary_and_synthesized_answers_keep_original_wrapper_even_with_browser_hint(intent):
    service, context, _ = setup()
    context.read.return_value = ProviderQuestionResponse.model_validate(fixture())
    service.router.route.return_value = (intent, DoctrinalSensitivity.HIGH if intent == Intent.ETHICS_CONSULT else DoctrinalSensitivity.LOW, "")
    service.agents[intent].run = AsyncMock(return_value=AgentResult(agent="synthetic", content="SYNTHESIS_SENTINEL"))
    # A broad overview stays synthesized; the dated CBC lookup requires exact excerpts.
    asyncio.run(service.handle(ChatRequest(
        persona=Persona.PROVIDER, client_turn_id=TURN_ID,
        message="Summarize the prepared visit overview for the upcoming encounter.",
        metadata={"historical_record_policy": True, "record_evidence_status": "validated_excerpts"},
    ), mount_context_token="mount_synthetic_001", client_turn_id=TURN_ID))
    call = service.arbiter.magisterium.ask.call_args.kwargs
    assert call["user_content"] == "DRAFT ANSWER:\nSYNTHESIS_SENTINEL\n\nDoes this conform? If not, give the corrected version."
    service.arbiter.magisterium.ask.assert_called_once()
    service.agents[intent].run.assert_awaited_once()


@pytest.mark.parametrize("intent,tier,option", [
    (Intent.ENGAGEMENT, DoctrinalSensitivity.HIGH, True),
    (Intent.LAB_REVIEW, DoctrinalSensitivity.LOW, True),
    (Intent.VISIT_PREP, DoctrinalSensitivity.LOW, True),
    (Intent.ETHICS_CONSULT, DoctrinalSensitivity.HIGH, True),
    (Intent.ENGAGEMENT, DoctrinalSensitivity.LOW, False),
    (Intent.ENGAGEMENT, DoctrinalSensitivity.LOW, "true"),
    (Intent.ENGAGEMENT, DoctrinalSensitivity.LOW, 1),
])
def test_internal_option_does_not_change_other_review_wrappers(intent, tier, option):
    arbiter = DoctrinalArbiter(Settings(_env_file=None, mock_mode=True))
    arbiter.magisterium.ask = AsyncMock(return_value=(ASSESSMENT, []))
    arbiter.llm.complete = AsyncMock(return_value='{"conforms":true}')
    asyncio.run(arbiter.review(deidentified_draft="VALUE_SAFE_SENTINEL", intent=intent,
                              sensitivity=tier, historical_record_policy=option))
    assert arbiter.magisterium.ask.call_args.kwargs == {
        "system": _REVIEW_SYSTEM,
        "user_content": "DRAFT ANSWER:\nVALUE_SAFE_SENTINEL\n\nDoes this conform? If not, give the corrected version.",
        "usage_purpose": "doctrinal_review",
    }


def test_none_default_bypass_is_unchanged_and_option_is_not_a_new_wire_field():
    arbiter = DoctrinalArbiter(Settings(_env_file=None, mock_mode=True))
    arbiter.magisterium.ask = AsyncMock()
    arbiter.llm.complete = AsyncMock()
    verdict = asyncio.run(arbiter.review(deidentified_draft="Synthetic logistics.", intent=Intent.SMALL_TALK,
                                       sensitivity=DoctrinalSensitivity.NONE, historical_record_policy=True))
    assert verdict.conforms is True
    arbiter.magisterium.ask.assert_not_called()
    arbiter.llm.complete.assert_not_called()
    assert "historical_record_policy" not in ChatRequest.model_json_schema()["properties"]


CLINICAL_LIMITATIONS = [
    "I cannot assess clinical accuracy.",
    "I can't evaluate clinical correctness.",
    "However, I am unable to assess clinical accuracy or rewrite clinician-note wording.",
    "Do not assess clinical accuracy or rewrite clinician-note wording.",
]


@pytest.mark.parametrize("limitation", CLINICAL_LIMITATIONS)
def test_positive_policy_judgment_with_clinical_only_limit_retains_actual_strict_parse(limitation):
    assessment = ASSESSMENT + " " + limitation
    service, context, conversation = setup(assessment=assessment)
    response = handle(service, CBC_QUESTION)
    assert response.structured["record_evidence_status"] == "validated_excerpts"
    assert "CBC order cancelled" in response.answer
    assert response.doctrinal_review.conforms is True
    service.arbiter.magisterium.ask.assert_called_once()
    assert service.llm.complete.call_count == 2
    assert service.llm.complete.call_args.kwargs["prompt"] == (
        f"Reviewer assessment:\n{assessment}\n\nFields: conforms (bool), needs_revision (bool)."
    )
    call = service.arbiter.magisterium.ask.call_args.kwargs
    assert "CBC" not in call["user_content"] and "losartan" not in call["user_content"]
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "completed"
    assert conversation.write_final_response.call_args.kwargs["provider_context_ref"] == context.read.return_value.tool_call_ref


@pytest.mark.parametrize("assessment", [
    "I cannot assess clinical accuracy.",  # No policy judgment for the parser to interpret.
    '"The reporting policy conforms." I cannot assess clinical accuracy.',
    "If this reporting policy conforms. I cannot assess clinical accuracy.",
    "The user says this reporting policy conforms. I cannot assess clinical accuracy.",
    "The reporting policy does not conform. I cannot assess clinical accuracy.",
    ASSESSMENT + " I cannot assess the reporting policy.",
    ASSESSMENT + " I cannot assess Catholic teaching or clinical accuracy.",
    ASSESSMENT + " I cannot assess clinical accuracy or doctrinal conformance.",
    ASSESSMENT + " Clinical accuracy is outside my scope; so is this policy.",
    ASSESSMENT + " I cannot assess clinical accuracy, and I cannot judge policy conformance.",
    ASSESSMENT + " I cannot review this clinical question.",
    ASSESSMENT + " I cannot determine whether this policy conforms. I cannot assess clinical accuracy.",
    ASSESSMENT + " I will not judge the policy. I cannot assess clinical accuracy.",
    ASSESSMENT + " I decline to judge doctrine. I cannot assess clinical accuracy.",
    ASSESSMENT + " This policy does not conform to Catholic teaching. I cannot assess clinical accuracy.",
])
def test_ambiguous_quoted_conditional_and_mixed_policy_refusals_remain_closed(assessment):
    service, _, conversation = setup(assessment=assessment)
    response = handle(service, CBC_QUESTION)
    assert response.answer.startswith(EVIDENCE_REVIEW_UNAVAILABLE)
    assert response.doctrinal_review is None and response.citations == []
    service.arbiter.magisterium.ask.assert_called_once()
    assert service.llm.complete.call_count == 1  # Selector only; no positive parser can override refusal.
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"


@pytest.mark.parametrize("verdict,lead", [
    ('{"conforms":false}', EVIDENCE_REVIEW_UNAVAILABLE),
    ('{"conforms":"true"}', EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE),
    ('{"conforms":true,"needs_revision":true}', EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE),
    ('{"conforms":false,"conforms":true}', EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE),
])
def test_clinical_only_limit_never_infers_conformance_or_repairs_parser(verdict, lead):
    service, _, conversation = setup(assessment=ASSESSMENT + " I cannot assess clinical accuracy.", verdict=verdict)
    response = handle(service, CBC_QUESTION)
    assert response.answer.startswith(lead) and response.doctrinal_review is None
    assert "CBC order cancelled" not in response.answer
    service.arbiter.magisterium.ask.assert_called_once()
    assert service.llm.complete.call_count == 2
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"


@pytest.mark.parametrize("intent,tier,option", [
    (Intent.ENGAGEMENT, DoctrinalSensitivity.HIGH, True),
    (Intent.ETHICS_CONSULT, DoctrinalSensitivity.HIGH, True),
    (Intent.VISIT_PREP, DoctrinalSensitivity.LOW, True),
    (Intent.ENGAGEMENT, DoctrinalSensitivity.LOW, False),
    (Intent.ENGAGEMENT, DoctrinalSensitivity.LOW, "true"),
])
def test_clinical_scope_distinction_is_internal_low_historical_policy_only(intent, tier, option):
    arbiter = DoctrinalArbiter(Settings(_env_file=None, mock_mode=True))
    arbiter.magisterium.ask = AsyncMock(return_value=(ASSESSMENT + " I cannot assess clinical accuracy.", []))
    arbiter.llm.classify_json = AsyncMock(return_value={"conforms": True})
    verdict = asyncio.run(arbiter.review(deidentified_draft="Synthetic only", intent=intent,
                                       sensitivity=tier, historical_record_policy=option))
    assert verdict.conforms is False and verdict._adjudication_disposition == "scope_refused"
    arbiter.magisterium.ask.assert_called_once()
    arbiter.llm.classify_json.assert_not_called()
