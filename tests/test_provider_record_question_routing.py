"""Scripted routing/prompt acceptance; this is not a live-model accuracy eval."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.clients.provider_question_contract import ProviderQuestionResponse
from app.config import Persona, Settings
from app.schemas import ChatRequest, DoctrinalSensitivity, Intent
from app.security.mount import MountResolutionError
from app.sovereign.router import Router
from tests.test_provider_question_context import handle, sovereign
from tests.test_provider_reviewed_sources import reviewed_response


CORPUS = json.loads((Path(__file__).parents[1]
                    / "contracts/fixtures/provider-question-context-v1.1/clinician-question-corpus.json").read_text())


@pytest.mark.parametrize("case", CORPUS, ids=lambda case: case["id"])
def test_twenty_clinician_queries_use_grounded_prompt_or_retain_hidden_skill_refusal(case):
    service, context, conversation = sovereign()
    service.router = Router(service.s, service.llm)
    classified_intent = Intent(case["intent"])
    # The primary classifier is intentionally scripted, including the actual
    # wrong LAB_REVIEW label observed in staging. The secondary scope selector
    # is also scripted. This tests behavior after those decisions, not their
    # stochastic accuracy or the correctness of a live generated answer.
    service.router.route = AsyncMock(return_value=(classified_intent, DoctrinalSensitivity.LOW, ""))
    service.llm.classify_json = AsyncMock(return_value={"requires_prepared_context": case["record_fact"]})
    # Selection is also scripted: this verifies routing/citation dispatch, not
    # live relevance or whether these excerpts fully answer each corpus item.
    selected_answer = json.dumps({"excerpts": [{
        "source_index": 0, "section_index": 0,
        "quote": "Office blood pressure is 148/92.",
    }]})
    service.llm.complete = AsyncMock(return_value=(
        selected_answer if classified_intent in {Intent.ENGAGEMENT, Intent.LAB_REVIEW}
        else "Synthetic clinician-facing answer."
    ))
    context.read.return_value = ProviderQuestionResponse.model_validate(reviewed_response())
    response = handle(service, case["message"])

    if not case["record_fact"]:
        assert response.safety_notice == "capability_denied"
        assert response.intent == classified_intent
        context.read.assert_not_called()
        service.llm.complete.assert_not_called()
        service.arbiter.review.assert_not_called()
        return

    assert response.safety_notice != "capability_denied"
    assert response.persona == Persona.PROVIDER
    assert response.intent == (Intent.ENGAGEMENT if classified_intent == Intent.LAB_REVIEW else classified_intent)
    assert context.read.call_count == 1
    service.arbiter.review.assert_called_once()
    prompt = service.llm.complete.call_args.kwargs
    assert "148/92" in prompt["prompt"]
    assert "128" in prompt["prompt"] and "not measured today" in prompt["prompt"]
    assert "clinician" in prompt["system"].lower()
    assert "questions the person could ask their" not in prompt["system"].lower()
    assert "patient preparing for their own visit" not in prompt["prompt"]
    assert not any("medlineplus" in citation.source.lower() for citation in response.citations)
    assert set(response.presentation.suppress) == {"intent_badge", "doctrinal_badge"}
    assert "disclaimer" not in response.presentation.suppress
    assert conversation.write_final_response.call_args.kwargs["presentation"] == response.presentation.model_dump(mode="json")


def test_actual_offline_classifier_reproduces_and_recovers_the_claude_mixed_query():
    case = next(case for case in CORPUS if case["id"] == "claude-mixed-dose-labs")
    router = Router(Settings(_env_file=None, mock_mode=True))
    request = ChatRequest(persona=Persona.PROVIDER, message=case["message"])
    intent, _, _ = asyncio.run(router.route(request))
    assert intent == Intent.LAB_REVIEW
    assert asyncio.run(router.needs_provider_context(request, intent)) is True


@pytest.mark.parametrize("message", [
    "Fetch the latest results from Athena.", "Show the current lab values in Epic.",
    "Query the entire chart for lab trends.", "Please order a CBC.",
    "For this patient, cancel the CBC order.", "Which notes mention fatigue? Then prescribe iron.",
    "Interpret whether the labs are concerning.",
])
def test_explicit_external_fetch_or_clinical_action_never_reaches_scope_classifier(message):
    router = Router(Settings(_env_file=None, mock_mode=True))
    router.llm.classify_json = AsyncMock(return_value={"requires_prepared_context": True})
    assert asyncio.run(router.needs_provider_context(
        ChatRequest(persona=Persona.PROVIDER, message=message), Intent.LAB_REVIEW,
    )) is False
    router.llm.classify_json.assert_not_called()


@pytest.mark.parametrize("decision", [False, "true", None])
def test_false_or_ambiguous_scope_never_enters_record_answer_lane(decision):
    service, context, _ = sovereign()
    service.router = Router(service.s, service.llm)
    service.router.route = AsyncMock(return_value=(Intent.LAB_REVIEW, DoctrinalSensitivity.LOW, ""))
    service.llm.classify_json = AsyncMock(return_value={"requires_prepared_context": decision})
    if decision is False:
        response = handle(service, "Which lab orders were documented in the prior notes?")
        assert response.safety_notice == "capability_denied"
    else:
        with pytest.raises(MountResolutionError) as caught:
            handle(service, "Which lab orders were documented in the prior notes?")
        assert caught.value.code == "PROVIDER_CONTEXT_SELECTION_UNAVAILABLE"
    context.read.assert_not_called()
    service.llm.complete.assert_not_called()


def test_rerouted_facts_keep_sensitivity_floor_and_one_selection():
    service, context, _ = sovereign()
    service.router = Router(service.s, service.llm)
    service.router.route = AsyncMock(return_value=(Intent.LAB_REVIEW, DoctrinalSensitivity.NONE, ""))
    service.llm.classify_json = AsyncMock(return_value={"requires_prepared_context": True})
    handle(service, "Which CBC results were documented in the prior notes?")
    assert service.llm.classify_json.call_count == 1
    assert service.arbiter.review.call_args.kwargs["sensitivity"] == DoctrinalSensitivity.LOW
    assert context.read.call_count == 1


def test_missing_record_capability_or_disabled_feature_cannot_use_new_lane():
    for service, context, _ in (sovereign(record_capability=False), sovereign(enabled=False)):
        service.router.route = AsyncMock(return_value=(Intent.LAB_REVIEW, DoctrinalSensitivity.LOW, ""))
        response = handle(service, "Which CBC results were documented in the prior notes?")
        assert response.safety_notice == "capability_denied"
        service.router.needs_provider_context.assert_not_called()
        context.read.assert_not_called()


def test_emergency_still_precedes_routing_context_and_canonical_work():
    service, context, conversation = sovereign()
    response = handle(service, "I have chest pain and cannot breathe. Which prior lab result matters?")
    assert response.intent == Intent.EMERGENCY
    assert response.presentation.mode == "emergency"
    assert response.presentation.suppress == []
    service.router.route.assert_not_called()
    context.read.assert_not_called()
    conversation.write_user_accepted.assert_not_called()


def test_ethics_context_keeps_doctrinal_review_badge():
    service, _, _ = sovereign()
    service.router.route = AsyncMock(return_value=(Intent.ETHICS_CONSULT, DoctrinalSensitivity.NONE, ""))
    ethics = service.agents[Intent.ETHICS_CONSULT]
    ethics.pubmed.search = AsyncMock(return_value=[])
    ethics.llm.classify_json = AsyncMock(return_value={"topic": "clinical ethics"})
    response = handle(service, "Does the documented prior care plan comply with Catholic ethics?")
    assert "doctrinal_badge" not in response.presentation.suppress
    assert service.arbiter.review.call_args.kwargs["sensitivity"] == DoctrinalSensitivity.HIGH
