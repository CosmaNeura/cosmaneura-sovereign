"""Code-only review diagnostics preserve provider closures and private values."""
from __future__ import annotations

import asyncio
import json
import pytest

from app.compliance.audit import AuditLogger, ReviewDisposition
from app.compliance.netsec import UpstreamCallError
from app.config import ComplianceProfile, Persona, Settings
from app.schemas import DoctrinalSensitivity
from tests.test_provider_question_context import handle
from tests.test_strict_doctrinal_verdict import QUESTION, factual_service

PRIVATE_REVIEW = "REVIEW_PROSE_SENTINEL: a reviewer explanation."


def breadcrumbs(captured):
    return [json.loads(line.removeprefix("[AUDIT-OP] "))
            for line in captured.err.splitlines() if line.startswith("[AUDIT-OP] ")]


def review_events(captured):
    return [event for event in breadcrumbs(captured)
            if event["event"].startswith("review_disposition.")]


@pytest.mark.parametrize("flag,assessment,code,terminal", [
    ({"conforms": True}, PRIVATE_REVIEW, "conforms", "completed"),
    ({"conforms": False}, PRIVATE_REVIEW, "nonconforms", "failed"),
    ({"conforms": True}, "I cannot review this question. " + PRIVATE_REVIEW, "scope_refused", "failed"),
    ({"conforms": True}, "The reporting policy conforms. I cannot assess clinical accuracy.",
     "conforms", "completed"),
    ({"conforms": "true"}, PRIVATE_REVIEW, "invalid_verdict", "failed"),
    ({"conforms": True, "needs_revision": True}, PRIVATE_REVIEW, "invalid_verdict", "failed"),
    ({"conforms": True, "extra": "PARSE_VALUE_SENTINEL"}, PRIVATE_REVIEW, "invalid_verdict", "failed"),
    ({"conforms": True}, "", "assessment_unavailable", "failed"),
    ({"conforms": True}, " \t\n", "assessment_unavailable", "failed"),
    ({"conforms": True}, None, "assessment_unavailable", "failed"),
    ({"conforms": True}, {"review": "REVIEW_PROSE_SENTINEL"}, "assessment_unavailable", "failed"),
])
def test_actual_provider_review_emits_closed_code_without_changing_terminal(
    flag, assessment, code, terminal, capsys,
):
    service, _, conversation = factual_service(flag, assessment)
    response = handle(service, QUESTION)
    captured = capsys.readouterr()
    events = review_events(captured)
    assert len(events) == 1
    assert events[0]["event"] == f"review_disposition.{code}"
    assert set(events[0]) == {"ts", "trace_id", "persona", "event", "durable_owner"}
    assert events[0]["trace_id"] == response.trace_id
    assert events[0]["persona"] == "provider" and events[0]["durable_owner"] == "platform"
    assert service._write_platform_observation.call_count == 1
    final_observation = service._write_platform_observation.call_args.kwargs
    assert final_observation["event_type"] == f"sovereign.turn.{terminal}"
    assert final_observation["status"] == terminal
    assert conversation.write_final_response.call_count == 1
    service.magisterium.ask.assert_awaited_once()
    if code in {"scope_refused", "assessment_unavailable"}:
        service.llm.classify_json.assert_not_awaited()
    else:
        service.llm.classify_json.assert_awaited_once()
    if terminal == "failed":
        assert response.safety_notice == "record_evidence_unavailable"
        assert response.doctrinal_review is None and response.citations == []
        assert set(response.presentation.suppress) == {
            "intent_badge", "doctrinal_badge", "citations", "structured_cards",
        }
    for marker in ("REVIEW_PROSE_SENTINEL", "PARSE_VALUE_SENTINEL", "PRIVATE_CITATION_SENTINEL",
                   "148/92", "private-review", "actor_synthetic", "tenant_synthetic", QUESTION):
        assert marker not in json.dumps(breadcrumbs(captured))


@pytest.mark.parametrize("dependency", ["assessment", "verdict_parse"])
def test_only_failure_during_required_review_gets_review_dependency_code(dependency, capsys):
    service, _, conversation = factual_service({"conforms": True}, PRIVATE_REVIEW)
    target = service.magisterium.ask if dependency == "assessment" else service.llm.classify_json
    target.side_effect = UpstreamCallError("UPSTREAM_ERROR_SENTINEL")
    response = handle(service, QUESTION)
    captured = capsys.readouterr()
    assert [event["event"] for event in review_events(captured)] == [
        "review_disposition.upstream_unavailable",
    ]
    assert response.safety_notice == "upstream_unavailable"
    assert response.doctrinal_review is None and response.citations == []
    assert conversation.write_final_response.call_count == 1
    assert service._write_platform_observation.call_args.kwargs["status"] == "failed"
    assert "UPSTREAM_ERROR_SENTINEL" not in captured.err


