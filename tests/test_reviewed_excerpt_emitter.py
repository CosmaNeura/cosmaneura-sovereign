"""Offline synthetic source/transport cases; no model, DB or live acceptance.

Coordinates refer to the actual received DTO enumeration, even when rendering
sorts a timeline or identical same-day sections cannot distinguish source IDs.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.clients.platform_conversation import PlatformConversationClient
from app.clients.provider_evidence import (
    EVIDENCE_UNAVAILABLE, TIMELINE_UNAVAILABLE, enumeration_digest,
    evidence_prompt, reviewed_timeline_evidence, validated_evidence,
)
from app.clients.provider_question import ProviderQuestionClient
from app.clients.provider_question_contract import (
    ProviderQuestionArguments, ProviderQuestionRequest, ProviderQuestionResponse,
)
from app.clients.reviewed_excerpts_contract import ReviewedExcerpt, ReviewedExcerpts
from app.clients.session_prep import SessionPrepDeliveryError
from app.schemas import DoctrinalVerdict, Intent
from app.sovereign.turn_deadline import ACTIVE_PROVIDER_TURN
from tests.test_provider_question_context import fixture, handle, headers, sovereign, turn
from tests.test_provider_reviewed_sources import reviewed_response
from tests.test_provider_source_readiness import source_only_response

HEADER = "X-Cosmaneura-Reviewed-Excerpts"
QUESTION = "What office blood pressure was documented on September 30?"


def request(version="1.2.0"):
    return ProviderQuestionRequest(tool_version=version, arguments=ProviderQuestionArguments(
        source_turn_ref="turn_synthetic_001",
        detail="prep_summary" if version == "1.0.0" else "reviewed_sources",
    ))


def read_response(raw, support=(), *, version="1.2.0", change_headers=None):
    seen = []
    response_headers = list(headers().items()) + [(HEADER, value) for value in support]
    if change_headers is not None:
        response_headers = change_headers(response_headers)

    def handler(incoming):
        seen.append(incoming)
        return httpx.Response(200, headers=response_headers, json=raw)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = ProviderQuestionClient(
                platform_base_url="https://platform.example", client=http,
                workload_token_provider=AsyncMock(return_value="synthetic-workload-token"),
            )
            return await client.read(turn=turn(), request=request(version))

    return scenario(), seen


def context(*, supported=True):
    result = ProviderQuestionResponse.model_validate(source_only_response("unavailable")).result
    if supported:
        result._reviewed_excerpts_version = "1.0.0"
    return result


def selected(ctx, coordinates=((0, 0), (1, 1))):
    return validated_evidence(json.dumps({"excerpts": [
        {"source_index": source, "section_index": section} for source, section in coordinates
    ]}), ctx, agent="synthetic_agent", data={"output_format": "chat"})


def indices(result):
    return [(item.source_index, item.section_index) for item in result._reviewed_excerpts.excerpts]


@pytest.mark.parametrize("version", ["1.1.0", "1.2.0"])
def test_header_opt_in_is_private_and_wire_context_and_prompt_are_unchanged(version):
    raw = reviewed_response() if version == "1.1.0" else source_only_response("unavailable")
    legacy_call, legacy_seen = read_response(raw, version=version)
    new_call, new_seen = read_response(raw, ["1.0.0"], version=version)
    legacy, negotiated = asyncio.run(legacy_call), asyncio.run(new_call)
    assert legacy.result._reviewed_excerpts_version is None
    assert negotiated.result._reviewed_excerpts_version == "1.0.0"
    assert legacy.model_dump(mode="json") == negotiated.model_dump(mode="json") == raw
    assert legacy.result.prompt_text() == negotiated.result.prompt_text()
    assert evidence_prompt(legacy.result) == evidence_prompt(negotiated.result)
    assert legacy_seen[0].content == new_seen[0].content
    assert HEADER.casefold() not in {key.casefold() for key in new_seen[0].headers}
    assert "reviewed_excerpts" not in negotiated.model_dump_json()


@pytest.mark.parametrize("support", [
    ["1.0.1"], [""], ["1.0.0", "1.0.0"], ["1.0.0", "1.0.1"],
    ["1.0.0, 1.0.0"], [" 1.0.0"], ["1.0.0 "],
])
def test_unknown_or_ambiguous_support_is_schema_failure_without_legacy_retry(support):
    call, seen = read_response(source_only_response(), support)
    with pytest.raises(SessionPrepDeliveryError) as error:
        asyncio.run(call)
    assert (error.value.category, error.value.status_code, error.value.retryable) == ("schema", 503, False)
    assert len(seen) == 1


@pytest.mark.parametrize("raw,version", [
    (fixture(), "1.0.0"), (source_only_response(sources_ready=False), "1.2.0"),
    (reviewed_response("pending"), "1.1.0"), (reviewed_response("unavailable"), "1.1.0"),
])
def test_support_header_cannot_activate_legacy_or_nonready_context(raw, version):
    call, seen = read_response(raw, ["1.0.0"], version=version)
    with pytest.raises(SessionPrepDeliveryError):
        asyncio.run(call)
    assert len(seen) == 1


@pytest.mark.parametrize("change", [
    lambda values: [(k, "wrong-trace" if k == "X-Trace-ID" else v) for k, v in values],
    lambda values: values + [("X-Trace-ID", turn().trace_id)],
    lambda values: [(k, "text/plain" if k == "Content-Type" else v) for k, v in values],
    lambda values: [(k, "wrong-turn" if k == "X-Cosmaneura-Client-Turn-ID" else v) for k, v in values],
])
def test_valid_opt_in_does_not_bypass_existing_transport_guards(change):
    call, _ = read_response(source_only_response(), ["1.0.0"], change_headers=change)
    with pytest.raises(SessionPrepDeliveryError):
        asyncio.run(call)


def test_body_hint_and_version_mismatch_cannot_negotiate_support():
    raw = source_only_response()
    raw["result"]["reviewed_excerpts_version"] = "1.0.0"
    call, _ = read_response(raw)
    with pytest.raises(SessionPrepDeliveryError):
        asyncio.run(call)
    call, seen = read_response(reviewed_response(), ["1.0.0"])
    with pytest.raises(SessionPrepDeliveryError):
        asyncio.run(call)
    assert len(seen) == 1


def test_selected_envelope_and_public_data_equal_legacy_with_original_indices():
    old = context(supported=False)
    new = context()
    first, second = selected(old, ((1, 1), (0, 0))), selected(new, ((1, 1), (0, 0)))
    assert first._reviewed_excerpts is None
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert indices(second) == [(1, 1), (0, 0)]
    namespace = second._reviewed_excerpts.model_dump(mode="json")
    assert namespace["mode"] == "selected"
    assert [item["ordinal"] for item in namespace["excerpts"]] == [1, 2]
    assert all(set(item) == {"ordinal", "source_index", "section_index"} for item in namespace["excerpts"])
    assert "note_synthetic" not in json.dumps(namespace) and "url" not in json.dumps(namespace)


@pytest.mark.parametrize("reorder", ["source", "section", "both"])
def test_identical_same_day_quotes_keep_actual_received_indices_and_digest(reorder):
    ctx = context()
    for source in ctx.reviewed_sources:
        source.source_at = ctx.reviewed_sources[0].source_at
        source.sections = {"O - OBJECTIVE": "Same fictional quote.", "P - PLAN": "Same fictional quote."}
    baseline = selected(ctx, ((0, 0),))
    changed = deepcopy(ctx)
    if reorder in {"source", "both"}:
        changed.reviewed_sources.reverse()
    if reorder in {"section", "both"}:
        for source in changed.reviewed_sources:
            source.sections = dict(reversed(list(source.sections.items())))
    # Keep selecting the original first source's objective section after reordering.
    coordinates = ((1 if reorder in {"source", "both"} else 0,
                    1 if reorder in {"section", "both"} else 0),)
    reproduced = selected(changed, coordinates)
    assert reproduced.content == baseline.content
    assert reproduced.citations == baseline.citations
    assert indices(reproduced) == list(coordinates)
    assert reproduced._reviewed_excerpts.enumeration_sha256 != baseline._reviewed_excerpts.enumeration_sha256


@pytest.mark.parametrize("change", ["time", "ref", "version", "unselected", "unicode"])
def test_digest_binds_full_visit_timestamp_identity_and_unselected_sections(change):
    ctx = context()
    before = selected(ctx, ((0, 0),))
    changed = deepcopy(ctx)
    source = changed.reviewed_sources[1]
    if change == "time":
        source.source_at += timedelta(hours=1)
    elif change == "ref":
        source.source_ref = "note_synthetic_different"
    elif change == "version":
        source.source_version += 1
    else:
        source.sections["P - PLAN"] += " Fictional café Δ." if change == "unicode" else " Extra fixture text."
    after = selected(changed, ((0, 0),))
    assert before.content == after.content
    assert before._reviewed_excerpts.enumeration_sha256 != after._reviewed_excerpts.enumeration_sha256
    matrix = [[s.source_ref, s.source_version, s.source_at.isoformat(), list(s.sections.items())]
              for s in changed.reviewed_sources]
    assert enumeration_digest(changed) == hashlib.sha256(json.dumps(
        matrix, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def test_timeline_sort_retains_unsorted_dto_and_section_indices_with_background_gaps():
    ctx = context()
    ctx.reviewed_sources.reverse()
    for source in ctx.reviewed_sources:
        source.sections = {"Allergies": "Fictional background.", **source.sections}
    legacy = deepcopy(ctx)
    legacy._reviewed_excerpts_version = None
    result = reviewed_timeline_evidence(ctx, agent="synthetic_agent", data={})
    original = reviewed_timeline_evidence(legacy, agent="synthetic_agent", data={})
    assert result.model_dump(mode="json") == original.model_dump(mode="json")
    assert indices(result) == [(1, 1), (1, 2), (0, 1), (0, 2)]
    assert result._reviewed_excerpts.mode == "timeline"
    assert result._reviewed_excerpts.enumeration_sha256 == enumeration_digest(ctx)
    assert result.content.index("2026-09-30") < result.content.index("2026-10-07")


def test_same_day_timeline_uses_full_time_then_stable_dto_order_for_ties():
    ctx = context()
    ctx.reviewed_sources.reverse()
    for source in ctx.reviewed_sources:
        source.source_at = ctx.reviewed_sources[0].source_at
        source.sections = {"O - OBJECTIVE": "Identical fictional quote."}
    result = reviewed_timeline_evidence(ctx, agent="synthetic_agent", data={})
    assert indices(result) == [(0, 0), (1, 0)]
    original_digest = result._reviewed_excerpts.enumeration_sha256
    ctx.reviewed_sources.reverse()
    reversed_result = reviewed_timeline_evidence(ctx, agent="synthetic_agent", data={})
    assert result.content == reversed_result.content
    assert original_digest != reversed_result._reviewed_excerpts.enumeration_sha256
    ctx.reviewed_sources[0].source_at += timedelta(hours=1)
    assert indices(reviewed_timeline_evidence(ctx, agent="synthetic_agent", data={})) == [(1, 0), (0, 0)]


@pytest.mark.parametrize("coordinates", [(), ((0, 0), (0, 0)), ((4, 0),), ((0, 63),)])
def test_invalid_selected_evidence_never_carries_optional_namespace(coordinates):
    result = selected(context(), coordinates)
    assert result.content == EVIDENCE_UNAVAILABLE and result._reviewed_excerpts is None


def test_overbound_timeline_cannot_attach_partial_coordinates():
    ctx = context()
    ctx.reviewed_sources[1].sections["P - PLAN"] = "x" * 3001
    result = reviewed_timeline_evidence(ctx, agent="synthetic_agent", data={})
    assert result.content == TIMELINE_UNAVAILABLE and result._reviewed_excerpts is None


def namespace():
    return selected(context())._reviewed_excerpts


@pytest.mark.parametrize("field,value", [
    ("ordinal", True), ("ordinal", 2), ("source_index", "0"), ("source_index", 5),
    ("source_index", -1), ("section_index", 64), ("section_index", 0.0),
    ("source_ref", "note_synthetic_001"), ("url", "https://untrusted.example/"),
])
def test_closed_coordinates_reject_coercion_gaps_and_claimed_authority(field, value):
    raw = namespace().model_dump(mode="json")
    raw["excerpts"][0][field] = value
    with pytest.raises(ValidationError):
        ReviewedExcerpts.model_validate(raw)


@pytest.mark.parametrize("field,value", [
    ("schema_version", "1.1.0"), ("mode", "all"), ("enumeration_sha256", "A" * 64),
    ("enumeration_sha256", b"a" * 64), ("source_ref", "note_synthetic_001"),
    ("excerpts", []),
])
def test_closed_namespace_rejects_unknown_modes_versions_and_extra_authority(field, value):
    raw = namespace().model_dump(mode="json")
    raw[field] = value
    with pytest.raises(ValidationError):
        ReviewedExcerpts.model_validate(raw)


def test_duplicate_coordinates_and_selected_and_timeline_budgets():
    raw = namespace().model_dump(mode="json")
    raw["excerpts"][1].update(source_index=0, section_index=0)
    with pytest.raises(ValidationError):
        ReviewedExcerpts.model_validate(raw)
    raw["excerpts"] = [dict(ordinal=i + 1, source_index=0, section_index=i) for i in range(9)]
    with pytest.raises(ValidationError):
        ReviewedExcerpts.model_validate(raw)
    raw["mode"] = "timeline"
    raw["excerpts"] = [dict(ordinal=i + 1, source_index=0, section_index=i) for i in range(64)]
    assert len(ReviewedExcerpts.model_validate(raw).excerpts) == 64
    raw["excerpts"].append(dict(ordinal=65, source_index=1, section_index=0))
    with pytest.raises(ValidationError):
        ReviewedExcerpts.model_validate(raw)


def pipeline(*, supported=True, intent=Intent.ENGAGEMENT):
    service, read, conversation = sovereign()
    response = ProviderQuestionResponse.model_validate(source_only_response("unavailable"))
    if supported:
        response.result._reviewed_excerpts_version = "1.0.0"
    read.read.return_value = response
    service.router.route.return_value = (intent, service.router.route.return_value[1], "")
    service.llm.complete.return_value = json.dumps({"excerpts": [
        {"source_index": 0, "section_index": 0}, {"source_index": 1, "section_index": 1},
    ]})
    return service, read, conversation


@pytest.mark.parametrize("initial", [Intent.ENGAGEMENT, Intent.LAB_REVIEW, Intent.PLAN_WORKBENCH, Intent.VISIT_PREP])
def test_actual_pipeline_forwards_only_reviewed_supported_evidence_without_prompt_or_answer_changes(initial):
    legacy, _, old_writer = pipeline(supported=False, intent=initial)
    service, read, writer = pipeline(intent=initial)
    original, result = handle(legacy, QUESTION), handle(service, QUESTION)
    old_final, final = old_writer.write_final_response.call_args.kwargs, writer.write_final_response.call_args.kwargs
    assert "reviewed_excerpts" not in old_final
    assert final["reviewed_excerpts"].enumeration_sha256 == enumeration_digest(read.read.return_value.result)
    assert final["answer"] == old_final["answer"] == original.answer == result.answer
    assert original.structured == result.structured and original.citations == result.citations
    assert final["provider_context_ref"] == read.read.return_value.tool_call_ref
    assert final["outcome"] == "completed"
    assert result.intent == Intent.ENGAGEMENT
    assert "reviewed_excerpts" not in result.model_dump_json()
    assert service.llm.complete.call_args == legacy.llm.complete.call_args
    assert service.arbiter.review.call_args == legacy.arbiter.review.call_args


@pytest.mark.parametrize("failure", ["selection", "review", "assessment", "context", "upstream"])
def test_actual_pipeline_failure_never_commits_coordinates_as_a_reviewed_answer(failure):
    service, read, writer = pipeline()
    if failure == "selection":
        service.llm.complete.return_value = '{"excerpts":[{"source_index":4,"section_index":0}]}'
    elif failure in {"review", "assessment"}:
        verdict = DoctrinalVerdict(conforms=False, rationale="Synthetic refusal", revised_content="Unvalidated prose.")
        if failure == "assessment":
            verdict._adjudication_disposition = "assessment_unavailable"
        service.arbiter.review.return_value = verdict
    elif failure == "context":
        read.read.return_value = ProviderQuestionResponse.model_validate(source_only_response(sources_ready=False))
    else:
        from app.clients.llm import UpstreamCallError
        service.llm.complete.side_effect = UpstreamCallError("synthetic")
    result = handle(service, QUESTION)
    final = writer.write_final_response.call_args.kwargs
    assert "reviewed_excerpts" not in final and final["outcome"] == "unavailable"
    assert result.citations == []


def test_canonical_final_wire_adds_only_namespace_and_revalidates_before_delivery():
    client = PlatformConversationClient(platform_base_url="https://platform.example", workload_token_provider=AsyncMock())
    client._write = AsyncMock()
    kwargs = dict(turn=turn(), expected_turn_ref="turn_synthetic_001", answer="Synthetic answer",
                  outcome="completed", presentation={"mode": "standard"}, provider_context_ref="context_synthetic_001")
    asyncio.run(client.write_final_response(**kwargs))
    legacy = deepcopy(client._write.call_args.kwargs["payload"])
    asyncio.run(client.write_final_response(**kwargs, reviewed_excerpts=namespace()))
    payload = client._write.call_args.kwargs["payload"]
    assert payload.pop("reviewed_excerpts") == namespace().model_dump(mode="json")
    assert payload == legacy
    forged = namespace()
    forged.excerpts[0] = ReviewedExcerpt.model_construct(ordinal=1, source_index=True, section_index=0)
    before = client._write.await_count
    with pytest.raises(ValidationError):
        asyncio.run(client.write_final_response(**kwargs, reviewed_excerpts=forged))
    assert client._write.await_count == before
    for overrides in ({"outcome": "unavailable"}, {"provider_context_ref": None}):
        with pytest.raises(ValueError):
            asyncio.run(client.write_final_response(**(kwargs | overrides), reviewed_excerpts=namespace()))
    assert client._write.await_count == before


def test_ambiguous_final_deadline_retries_original_namespace_without_reselection():
    service, read, writer = pipeline()
    captured = []

    async def capture(**kwargs):
        state = ACTIVE_PROVIDER_TURN.get()
        assert state.final_arguments == kwargs
        captured.append(state)

    writer.write_final_response.side_effect = capture
    handle(service, QUESTION)
    state = captured[0]
    original = deepcopy(state.final_arguments)
    state.final_committed = False
    # A later DTO order cannot rebind a final that may already have committed.
    read.read.return_value.result.reviewed_sources.reverse()
    writer.write_final_response.side_effect = None
    reads, selections, reviews = read.read.await_count, service.llm.complete.await_count, service.arbiter.review.await_count
    assert asyncio.run(service._close_provider_deadline(state)) is None
    assert writer.write_final_response.call_args.kwargs == original
    assert (read.read.await_count, service.llm.complete.await_count, service.arbiter.review.await_count) == (reads, selections, reviews)
