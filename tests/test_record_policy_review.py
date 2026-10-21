"""Review neutral historical reporting without asking a doctrine service for labs."""

from __future__ import annotations

import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest

from app.clients.provider_evidence import EVIDENCE_REVIEW_UNAVAILABLE
from app.clients.provider_question_contract import ProviderQuestionResponse
from app.config import Settings
from app.schemas import AgentResult, DoctrinalSensitivity, DoctrinalVerdict, Intent
from app.sovereign.arbiter import DoctrinalArbiter
from app.sovereign.orchestrator import (
    _neutral_record_projection_allowed, _neutral_record_review_projection,
)
from tests.test_provider_question_context import fixture, handle, sovereign
from tests.test_provider_source_readiness import source_only_response

FAILED_QUESTION = (
    "Were potassium or creatinine ordered or resulted in the reviewed notes? "
    "Use the October 8 correction."
)
SCOPE_REFUSAL = (
    "Magisterium AI focuses on Catholic teaching and related topics, so I can’t "
    "assess or revise this medical-document wording."
)
CORRECTION = (
    "October 8 correction: potassium and creatinine were not ordered, and no "
    "results are documented. CBC order cancelled; continue losartan 25 mg daily."
)


def corrected_context(extra=""):
    raw = source_only_response("unavailable")
    last = copy.deepcopy(raw["result"]["provenance"][-1])
    last.update(source_ref="note_synthetic_correction", source_at="2026-10-08T14:30:00Z")
    raw["result"]["provenance"].append(last)
    raw["result"]["current_through"] = last["source_at"]
    raw["result"]["reviewed_sources"].append({
        "source_ref": last["source_ref"], "source_version": last["source_version"],
        "source_at": last["source_at"], "sections": {"LABS AND MEDICATION CORRECTION": CORRECTION + extra},
    })
    return ProviderQuestionResponse.model_validate(raw)


def setup(sensitivity=DoctrinalSensitivity.LOW, extra=""):
    service, read, conversation = sovereign()
    read.read.return_value = corrected_context(extra)
    service.router.route.return_value = (Intent.ENGAGEMENT, sensitivity, "")
    service.llm.complete.return_value = json.dumps({"excerpts": [{
        "source_index": 2, "section_index": 0,
    }]})
    return service, read, conversation


def test_exact_failed_query_uses_policy_posture_and_keeps_verified_answer():
    service, read, conversation = setup()
    response = handle(service, FAILED_QUESTION)
    review = service.arbiter.review.call_args.kwargs
    assert review["deidentified_draft"] == _neutral_record_review_projection()
    assert review["sensitivity"] == DoctrinalSensitivity.LOW
    assert "not ordered" in response.answer and "2026-10-08" in response.answer
    assert "neutral historical quotation" in review["deidentified_draft"]
    assert "not asked to assess clinical accuracy" in review["deidentified_draft"]
    for absent in ("potassium", "creatinine", "CBC", "losartan", "25 mg", "October", "2026-10-08",
                   read.read.return_value.tool_call_ref, "note_synthetic", "sha256_"):
        assert absent not in review["deidentified_draft"]
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "completed"
    assert conversation.write_final_response.call_args.kwargs["provider_context_ref"] == read.read.return_value.tool_call_ref


def test_none_classification_cannot_skip_review_for_neutral_chart_excerpts():
    service, _, _ = setup(DoctrinalSensitivity.NONE)
    handle(service, FAILED_QUESTION)
    assert service.arbiter.review.call_args.kwargs["sensitivity"] == DoctrinalSensitivity.LOW