def test_pre_review_drafting_failure_does_not_claim_review_failure(capsys):
    service, _, _ = factual_service({"conforms": True}, PRIVATE_REVIEW)
    service.llm.complete.side_effect = UpstreamCallError("DRAFT_ERROR_SENTINEL")
    response = handle(service, QUESTION)
    captured = capsys.readouterr()
    assert review_events(captured) == []
    service.magisterium.ask.assert_not_awaited()
    assert response.safety_notice == "upstream_unavailable"
    assert "DRAFT_ERROR_SENTINEL" not in captured.err


def test_cancellation_is_not_reclassified_or_swallowed(capsys):
    service, _, conversation = factual_service({"conforms": True}, PRIVATE_REVIEW)
    service.magisterium.ask.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        handle(service, QUESTION)
    assert review_events(capsys.readouterr()) == []
    conversation.write_final_response.assert_not_awaited()


def test_none_review_bypass_has_truthful_not_adjudicated_code(capsys):
    service, _, _ = factual_service({"conforms": True}, PRIVATE_REVIEW, exact=False)
    service.router.needs_provider_context.return_value = False
    service.router.route.return_value = (service.router.route.return_value[0], DoctrinalSensitivity.NONE, "")
    handle(service, "A synthetic logistics question.")
    assert [event["event"] for event in review_events(capsys.readouterr())] == [
        "review_disposition.not_adjudicated",
    ]
    service.magisterium.ask.assert_not_awaited()


def test_unknown_private_dispositions_never_coerce_or_escape(capsys):
    class RepresentationTrap:
        def __str__(self):
            raise AssertionError("untrusted disposition coerced")

        def __repr__(self):
            raise AssertionError("untrusted disposition represented")

    logger = AuditLogger(Settings(_env_file=None, platform_agent_api_enabled=True))
    profile = ComplianceProfile(Persona.PROVIDER)
    for value in (RepresentationTrap(), None, {}, [], "RAW_DISPOSITION_SENTINEL", "upstream_unavailable"):
        logger.record_review_disposition(
            trace_id="generated_trace_identifier", profile=profile,
            disposition=ReviewDisposition.from_adjudication(value),
        )
    events = review_events(capsys.readouterr())
    assert len(events) == 6
    assert {event["event"] for event in events} == {"review_disposition.unrecognized_disposition"}
    assert "RAW_DISPOSITION_SENTINEL" not in json.dumps(events)


@pytest.mark.parametrize("value", ["conforms", "RAW_DISPOSITION_SENTINEL", None, {}])
def test_logger_cannot_accept_free_form_disposition(value, capsys):
    logger = AuditLogger(Settings(_env_file=None, platform_agent_api_enabled=True))
    with pytest.raises(ValueError, match="review disposition must be code-controlled"):
        logger.record_review_disposition(
            trace_id="generated_trace_identifier", profile=ComplianceProfile(Persona.PROVIDER),
            disposition=value,
        )
    assert capsys.readouterr().err == ""


def test_profile_gate_and_platform_sink_ownership_are_preserved(tmp_path, capsys):
    path = tmp_path / "unused-audit.jsonl"
    logger = AuditLogger(Settings(_env_file=None, platform_agent_api_enabled=True,
                                  audit_sink="file", audit_path=str(path)))
    profile = ComplianceProfile(Persona.PROVIDER)
    profile.audit_required = False
    logger.record_review_disposition(trace_id="generated_trace_identifier", profile=profile,
                                     disposition=ReviewDisposition.SCOPE_REFUSED)
    assert capsys.readouterr().err == "" and not path.exists()
    profile.audit_required = True
    logger.record_review_disposition(trace_id="generated_trace_identifier", profile=profile,
                                     disposition=ReviewDisposition.SCOPE_REFUSED)
    assert len(review_events(capsys.readouterr())) == 1
    assert not path.exists() and not hasattr(logger, "_prev_chain") and not hasattr(logger, "_lock")
