"""Synthetic source/egress/deadline regressions; no vendor acceptance claim."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import socket
import subprocess
from time import monotonic
from unittest.mock import AsyncMock

import pytest

from app.clients.llm import LLMClient
from app.clients.provider_evidence import validated_evidence, reviewed_timeline_evidence
from app.clients.provider_question_contract import ProviderQuestionResponse
from app.clients.record_review_context import (
    MAX_TIMELINE_ASSESSMENT_TOKENS,
    RecordReviewAgentResult,
    RecordReviewContextUnavailable,
    assess_timeline_record_review_context,
    bind_record_review_context,
    parse_record_review_descriptor,
    record_review_projection,
)
from app.clients.reviewed_excerpts_contract import ReviewedExcerpt, ReviewedExcerpts
from app.config import ComplianceProfile, Persona, Settings


@pytest.fixture(autouse=True)
def deny_external_io(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("External I/O is forbidden in this source-only test.")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)


def context():
    path = (Path(__file__).parents[1] / "contracts/fixtures/provider-question-context-v1.1"
            / "response-ready.json")
    return ProviderQuestionResponse.model_validate(json.loads(path.read_text())).result


QUESTION = "What does the fictional prior note document?"
SPANS = ((0, 0),)


def canonical(ctx=None, spans=SPANS):
    ctx = ctx or context()
    raw = json.dumps({"excerpts": [
        {"source_index": source, "section_index": section} for source, section in spans
    ]})
    result = validated_evidence(raw, ctx, agent="synthetic", data={})
    assert result.data["record_evidence_status"] == "validated_excerpts"
    return result


def descriptor(ctx=None, spans=SPANS):
    ctx = ctx or context()
    return {
        "schema_version": "1.0.0",
        "question_use": "historical_lookup",
        "source_context": [
            {"source_index": index, "section_indexes": list(range(len(source.sections))),
             "scope": "historical_documentation"}
            for index, source in enumerate(ctx.reviewed_sources)
        ],
        "selected_context": [
            {"source_index": source, "section_index": section,
             "posture": "historical_description"}
            for source, section in spans
        ],
    }


def bind(ctx=None, answer=None, value=None, question=QUESTION, spans=SPANS):
    ctx = ctx or context()
    return bind_record_review_context(
        value or descriptor(ctx, spans), result=answer or canonical(ctx, spans),
        context=ctx, question=question, selected_spans=spans,
    )


def test_native_canonical_quote_is_bound_but_no_clinical_token_enters_frame_or_private_wire():
    ctx = context()
    ctx.reviewed_sources[0].sections["Objective"] = (
        "Synthetic office BP 148/92 mmHg; fictional contact synthetic@example.test. "
        "Fictional source says https://synthetic.example.test/record; fifty units."
    )
    result = bind(ctx)
    frame = record_review_projection(result, context=ctx, question=QUESTION)
    for forbidden in ("148/92", "mmHg", "synthetic@example.test", "fifty units",
                      "synthetic.example.test", "2026-08-12", "note_synthetic_001",
                      ctx.source_version_digest):
        assert forbidden not in frame
    assert "148/92" in result.content
    assert "not a doctrinal verdict" in frame
    assert "When this represented context is insufficient" in frame
    assert "_record_review_context" not in result.model_dump_json()
    assert "review_context" not in result.data
    assert result.model_dump() == canonical(ctx).model_dump()


def test_historical_instruction_is_represented_as_source_posture_not_new_medical_advice():
    value = descriptor()
    value["selected_context"][0]["posture"] = "quoted_historical_instruction"
    result = bind(value=value)
    frame = record_review_projection(result, context=context(), question=QUESTION)
    assert "source-authored historical instructions" in frame
    assert "not an assistant-authored instruction to carry it out" in frame
    assert "moral permissibility from a negative keyword screen" in frame


def negotiated_context():
    ctx = context()
    ctx._reviewed_excerpts_version = "1.0.0"
    return ctx


def test_combined_emitter_wrapper_preserves_only_typed_private_namespace_without_aliasing():
    ctx = negotiated_context()
    original = canonical(ctx)
    expected = original._reviewed_excerpts.model_dump(mode="json")
    original._untrusted_model_private = {"url": "https://synthetic.example.test"}
    wrapped = bind(ctx, answer=original)
    assert wrapped._reviewed_excerpts.model_dump(mode="json") == expected
    assert wrapped._reviewed_excerpts is not original._reviewed_excerpts
    assert wrapped._reviewed_excerpts.excerpts[0] is not original._reviewed_excerpts.excerpts[0]
    assert not hasattr(wrapped, "_untrusted_model_private")
    assert wrapped.model_dump() == original.model_dump()
    assert "reviewed_excerpts" not in wrapped.model_dump_json()
    original._reviewed_excerpts.excerpts[0].section_index = 1
    assert wrapped._reviewed_excerpts.model_dump(mode="json") == expected
    assert record_review_projection(wrapped, context=ctx, question=QUESTION)


@pytest.mark.parametrize("mutation", [
    "missing", "coordinate", "ordinal", "mode", "digest", "boolean_index",
    "dict_association", "version_removed", "version_unknown",
])
def test_combined_emitter_mutable_private_namespace_cannot_diverge_from_binding(mutation):
    ctx = negotiated_context()
    result = bind(ctx)
    namespace = result._reviewed_excerpts
    if mutation == "missing":
        result._reviewed_excerpts = None
    elif mutation == "coordinate":
        namespace.excerpts[0].section_index = 1
    elif mutation == "ordinal":
        namespace.excerpts[0].ordinal = 2
    elif mutation == "mode":
        namespace.mode = "timeline"
    elif mutation == "digest":
        namespace.enumeration_sha256 = "f" * 64
    elif mutation == "boolean_index":
        namespace.excerpts[0] = ReviewedExcerpt.model_construct(
            ordinal=1, source_index=True, section_index=0,
        )
    elif mutation == "dict_association":
        result._reviewed_excerpts = namespace.model_dump()
    elif mutation == "version_removed":
        ctx._reviewed_excerpts_version = None
    else:
        ctx._reviewed_excerpts_version = "9.9.9"
    with pytest.raises(RecordReviewContextUnavailable):
        record_review_projection(result, context=ctx, question=QUESTION)


def test_same_day_identical_source_text_cannot_substitute_emitter_coordinates():
    ctx = negotiated_context()
    second = ctx.reviewed_sources[0].model_copy(deep=True)
    second.source_ref = "note_synthetic_002"
    ctx.reviewed_sources.append(second)
    first = canonical(ctx, ((0, 0),))
    other = canonical(ctx, ((1, 0),))
    assert first.content == other.content
    assert first._reviewed_excerpts.enumeration_sha256 == other._reviewed_excerpts.enumeration_sha256
    with pytest.raises(RecordReviewContextUnavailable):
        bind(ctx, answer=first, value=descriptor(ctx, ((1, 0),)), spans=((1, 0),))
    wrapped = bind(ctx, answer=first)
    wrapped._reviewed_excerpts = ReviewedExcerpts.model_validate(
        other._reviewed_excerpts.model_dump(),
    )
    with pytest.raises(RecordReviewContextUnavailable):
        record_review_projection(wrapped, context=ctx, question=QUESTION)


def test_negotiated_renderer_namespace_cannot_be_missing_at_initial_binding():
    ctx = negotiated_context()
    result = canonical(ctx)
    result._reviewed_excerpts = None
    with pytest.raises(RecordReviewContextUnavailable):
        bind(ctx, answer=result)


def test_combined_timeline_namespace_mode_and_rendered_original_order_are_preserved():
    ctx = negotiated_context()
    result = reviewed_timeline_evidence(ctx, agent="synthetic", data={})
    spans = tuple((item.source_index, item.section_index)
                  for item in result._reviewed_excerpts.excerpts)
    wrapped = bind_record_review_context(
        descriptor(ctx, spans), result=result, context=ctx, question=QUESTION,
        selected_spans=spans, selection_mode="timeline",
    )
    assert wrapped._reviewed_excerpts.model_dump() == result._reviewed_excerpts.model_dump()
    assert record_review_projection(wrapped, context=ctx, question=QUESTION)


@pytest.mark.parametrize("mutation", [
    "question_whitespace", "question_text", "answer", "unselected_section", "selected_section",
    "section_order", "source_version", "full_context_metadata", "evidence_status", "evidence_count",
])
def test_actual_question_full_enumeration_and_canonical_answer_mutation_invalidates(mutation):
    ctx = context()
    result = bind(ctx)
    question = QUESTION
    if mutation == "question_whitespace":
        question += " "
    elif mutation == "question_text":
        question += " Then act on the prior plan."
    elif mutation == "answer":
        result.content += " New unrelated sentence."
    elif mutation == "unselected_section":
        ctx.reviewed_sources[0].sections["Plan"] = "Changed unselected synthetic plan."
    elif mutation == "selected_section":
        ctx.reviewed_sources[0].sections["Objective"] = "Changed synthetic observation."
    elif mutation == "section_order":
        source = ctx.reviewed_sources[0]
        source.sections = dict(reversed(list(source.sections.items())))
    elif mutation == "source_version":
        ctx.reviewed_sources[0].source_version += 1
    elif mutation == "full_context_metadata":
        ctx.generated_at = ctx.generated_at.replace(minute=ctx.generated_at.minute + 1)
    elif mutation == "evidence_status":
        result.data["record_evidence_status"] = "unavailable"
    else:
        result.data["record_evidence_count"] = 2
    with pytest.raises(RecordReviewContextUnavailable):
        record_review_projection(result, context=ctx, question=question)


@pytest.mark.parametrize("field,value", [
    ("question_use", "clinical_advice_or_action"), ("question_use", "moral_guidance"),
    ("question_use", "mixed"), ("question_use", "unknown"),
    ("scope", "moral_claim"), ("scope", "action_enabling"),
    ("scope", "mixed"), ("scope", "unknown"),
    ("posture", "moral_endorsement"), ("posture", "practical_assistance"),
    ("posture", "mixed"), ("posture", "unknown"),
])
def test_uncertain_or_unsafe_interpretation_cannot_authorize_neutral_frame(field, value):
    raw = descriptor()
    if field == "question_use":
        raw[field] = value
    elif field == "scope":
        raw["source_context"][0][field] = value
    else:
        raw["selected_context"][0][field] = value
    with pytest.raises(RecordReviewContextUnavailable):
        bind(value=raw)


@pytest.mark.parametrize("mutation", [
    "missing_source", "duplicate_source", "extra_source", "missing_section", "section_order",
    "duplicate_section", "foreign_section", "boolean_source", "boolean_section", "missing_selected",
    "duplicate_selected", "foreign_selected", "extra_field", "source_ref", "conforms", "url",
])
def test_complete_source_and_selected_index_contract_is_closed(mutation):
    raw = descriptor()
    sources = raw["source_context"]
    selected = raw["selected_context"]
    if mutation == "missing_source":
        sources.clear()
    elif mutation == "duplicate_source":
        sources.append(copy.deepcopy(sources[0]))
    elif mutation == "extra_source":
        sources.append({"source_index": 1, "section_indexes": [0], "scope": "historical_documentation"})
    elif mutation == "missing_section":
        sources[0]["section_indexes"] = [0]
    elif mutation == "section_order":
        sources[0]["section_indexes"].reverse()
    elif mutation == "duplicate_section":
        sources[0]["section_indexes"] = [0, 0]
    elif mutation == "foreign_section":
        sources[0]["section_indexes"] = [0, 2]
    elif mutation == "boolean_source":
        sources[0]["source_index"] = False
    elif mutation == "boolean_section":
        sources[0]["section_indexes"][0] = False
    elif mutation == "missing_selected":
        selected.clear()
    elif mutation == "duplicate_selected":
        selected.append(copy.deepcopy(selected[0]))
    elif mutation == "foreign_selected":
        selected[0]["section_index"] = 1
    elif mutation == "extra_field":
        raw["rationale"] = "Untrusted patient text"
    elif mutation == "source_ref":
        selected[0]["source_ref"] = "model_claimed_ref"
    elif mutation == "conforms":
        raw["conforms"] = True
    else:
        sources[0]["url"] = "https://model.example.test/visit"
    with pytest.raises(RecordReviewContextUnavailable) as err:
        bind(value=raw)
    assert str(err.value) == "The actual record-review context is unavailable."


@pytest.mark.parametrize("raw", [
    '{"schema_version":"1.0.0","schema_version":"1.0.0"}',
    '{"source_context":[{"source_index":0,"source_index":0}]}',
    '{"source_context":NaN}', '{"source_context":Infinity}', '[]', 'null',
    '```json\n{}\n```', '{"conforms":true}', 'x' * 40_001,
])
def test_descriptor_json_rejects_duplicate_nonfinite_unclosed_and_oversize_shapes(raw):
    with pytest.raises(RecordReviewContextUnavailable):
        parse_record_review_descriptor(raw)


def test_valid_descriptor_roundtrip_does_not_mutate_source_or_model_lists_after_binding():
    raw = descriptor()
    parsed = parse_record_review_descriptor(json.dumps(raw))
    assert parsed.model_dump() == raw
    result = bind(value=parsed)
    parsed.selected_context[0].posture = "unknown"
    parsed.source_context[0].section_indexes.clear()
    assert "historical descriptions" in record_review_projection(
        result, context=context(), question=QUESTION,
    )


def test_browser_or_model_data_cannot_forge_private_review_context():
    result = canonical()
    result.data["review_context"] = descriptor()
    result.data["historical_record_policy"] = True
    with pytest.raises(RecordReviewContextUnavailable):
        record_review_projection(result, context=context(), question=QUESTION)
    fabricated = RecordReviewAgentResult.model_validate(result.model_dump())
    with pytest.raises(RecordReviewContextUnavailable):
        record_review_projection(fabricated, context=context(), question=QUESTION)


@pytest.mark.parametrize("spans", [((False, 0),), ((0, False),), ((0, 0), (0, 0)),
                                  ((1, 0),), ((0, 2),), (), ((0, 0, 1),)])
def test_unvalidated_expected_selection_never_creates_binding(spans):
    with pytest.raises(RecordReviewContextUnavailable):
        bind_record_review_context(descriptor(), result=canonical(), context=context(),
                                   question=QUESTION, selected_spans=spans)


def timeline_setup():
    ctx = context()
    answer = reviewed_timeline_evidence(ctx, agent="synthetic", data={})
    spans = ((0, 0), (0, 1))
    llm = LLMClient(Settings(_env_file=None, mock_mode=True))
    llm.complete = AsyncMock(return_value=json.dumps(descriptor(ctx, spans)))
    return ctx, answer, spans, llm


def assess(ctx, answer, spans, llm, deadline=None, profile=None):
    return asyncio.run(assess_timeline_record_review_context(
        llm=llm, profile=profile or ComplianceProfile(Persona.PROVIDER), result=answer,
        context=ctx, question=QUESTION, selected_spans=spans,
        deadline=monotonic() + 2 if deadline is None else deadline,
    ))


def test_timeline_uses_one_BAA_assessment_of_actual_full_context_and_canonical_answer():
    ctx, answer, spans, llm = timeline_setup()
    result = assess(ctx, answer, spans, llm)
    llm.complete.assert_awaited_once()
    call = llm.complete.call_args.kwargs
    assert call["max_tokens"] == MAX_TIMELINE_ASSESSMENT_TOKENS
    assert call["usage_call_kind"] == "classification"
    assert call["usage_purpose"] == "record_review_context"
    assert not call.get("use_doctrinal_mcp")
    assert QUESTION in call["prompt"] and answer.content in call["prompt"]
    assert "Synthetic office BP 148/92 mmHg" in call["prompt"]
    assert "source_index" in call["prompt"] and "section_indexes" in call["prompt"]
    assert "note_synthetic_001" not in call["prompt"]
    assert "148/92" not in record_review_projection(result, context=ctx, question=QUESTION)


@pytest.mark.parametrize("deadline", [0, -1, float("inf"), float("nan"), True])
def test_expired_or_invalid_absolute_deadline_does_not_start_model_call(deadline):
    ctx, answer, spans, llm = timeline_setup()
    with pytest.raises(RecordReviewContextUnavailable):
        assess(ctx, answer, spans, llm, deadline=deadline)
    llm.complete.assert_not_called()


def test_timeline_timeout_cancels_single_assessment_without_retry_or_new_deadline():
    ctx, answer, spans, llm = timeline_setup()
    async def delayed(**_kwargs):
        await asyncio.sleep(1)
    llm.complete = AsyncMock(side_effect=delayed)
    start = monotonic()
    with pytest.raises(RecordReviewContextUnavailable):
        assess(ctx, answer, spans, llm, deadline=start + .01)
    assert monotonic() - start < .5
    llm.complete.assert_awaited_once()


@pytest.mark.parametrize("changed", ["source", "answer"])
def test_timeline_rejects_context_or_answer_changed_during_model_assessment(changed):
    ctx, answer, spans, llm = timeline_setup()
    async def altered(**_kwargs):
        if changed == "source":
            ctx.reviewed_sources[0].sections["Plan"] = "Changed synthetic plan."
        else:
            answer.content += " Changed answer."
        return json.dumps(descriptor(ctx, spans))
    llm.complete = AsyncMock(side_effect=altered)
    with pytest.raises(RecordReviewContextUnavailable):
        assess(ctx, answer, spans, llm)
    llm.complete.assert_awaited_once()


def test_timeline_non_BAA_and_non_provider_inputs_are_refused_before_model_call():
    for provider in (False, True):
        ctx, answer, spans, llm = timeline_setup()
        if provider:
            llm.s = Settings(_env_file=None, mock_mode=False, baa_attested=False)
        profile = ComplianceProfile(Persona.PROVIDER if provider else Persona.PATIENT)
        with pytest.raises(RecordReviewContextUnavailable):
            assess(ctx, answer, spans, llm, profile=profile)
        llm.complete.assert_not_called()


def test_full_bounded_timeline_can_cover_all_sections_without_selector_eight_span_limit():
    ctx = context()
    ctx.reviewed_sources[0].sections = {
        f"Synthetic clinical section {i}": f"Fictional historical section {i}." for i in range(64)
    }
    spans = tuple((0, i) for i in range(64))
    answer = reviewed_timeline_evidence(ctx, agent="synthetic", data={})
    llm = LLMClient(Settings(_env_file=None, mock_mode=True))
    llm.complete = AsyncMock(return_value=json.dumps(descriptor(ctx, spans)))
    result = assess(ctx, answer, spans, llm)
    assert result.data["record_evidence_count"] == 64
    assert "ACTUAL DISPLAYED" in record_review_projection(result, context=ctx, question=QUESTION)
    with pytest.raises(RecordReviewContextUnavailable):
        bind_record_review_context(descriptor(ctx, spans), result=answer, context=ctx,
                                   question=QUESTION, selected_spans=spans)