@pytest.mark.parametrize("where", ["question", "selected_source", "other_source", "high"])
def test_real_arbiter_is_called_when_full_review_vetoes_the_neutral_projection(where):
    extra = " Historical note discussed IVF." if where == "selected_source" else ""
    sensitivity = DoctrinalSensitivity.HIGH if where == "high" else DoctrinalSensitivity.NONE
    service, read, conversation = setup(sensitivity, extra)
    if where == "other_source":
        raw = read.read.return_value.model_dump(mode="json")
        raw["result"]["reviewed_sources"][0]["sections"]["OTHER"] = "Historical abortion discussion."
        read.read.return_value = ProviderQuestionResponse.model_validate(raw)
    service.arbiter = DoctrinalArbiter(service.s, llm=service.llm)
    service.arbiter.magisterium.ask = AsyncMock(return_value=("The quotation conforms.", []))
    service.llm.classify_json = AsyncMock(return_value={"conforms": True, "needs_revision": False})
    question = FAILED_QUESTION + (" Explain Catholic teaching." if where == "question" else "")
    response = handle(service, question)
    service.arbiter.magisterium.ask.assert_called_once()
    reviewed = service.arbiter.magisterium.ask.call_args.kwargs["user_content"]
    assert "potassium" in reviewed and "neutral historical quotation" not in reviewed
    assert response.doctrinal_review.conforms is True
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "completed"


@pytest.mark.parametrize("where", ["question", "selected_source", "other_source", "high"])
def test_moral_or_high_inputs_keep_full_deidentified_review(where):
    extra = " Historical note discussed IVF." if where == "selected_source" else ""
    sensitivity = DoctrinalSensitivity.HIGH if where == "high" else DoctrinalSensitivity.LOW
    service, read, _ = setup(sensitivity, extra)
    question = FAILED_QUESTION + (" Explain Catholic teaching." if where == "question" else "")
    if where == "other_source":
        raw = read.read.return_value.model_dump(mode="json")
        raw["result"]["reviewed_sources"][0]["sections"]["OTHER"] = "Historical abortion discussion."
        read.read.return_value = ProviderQuestionResponse.model_validate(raw)
    response = handle(service, question)
    review = service.arbiter.review.call_args.kwargs["deidentified_draft"]
    assert "potassium" in review and "25 mg" in review
    assert review != _neutral_record_review_projection()
    assert "not ordered" in response.answer


def test_ethics_and_preparation_synthesis_do_not_use_the_neutral_projection():
    for intent in (Intent.ETHICS_CONSULT, Intent.VISIT_PREP):
        service, _, _ = setup()
        service.router.route.return_value = (intent, DoctrinalSensitivity.LOW, "")
        service.agents[intent].run = AsyncMock(return_value=AgentResult(
            agent="synthetic_agent", content="Synthesized clinical wording: potassium omitted.",
        ))
        question = ("Prepare a short outline for the upcoming patient visit."
                    if intent == Intent.VISIT_PREP else FAILED_QUESTION)
        handle(service, question)
        assert "Synthesized clinical wording: potassium omitted." in service.arbiter.review.call_args.kwargs["deidentified_draft"]


def test_summary_only_and_free_prose_keep_the_existing_full_review():
    service, read, _ = sovereign()
    read.read.return_value = ProviderQuestionResponse.model_validate(fixture())
    service.llm.complete.return_value = "Prepared summary says potassium was not ordered."
    handle(service, "Explain the available prepared summary of the prior visits.")
    review = service.arbiter.review.call_args.kwargs["deidentified_draft"]
    assert "potassium was not ordered" in review
    assert review != _neutral_record_review_projection()


def test_malformed_selection_never_receives_the_verified_quotation_projection():
    service, _, conversation = setup()
    service.llm.complete.return_value = "Unsupported current medication dose is 50 mg."
    response = handle(service, FAILED_QUESTION)
    assert response.safety_notice == "record_evidence_unavailable"
    assert service.arbiter.review.call_args.kwargs["deidentified_draft"] != _neutral_record_review_projection()
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"


def test_projection_cannot_be_enabled_by_missing_summary_or_browser_style_hints():
    args = {
        "question": FAILED_QUESTION, "answer": CORRECTION,
        "intent": Intent.ENGAGEMENT, "sensitivity": DoctrinalSensitivity.LOW,
        "result_data": {"record_evidence_status": "validated_excerpts"}, "context": None,
    }
    assert _neutral_record_projection_allowed(**args) is False
    args["context"] = ProviderQuestionResponse.model_validate(fixture()).result
    assert _neutral_record_projection_allowed(**args) is False
    args["context"] = corrected_context().result
    args["result_data"] = {}
    assert _neutral_record_projection_allowed(**args) is False


