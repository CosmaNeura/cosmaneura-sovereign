"""Actual pure pipeline/arbiter paths with explicitly scripted synthetic models.

These cases establish source binding and guards, not actual semantic or vendor
acceptance. No live/DB/network operations or real patient data are used.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest

from app.clients.actual_record_review_frame_contract import is_actual_record_review_frame
from app.clients.provider_evidence import EVIDENCE_UNAVAILABLE, validated_evidence
from app.clients.record_review_context import (
    bind_record_review_context, record_review_projection,
)
from app.config import Settings
from app.schemas import DoctrinalSensitivity, Intent
from app.sovereign.arbiter import DoctrinalArbiter
from tests.test_complete_record_fact_question_routing import (
    EXPECTED_QUOTES, MD_QUESTIONS, setup as md_setup,
)
from tests.test_provider_question_context import handle
from tests.test_provider_factual_timeline import (
    CLINICAL_HEADINGS, setup_timeline, QUESTION as TIMELINE_QUESTION,
)
from tests.test_record_review_context import descriptor, deny_external_io  # noqa: F401


def prepare(*, index=2, intent=Intent.PLAN_WORKBENCH, sensitivity=DoctrinalSensitivity.LOW):
    service, read, writer = md_setup(intent, index=index, sensitivity=sensitivity)
    ctx = read.read.return_value.result
    ctx._reviewed_excerpts_version = "1.0.0"
    selection = json.loads(service.llm.complete.return_value)
    spans = tuple((row["source_index"], row["section_index"]) for row in selection["excerpts"])
    selection["review_context"] = descriptor(ctx, spans)
    service.llm.complete.return_value = json.dumps(selection)
    return service, read, writer, spans


def frame(posture="historical_description"):
    service, read, _, spans = prepare()
    ctx = read.read.return_value.result
    selection = json.loads(service.llm.complete.return_value)
    for row in selection["review_context"]["selected_context"]:
        row["posture"] = posture
    result = validated_evidence(json.dumps({"excerpts": selection["excerpts"]}), ctx,
                                agent="synthetic", data={})
    bound = bind_record_review_context(selection["review_context"], result=result,
                                      context=ctx, question=MD_QUESTIONS[2], selected_spans=spans)
    return record_review_projection(bound, context=ctx, question=MD_QUESTIONS[2])


@pytest.mark.parametrize("index", range(6))
@pytest.mark.parametrize("intent", [Intent.ENGAGEMENT, Intent.LAB_REVIEW, Intent.PLAN_WORKBENCH, Intent.VISIT_PREP])
def test_original_complete_queries_keep_two_quotes_and_actual_context_review(index, intent):
    service, read, writer, spans = prepare(index=index, intent=intent)
    result = handle(service, MD_QUESTIONS[index])
    review = service.arbiter.review.call_args.kwargs
    assert review.get("represented_record_answer") is True
    assert "historical_record_policy" not in review
    assert is_actual_record_review_frame(review["deidentified_draft"])
    assert "policy for all possible future excerpts" in review["deidentified_draft"]
    for text in EXPECTED_QUOTES[index]:
        assert "> " + text in result.answer and text not in review["deidentified_draft"]
    assert service.llm.complete.await_count == 1
    assert "review_context" in service.llm.complete.call_args.kwargs["system"]
    assert service.llm.complete.call_args.kwargs["max_tokens"] == 4096
    final = writer.write_final_response.call_args.kwargs
    assert final["outcome"] == "completed" and final["answer"] == result.answer
    assert tuple((item.source_index, item.section_index) for item in final["reviewed_excerpts"].excerpts) == spans
    assert "review_context" not in result.model_dump_json()
    assert "record_evidence_spans" not in result.model_dump_json()
    assert read.read.await_count == 1


@pytest.mark.parametrize("failure", ["missing", "unknown", "moral", "action", "coverage", "duplicate", "extra"])
def test_unsafe_semantic_context_cannot_use_policy_or_clinical_fallback(failure):
    service, _, writer, _ = prepare()
    selection = json.loads(service.llm.complete.return_value)
    descriptor_value = selection["review_context"]
    if failure == "missing":
        del selection["review_context"]
    elif failure == "unknown":
        descriptor_value["question_use"] = "unknown"
    elif failure == "moral":
        descriptor_value["source_context"][0]["scope"] = "moral_claim"
    elif failure == "action":
        descriptor_value["selected_context"][0]["posture"] = "practical_assistance"
    elif failure == "coverage":
        descriptor_value["source_context"][0]["section_indexes"].pop()
    elif failure == "duplicate":
        descriptor_value["selected_context"].append(deepcopy(descriptor_value["selected_context"][0]))
    else:
        descriptor_value["claim"] = "Synthetic unsupported clinical text."
    service.llm.complete.return_value = json.dumps(selection)
    result = handle(service, MD_QUESTIONS[2])
    assert result.safety_notice == "record_evidence_unavailable"
    review = service.arbiter.review.call_args.kwargs
    assert review["deidentified_draft"] == EVIDENCE_UNAVAILABLE
    assert "represented_record_answer" not in review and "historical_record_policy" not in review
    assert writer.write_final_response.call_args.kwargs["outcome"] == "unavailable"
    assert "reviewed_excerpts" not in writer.write_final_response.call_args.kwargs
    assert result.citations == []
    service.arbiter.review.assert_awaited_once()


@pytest.mark.parametrize("change", ["source", "namespace", "answer"])
def test_changes_across_required_review_cannot_donate_a_different_final(change, monkeypatch):
    service, read, writer, _ = prepare()
    import app.sovereign.orchestrator as orchestration
    original_projection = orchestration.record_review_projection
    captured = []

    def capture(result, **kwargs):
        captured.append(result)
        return original_projection(result, **kwargs)

    monkeypatch.setattr(orchestration, "record_review_projection", capture)
    original_verdict = service.arbiter.review.return_value

    async def mutate(**_kwargs):
        bound = captured[0]
        if change == "source":
            read.read.return_value.result.reviewed_sources[0].sections["Medications"] += " Changed synthetic data."
        elif change == "namespace":
            bound._reviewed_excerpts.excerpts.reverse()
        else:
            bound.content += " Changed unsupported answer."
        return original_verdict

    service.arbiter.review.side_effect = mutate
    result = handle(service, MD_QUESTIONS[2])
    assert result.safety_notice == "record_evidence_unavailable"
    final = writer.write_final_response.call_args.kwargs
    assert final["outcome"] == "unavailable" and "reviewed_excerpts" not in final
    assert "Changed" not in result.answer and result.citations == []


def arbiter(assessment="This represented answer conforms to Catholic teaching."):
    value = DoctrinalArbiter(Settings(_env_file=None, mock_mode=True))
    value.magisterium.ask = AsyncMock(return_value=(assessment, []))
    value.llm.classify_json = AsyncMock(return_value={"conforms": True, "needs_revision": False})
    return value


@pytest.mark.parametrize("assessment", [
    "This answer conforms to Catholic teaching. I cannot assess clinical accuracy.",
    "The represented particular answer conforms, but I cannot review clinical accuracy.",
    "This displayed answer is consistent with Catholic teaching because it reports past documentation. I cannot evaluate clinical correctness.",
])
def test_actual_answer_judgment_with_only_clinical_limit_reaches_unchanged_parser(assessment):
    value = arbiter(assessment)
    verdict = asyncio.run(value.review(deidentified_draft=frame(), intent=Intent.ENGAGEMENT,
                                     sensitivity=DoctrinalSensitivity.LOW, represented_record_answer=True))
    assert verdict.conforms is True
    value.llm.classify_json.assert_awaited_once()
    sent = value.magisterium.ask.call_args.kwargs
    assert sent["user_content"].startswith("REPRESENTED ACTUAL ANSWER CONTEXT:")
    assert "HISTORICAL REPORTING POLICY:" not in sent["user_content"]
    assert "Evaluate the reporting policy only" not in sent["user_content"]
    assert "Do not assume conformance" in sent["system"]
    assert value.llm.classify_json.call_args.kwargs["reject_duplicate_keys"] is True


def test_bound_source_instruction_frame_reaches_actual_review_without_clinical_text():
    value = arbiter()
    text = frame("quoted_historical_instruction")
    assert is_actual_record_review_frame(text)
    assert "source-authored historical instructions" in text
    verdict = asyncio.run(value.review(deidentified_draft=text, intent=Intent.ENGAGEMENT,
                                     sensitivity=DoctrinalSensitivity.LOW, represented_record_answer=True))
    assert verdict.conforms is True
    value.magisterium.ask.assert_awaited_once()
    value.llm.classify_json.assert_awaited_once()


@pytest.mark.parametrize("assessment", [
    "This answer conforms if no moral issue is present.",
    "This answer conforms. Unless other excerpts endorse an immoral action.",
    "This answer conforms provided the underlying wording is historical.",
    "This answer conforms. I cannot assess moral conformance.",
    "This reporting policy conforms. I cannot assess clinical accuracy.",
    "I cannot judge this particular answer without its actual excerpts.",
])
def test_actual_mode_conditions_anywhere_scope_refusal_and_policy_only_judgment_are_denied(assessment):
    value = arbiter(assessment)
    verdict = asyncio.run(value.review(deidentified_draft=frame(), intent=Intent.ENGAGEMENT,
                                     sensitivity=DoctrinalSensitivity.LOW, represented_record_answer=True))
    assert verdict.conforms is False and verdict._adjudication_disposition == "scope_refused"
    value.llm.classify_json.assert_not_awaited()


@pytest.mark.parametrize("condition", ["if", "unless", "provided", "assuming", "conditionally",
                                    "subject to", "as long as", "so long as", "on condition that",
                                    "contingent on", "depending on", "only when", "only after"])
def test_actual_answer_condition_in_separate_sentence_cannot_reach_positive_parser(condition):
    value = arbiter("This answer conforms. Acceptance is " + condition + " future revision.")
    result = asyncio.run(value.review(deidentified_draft=frame(), intent=Intent.ENGAGEMENT,
                                    sensitivity=DoctrinalSensitivity.LOW, represented_record_answer=True))
    assert result.conforms is False and result._adjudication_disposition == "scope_refused"
    value.llm.classify_json.assert_not_awaited()


@pytest.mark.parametrize("flag", [{"conforms": "true"}, {"conforms": True, "extra": 1},
                                  {"conforms": True, "needs_revision": True}, {}, {"conforms": False}])
def test_actual_mode_does_not_manufacture_conformance_from_parser(flag):
    value = arbiter()
    value.llm.classify_json.return_value = flag
    verdict = asyncio.run(value.review(deidentified_draft=frame(), intent=Intent.ENGAGEMENT,
                                     sensitivity=DoctrinalSensitivity.LOW, represented_record_answer=True))
    assert verdict.conforms is False


@pytest.mark.parametrize("kind", ["raw", "modified", "none", "high", "intent", "both"])
def test_internal_actual_mode_rejects_unbound_text_wrong_tier_or_conflicting_task_before_call(kind):
    value = arbiter()
    kwargs = dict(deidentified_draft=frame(), intent=Intent.ENGAGEMENT,
                  sensitivity=DoctrinalSensitivity.LOW, represented_record_answer=True)
    if kind == "raw":
        kwargs["deidentified_draft"] = "Synthetic medication wording."
    elif kind == "modified":
        kwargs["deidentified_draft"] += " Synthetic injected text."
    elif kind == "none":
        kwargs["sensitivity"] = DoctrinalSensitivity.NONE
    elif kind == "high":
        kwargs["sensitivity"] = DoctrinalSensitivity.HIGH
    elif kind == "intent":
        kwargs["intent"] = Intent.ETHICS_CONSULT
    else:
        kwargs["historical_record_policy"] = True
    with pytest.raises(ValueError):
        asyncio.run(value.review(**kwargs))
    value.magisterium.ask.assert_not_awaited()


def test_timeline_assesses_once_with_real_existing_work_deadline_and_retains_original_indices():
    service, read, writer = setup_timeline()
    ctx = read.read.return_value.result
    ctx._reviewed_excerpts_version = "1.0.0"
    spans = tuple((i, j) for i, source in sorted(enumerate(ctx.reviewed_sources), key=lambda pair: pair[1].source_at)
                  for j, (title, text) in enumerate(source.sections.items())
                  if text.strip() and title in CLINICAL_HEADINGS)
    service.llm.complete.return_value = json.dumps(descriptor(ctx, spans))
    result = handle(service, TIMELINE_QUESTION)
    assert result.structured["record_evidence_mode"] == "reviewed_timeline"
    assert service.llm.complete.await_count == 1
    call = service.llm.complete.call_args.kwargs
    assert call["max_tokens"] == 4096 and call["usage_purpose"] == "record_review_context"
    assert service.arbiter.review.call_args.kwargs["represented_record_answer"] is True
    assert tuple((row.source_index, row.section_index) for row in writer.write_final_response.call_args.kwargs["reviewed_excerpts"].excerpts) == spans


def test_old_policy_and_high_full_task_wording_stays_unchanged():
    value = arbiter("This policy conforms to Catholic teaching.")
    asyncio.run(value.review(deidentified_draft="Fixed policy.", intent=Intent.ENGAGEMENT,
                             sensitivity=DoctrinalSensitivity.LOW, historical_record_policy=True))
    assert value.magisterium.ask.call_args.kwargs["user_content"].startswith("HISTORICAL REPORTING POLICY:")
    value = arbiter()
    asyncio.run(value.review(deidentified_draft="Synthetic full review.", intent=Intent.ENGAGEMENT,
                             sensitivity=DoctrinalSensitivity.HIGH))
    assert value.magisterium.ask.call_args.kwargs["user_content"] == "DRAFT ANSWER:\nSynthetic full review.\n\nDoes this conform? If not, give the corrected version."
