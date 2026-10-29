"""Unusable private verdicts deny egress without claiming a reviewed revision."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import httpx
from pydantic import ValidationError

from app.clients.provider_evidence import (
    EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE, EVIDENCE_REVIEW_UNAVAILABLE, EVIDENCE_UNAVAILABLE,
)
from app.clients.llm import LLMClient
from app.clients.provider_question_contract import ProviderQuestionResponse
from app.compliance.safety import disclaimer_for
from app.config import Persona, Settings
from app.schemas import Citation, DoctrinalSensitivity, DoctrinalVerdict, Intent
from app.sovereign.arbiter import DoctrinalArbiter, REVIEW_ASSESSMENT_UNAVAILABLE
from tests.test_provider_evidence import output, selected
from tests.test_provider_question_context import handle, sovereign
from tests.test_provider_source_readiness import source_only_response

REVIEW = "PRIVATE_REVIEW_SENTINEL: unsupported replacement dose is 75 mg."
QUESTION = "What office blood pressure was documented on September 30, 2026? Include the visit date."
INVALID_FLAGS = [
    None, True, False, 0, 1, 1.0, "true", "false", [], [{}], {},
    {"needs_revision": False}, {"Conforms": True},
    *[{"conforms": value} for value in (None, "true", "false", "0", 0, 1, 1.0, [], {})],
    *[{"conforms": True, "needs_revision": value}
      for value in (None, "false", 0, 1, [], {})],
    {"conforms": True, "needs_revision": True},
    {"conforms": True, "rationale": "PRIVATE_FLAG_SENTINEL"},
    {"conforms": False, "status": "PRIVATE_FLAG_SENTINEL"},
    {"conforms": True, 1: True},
]
SUPPRESSIONS = {"intent_badge", "doctrinal_badge", "citations", "structured_cards"}
ABSENT_ASSESSMENTS = ["", " \t\r\n", "\u00a0", None, False, 0, 1.5, [], ["unsupported"], {}, {"conforms": True}]


def arbiter(flag, review=REVIEW):
    service = DoctrinalArbiter(Settings(_env_file=None, mock_mode=True))
    service.magisterium.ask = AsyncMock(return_value=(review, []))
    service.llm.classify_json = AsyncMock(return_value=flag)
    return service


def review(service, sensitivity=DoctrinalSensitivity.LOW):
    return asyncio.run(service.review(
        deidentified_draft="Synthetic reviewed-source quotation.",
        intent=Intent.ENGAGEMENT, sensitivity=sensitivity,
    ))


@pytest.mark.parametrize("sensitivity", [DoctrinalSensitivity.LOW, DoctrinalSensitivity.HIGH])
@pytest.mark.parametrize("flag", INVALID_FLAGS)
def test_invalid_flags_are_denied_after_one_required_review_and_one_parse(flag, sensitivity):
    service = arbiter(flag)
    verdict = review(service, sensitivity)
    assert verdict.conforms is False
    assert verdict._adjudication_disposition == "invalid_verdict"
    service.magisterium.ask.assert_called_once()
    service.llm.classify_json.assert_called_once()


@pytest.mark.parametrize("flag,conforms", [
    ({"conforms": True}, True),
    ({"conforms": True, "needs_revision": False}, True),
    ({"conforms": False}, False),
    ({"conforms": False, "needs_revision": False}, False),
    ({"conforms": False, "needs_revision": True}, False),
])
def test_closed_consistent_boolean_judgments_preserve_the_gate(flag, conforms):
    verdict = review(arbiter(flag))
    assert verdict.conforms is conforms
    assert verdict._adjudication_disposition == ("conforms" if conforms else "nonconforms")
    assert verdict.revised_content == (None if conforms else REVIEW)


def test_no_python_truthiness_can_manufacture_conformance():
    class TruthinessTrap:
        def __bool__(self):
            raise AssertionError("Untrusted flag truthiness must not be evaluated")

    class ObjectTrap(dict):
        def __bool__(self):
            raise AssertionError("Untrusted object truthiness must not be evaluated")

    for flag in (TruthinessTrap(), {"conforms": TruthinessTrap()}, ObjectTrap(conforms=True)):
        assert review(arbiter(flag))._adjudication_disposition == "invalid_verdict"


@pytest.mark.parametrize("raw", [
    "not JSON", "", "null", "[]", "false", '{"conforms":"false"}',
    '{"conforms":true,"needs_revision":true}', '{"conforms":true,"rationale":"injection"}',
    '{"conforms":false,"conforms":true}', '{"conforms":true,"conforms":false}',
    '{"conforms":true,"needs_revision":true,"needs_revision":false}',
    '{"conforms":false,"con\\u0066orms":true}',
    '```json\n{"conforms":false,"conforms":true}\n```', '```json\n[]\n```',
])
def test_actual_json_helper_malformed_and_wrong_shapes_stay_denied(raw):
    service = DoctrinalArbiter(Settings(_env_file=None, mock_mode=True))
    service.magisterium.ask = AsyncMock(return_value=(REVIEW, []))
    service.llm.complete = AsyncMock(return_value=raw)
    verdict = review(service)
    assert verdict.conforms is False and verdict._adjudication_disposition == "invalid_verdict"
    service.llm.complete.assert_called_once()


def test_valid_fenced_adjudication_and_default_json_parser_compatibility():
    service = DoctrinalArbiter(Settings(_env_file=None, mock_mode=True))
    service.magisterium.ask = AsyncMock(return_value=(REVIEW, []))
    service.llm.complete = AsyncMock(return_value='```json\n{"conforms":true,"needs_revision":false}\n```')
    assert review(service).conforms is True
    call = service.llm.complete.call_args.kwargs
    assert call["max_tokens"] == 300 and call["temperature"] == 0.0
    assert call["model"] == service.s.fast_model and call["usage_purpose"] == "doctrinal_verdict_parse"
    assert call["usage_call_kind"] == "classification"
    assert call["system"] == (
        "You are a doctrinal reviewer adjudication parser. Read the "
        "reviewer's assessment and decide if the draft conforms."
        "\nRespond with ONLY a JSON object, no prose, no code fences."
    )
    assert call["prompt"] == f"Reviewer assessment:\n{REVIEW}\n\nFields: conforms (bool), needs_revision (bool)."
    client = LLMClient(Settings(_env_file=None, mock_mode=True))
    client.complete = AsyncMock(return_value='{"selector":false,"selector":true}')
    assert asyncio.run(client.classify_json(system="Routing", prompt="Synthetic")) == {"selector": True}
    client.complete.assert_called_once()


def test_scope_refusal_still_precedes_parser_and_none_still_skips_review():
    service = arbiter({"conforms": True}, "I cannot review this clinical question.")
    assert review(service)._adjudication_disposition == "scope_refused"
    service.llm.classify_json.assert_not_called()
    service = arbiter({"conforms": False})
    assert review(service, DoctrinalSensitivity.NONE).conforms is True
    service.magisterium.ask.assert_not_called()
    service.llm.classify_json.assert_not_called()


def test_private_disposition_is_not_wire_input_output_or_schema():
    verdict = review(arbiter({}))
    assert set(verdict.model_dump()) == {"conforms", "rationale", "revised_content", "citations"}
    assert "_adjudication_disposition" not in json.dumps(verdict.model_json_schema())
    assert "invalid_verdict" not in verdict.model_dump_json()
    with pytest.raises(ValidationError):
        DoctrinalVerdict.model_validate({**verdict.model_dump(), "_adjudication_disposition": "conforms"})


def factual_service(flag, review_text=REVIEW, *, exact=True):
    service, read, conversation = sovereign()
    if exact:
        read.read.return_value = ProviderQuestionResponse.model_validate(source_only_response("unavailable"))
        service.llm.complete.return_value = output(selected())
    service.magisterium.ask = AsyncMock(return_value=(review_text, [Citation(
        source="PRIVATE_CITATION_SENTINEL", url="https://synthetic.invalid/private-review",
    )]))
    service.llm.classify_json = AsyncMock(return_value=flag)
    service.arbiter = DoctrinalArbiter(service.s, magisterium=service.magisterium, llm=service.llm)
    return service, read, conversation


@pytest.mark.parametrize("flag", INVALID_FLAGS)
def test_invalid_verdict_canonicalizes_static_failure_and_no_transient_review_exports(flag):
    service, read, conversation = factual_service(flag)
    response = handle(service, QUESTION)
    assert response.answer.startswith(EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE)
    assert response.safety_notice == "record_evidence_unavailable"
    assert response.structured == {"record_evidence_status": "review_unavailable"}
    assert response.doctrinal_review is None and response.citations == []
    assert set(response.presentation.suppress) == SUPPRESSIONS
    assert disclaimer_for(Persona.PROVIDER) in response.answer
    assert read.read.return_value.result.source_notice() in response.answer
    for absent in ("148/92", REVIEW, "PRIVATE_FLAG_SENTINEL", "PRIVATE_CITATION_SENTINEL", "private-review"):
        assert absent not in response.model_dump_json()
    final = conversation.write_final_response.call_args.kwargs
    assert final["answer"] == response.answer and final["outcome"] == "unavailable"
    assert final["presentation"] == response.presentation.model_dump(mode="json")
    assert final["provider_context_ref"] == read.read.return_value.tool_call_ref
    assert final["history_context_ref"] == "history_context_fictional"
    terminal = service._write_platform_observation.call_args.kwargs
    assert terminal["status"] == "failed" and terminal["event_type"] == "sovereign.turn.failed"
    service.magisterium.ask.assert_called_once()
    service.llm.classify_json.assert_called_once()
    service.llm.complete.assert_called_once()
    conversation.write_final_response.assert_called_once()


@pytest.mark.parametrize("flag,text", [
    ({"conforms": False, "needs_revision": True}, REVIEW),
    ({"conforms": True}, "I cannot review this clinical question."),
])
def test_explicit_negative_and_refusal_keep_their_distinct_non_answer(flag, text):
    service, _, conversation = factual_service(flag, text)
    response = handle(service, QUESTION)
    assert response.answer.startswith(EVIDENCE_REVIEW_UNAVAILABLE)
    assert EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE not in response.answer
    assert response.doctrinal_review is None and response.citations == []
    assert set(response.presentation.suppress) == SUPPRESSIONS
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"
    service.magisterium.ask.assert_called_once()


def test_non_factual_revision_and_verified_factual_success_remain_visible():
    service, _, conversation = factual_service({"conforms": False}, exact=False)
    service.router.needs_provider_context.return_value = False
    response = handle(service, QUESTION)
    assert REVIEW in response.answer and response.doctrinal_review.conforms is False
    assert response.citations and response.presentation.suppress == []
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "completed"
    service, _, conversation = factual_service({"conforms": True, "needs_revision": False})
    response = handle(service, QUESTION)
    assert "148/92" in response.answer and response.doctrinal_review.conforms is True
    assert response.citations and set(response.presentation.suppress) == {"intent_badge", "doctrinal_badge"}
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "completed"


@pytest.mark.parametrize("flag", [{"conforms": True}, {"conforms": False}, {}, {"conforms": "true"}])
def test_invalid_selection_preserves_primary_failure_after_any_mandatory_review(flag):
    service, _, conversation = factual_service(flag)
    service.llm.complete.return_value = "Invalid private selection."
    response = handle(service, QUESTION)
    assert response.safety_notice == "record_evidence_unavailable"
    assert response.answer.startswith(EVIDENCE_UNAVAILABLE)
    assert response.structured["record_evidence_status"] == "unavailable"
    assert EVIDENCE_REVIEW_UNAVAILABLE not in response.answer
    assert EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE not in response.answer
    assert response.doctrinal_review is None and response.citations == []
    assert set(response.presentation.suppress) == SUPPRESSIONS
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"
    service.magisterium.ask.assert_called_once()


@pytest.mark.parametrize("assessment", ABSENT_ASSESSMENTS)
def test_absent_assessment_cannot_be_supplied_by_the_actual_positive_parser(assessment):
    service = DoctrinalArbiter(Settings(_env_file=None, mock_mode=True))
    service.magisterium.ask = AsyncMock(return_value=(assessment, [Citation(source="PRIVATE_CITATION_SENTINEL")]))
    service.llm.complete = AsyncMock(return_value='{"conforms":true,"needs_revision":false}')
    service.llm.classify_json = AsyncMock(wraps=service.llm.classify_json)
    verdict = review(service)
    assert verdict.conforms is False and verdict._adjudication_disposition == "assessment_unavailable"
    assert verdict.rationale == verdict.revised_content == REVIEW_ASSESSMENT_UNAVAILABLE
    assert verdict.citations == []
    service.magisterium.ask.assert_called_once()
    service.llm.classify_json.assert_not_called()
    service.llm.complete.assert_not_called()


@pytest.mark.parametrize("assessment", ["", " \t\r\n", None, [], {}])
def test_actual_magisterium_response_absence_is_unavailable_without_parser(monkeypatch, assessment):
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": assessment}}],
            "citations": [{"title": "PRIVATE_CITATION_SENTINEL", "url": "https://synthetic.invalid/review"}],
        })

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs))
    monkeypatch.setattr("app.clients.magisterium.assert_safe_outbound_url", lambda _: None)
    service = DoctrinalArbiter(Settings(
        _env_file=None, mock_mode=False, magisterium_api_key="synthetic-key",
        magisterium_base_url="https://synthetic.invalid",
    ))
    service.llm.complete = AsyncMock(return_value='{"conforms":true}')
    verdict = review(service)
    assert verdict.conforms is False and verdict._adjudication_disposition == "assessment_unavailable"
    assert verdict.rationale == REVIEW_ASSESSMENT_UNAVAILABLE and verdict.citations == []
    assert len(requests) == 1
    service.llm.complete.assert_not_called()


def positive_parser_for(service, draft):
    service.llm.classify_json = AsyncMock(wraps=LLMClient.classify_json.__get__(service.llm))
    service.llm.complete.side_effect = lambda **kwargs: (
        '{"conforms":true,"needs_revision":false}'
        if kwargs.get("usage_purpose") == "doctrinal_verdict_parse" else draft
    )


@pytest.mark.parametrize("assessment", ABSENT_ASSESSMENTS)
def test_factual_absent_assessment_is_static_unavailable_after_selection(assessment):
    service, read, conversation = factual_service({"conforms": True}, assessment)
    positive_parser_for(service, output(selected()))
    response = handle(service, QUESTION)
    assert response.answer.startswith(EVIDENCE_REVIEW_INTERPRETATION_UNAVAILABLE)
    assert response.safety_notice == "record_evidence_unavailable"
    assert response.doctrinal_review is None and response.citations == []
    assert set(response.presentation.suppress) == SUPPRESSIONS
    assert "148/92" not in response.answer and "PRIVATE_CITATION_SENTINEL" not in response.model_dump_json()
    final = conversation.write_final_response.call_args.kwargs
    assert final["outcome"] == "unavailable" and final["answer"] == response.answer
    assert final["provider_context_ref"] == read.read.return_value.tool_call_ref
    assert final["history_context_ref"] == "history_context_fictional"
    service.magisterium.ask.assert_called_once()
    service.llm.classify_json.assert_not_called()
    service.llm.complete.assert_called_once()


@pytest.mark.parametrize("with_context", [False, True])
@pytest.mark.parametrize("assessment", ["", None, {}])
def test_nonfactual_absent_assessment_replaces_draft_with_unavailable_closure(assessment, with_context):
    service, read, conversation = factual_service({"conforms": True}, assessment, exact=False)
    service.router.needs_provider_context.return_value = with_context
    positive_parser_for(service, "NONFACTUAL_DRAFT_SENTINEL")
    # Exercise synthesis, not the closed dated blood-pressure excerpt lookup.
    response = handle(service, "Summarize the prepared visit overview for the upcoming encounter.")
    assert response.answer.startswith(REVIEW_ASSESSMENT_UNAVAILABLE)
    assert response.safety_notice == "upstream_unavailable"
    assert response.structured == {} and response.doctrinal_review is None and response.citations == []
    assert set(response.presentation.suppress) == SUPPRESSIONS
    assert "NONFACTUAL_DRAFT_SENTINEL" not in response.model_dump_json()
    assert "PRIVATE_CITATION_SENTINEL" not in response.model_dump_json()
    assert disclaimer_for(Persona.PROVIDER) in response.answer
    final = conversation.write_final_response.call_args.kwargs
    assert final["outcome"] == "unavailable" and final["answer"] == response.answer
    assert final["history_context_ref"] == "history_context_fictional"
    assert final["presentation"] == response.presentation.model_dump(mode="json")
    if with_context:
        assert read.read.return_value.result.source_notice() in response.answer
        assert final["provider_context_ref"] == read.read.return_value.tool_call_ref
    else:
        assert "provider_context_ref" not in final
    assert service._write_platform_observation.call_args.kwargs["event_type"] == "sovereign.turn.failed"
    service.magisterium.ask.assert_called_once()
    service.llm.classify_json.assert_not_called()
    service.llm.complete.assert_called_once()


@pytest.mark.parametrize("assessment", ["", None, {}])
def test_selection_failure_remains_primary_when_assessment_is_absent(assessment):
    service, _, conversation = factual_service({"conforms": True}, assessment)
    positive_parser_for(service, "Invalid private selection.")
    response = handle(service, QUESTION)
    assert response.answer.startswith(EVIDENCE_UNAVAILABLE)
    assert response.structured == {"record_evidence_status": "unavailable"}
    assert response.safety_notice == "record_evidence_unavailable"
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"
    service.magisterium.ask.assert_called_once()
    service.llm.classify_json.assert_not_called()


def test_none_bypass_and_absent_assessment_never_coerce_arbitrary_objects():
    class RepresentationTrap:
        def __str__(self):
            raise AssertionError("Absent assessment must not be coerced")

    service = arbiter({"conforms": True}, RepresentationTrap())
    assert review(service)._adjudication_disposition == "assessment_unavailable"
    service = arbiter({"conforms": True}, None)
    assert review(service, DoctrinalSensitivity.NONE).conforms is True
    service.magisterium.ask.assert_not_called()
    service.llm.classify_json.assert_not_called()