def test_patient_identifiers_and_lab_values_never_enter_the_neutral_review():
    service, _, _ = setup(extra=" Fictional email: synthetic@example.com. BP 148/92.")
    response = handle(service, FAILED_QUESTION)
    review = service.arbiter.review.call_args.kwargs["deidentified_draft"]
    assert "synthetic@example.com" not in review and "148/92" not in review
    assert "synthetic@example.com" in response.answer  # Only the authorized provider response.


@pytest.mark.parametrize("conforms,revision", [(False, SCOPE_REFUSAL), (False, None)])
def test_refusal_or_nonconformance_still_blocks_the_historical_quote(conforms, revision):
    service, _, conversation = setup()
    service.arbiter.review.return_value = DoctrinalVerdict(
        conforms=conforms, rationale="Synthetic review", revised_content=revision,
    )
    response = handle(service, FAILED_QUESTION)
    assert EVIDENCE_REVIEW_UNAVAILABLE in response.answer
    assert "25 mg" not in response.answer and "potassium" not in response.answer
    assert response.citations == []
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"


@pytest.mark.parametrize("refusal", [SCOPE_REFUSAL, "I cannot review this medical wording.",
                                    "That is outside my scope.",
                                    "I cannot provide a clinical assessment.",
                                    "I am unable to offer an assessment of the medical record.",
                                    "I am not qualified to give a medical evaluation.",
                                    "I can't perform a professional clinical review.",
                                    "I do not provide clinical assessments.",
                                    "I’m unable to answer this clinical question.",
                                    "I don't offer medical opinions.",
                                    "I cannot provide medical advice.",
                                    "I am unable to offer clinical guidance.",
                                    "I cannot give feedback on a medical record."])
def test_explicit_scope_refusal_is_not_conformance_even_if_parser_would_pass(refusal):
    arbiter = DoctrinalArbiter(Settings(_env_file=None, mock_mode=True))
    arbiter.magisterium.ask = AsyncMock(return_value=(refusal, []))
    arbiter.llm.classify_json = AsyncMock(return_value={"conforms": True, "needs_revision": False})
    verdict = asyncio.run(arbiter.review(
        deidentified_draft=_neutral_record_review_projection(), intent=Intent.ENGAGEMENT,
        sensitivity=DoctrinalSensitivity.LOW,
    ))
    assert verdict.conforms is False and verdict.revised_content == refusal
    arbiter.llm.classify_json.assert_not_called()


@pytest.mark.parametrize("conforms", [True, False])
def test_genuine_doctrinal_judgments_retain_the_existing_parse_and_revision(conforms):
    text = "The neutral reporting posture conforms." if conforms else "The draft endorses IVF; it must be revised."
    arbiter = DoctrinalArbiter(Settings(_env_file=None, mock_mode=True))
    arbiter.magisterium.ask = AsyncMock(return_value=(text, []))
    arbiter.llm.classify_json = AsyncMock(return_value={"conforms": conforms, "needs_revision": not conforms})
    verdict = asyncio.run(arbiter.review(
        deidentified_draft="Synthetic doctrine-sensitive draft.", intent=Intent.ETHICS_CONSULT,
        sensitivity=DoctrinalSensitivity.HIGH,
    ))
    assert verdict.conforms is conforms
    assert verdict.revised_content == (None if conforms else text)
    arbiter.llm.classify_json.assert_called_once()


@pytest.mark.parametrize("text", [
    "Catholic teaching cannot endorse IVF. The draft must be revised.",
    "The Church cannot provide approval for direct abortion. Revise the endorsement.",
    "I cannot recommend contraception. Frame the response according to Catholic teaching.",
])
def test_doctrinal_prohibitions_are_not_mistaken_for_task_scope_refusals(text):
    arbiter = DoctrinalArbiter(Settings(_env_file=None, mock_mode=True))
    arbiter.magisterium.ask = AsyncMock(return_value=(text, []))
    arbiter.llm.classify_json = AsyncMock(return_value={"conforms": False, "needs_revision": True})
    verdict = asyncio.run(arbiter.review(deidentified_draft="Synthetic ethical case.",
                                      intent=Intent.ETHICS_CONSULT, sensitivity=DoctrinalSensitivity.HIGH))
    assert verdict.conforms is False and verdict.revised_content == text
    arbiter.llm.classify_json.assert_called_once()
