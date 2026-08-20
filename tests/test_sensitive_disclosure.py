"""Sensitive-disclosure regressions for the patient mount."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.clients.platform_conversation import ConversationDeliveryError
from app.compliance import safety
from app.compliance.sensitive_disclosure import (
    SENSITIVE_DISCLOSURE_RESPONSE,
    detect_sensitive_disclosure,
)
from app.config import MountProfile, Persona, Settings
from app.schemas import (ALL_PRESENTATION_SUPPRESSIONS, ChatRequest,
                         DoctrinalSensitivity, Intent)
from app.security.auth import Principal
from app.sovereign.orchestrator import Sovereign, _sensitive_fallback


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("message", [
    "I had a miscarriage yesterday.",
    "I've had a miscarriage.",
    "I've miscarried.",
    "I have miscarried.",
    "My wife miscarried and we are heartbroken.",
    "We suffered a pregnancy loss.",
    "I had a stillbirth.",
    "My wife had a stillbirth.",
    "My baby was stillborn.",
    "We just lost our baby.",
    "My father passed away this morning.",
    "I'm grieving the death of my sister.",
    "I was diagnosed with terminal cancer.",
    "I have terminal cancer.",
    "I have a terminal diagnosis.",
    "I'm terminally ill.",
    "I was told I have terminal cancer.",
    "I've just been diagnosed with a terminal illness.",
    "I've just received a terminal diagnosis.",
    "My doctor told me it is terminal.",
    "I was just hospitalized.",
    "I am currently hospitalized.",
    "I've been admitted to the hospital because of an infection.",
    "I just got admitted to the hospital.",
    "I'm currently in the hospital.",
    "I'm in the hospital for pneumonia.",
    "I'm in the hospital, and I'm scared.",
    "My daughter is in the hospital.",
    "My daughter is in the hospital for pneumonia.",
    "Our daughter was hospitalized.",
    "I just came home from the hospital.",
])
def test_detector_recognizes_supported_personal_disclosures(message):
    assert detect_sensitive_disclosure(message)


@pytest.mark.parametrize("message", [
    "What are the warning signs of miscarriage?",
    "My miscarriage risk is low.",
    "I lost my keys at the hospital.",
    "Loss of appetite can happen during treatment.",
    "How do airport terminal maps work?",
    "What does a terminal diagnosis mean?",
    "What happens after hospitalization?",
    "I have a routine appointment at the hospital.",
    "I'm in the hospital for a routine appointment.",
    "I'm in the hospital, visiting my sister.",
    "I'm in the hospital, and visiting my sister.",
    "My daughter is in the hospital for a routine appointment.",
    "My daughter is in the hospital, visiting a friend.",
    "I'm in the hospital working today.",
    "I'm in the hospital for outpatient tests.",
    "I'm in the hospital for work.",
    "My patient was hospitalized after surgery.",
    "Our hospitality team admitted a scheduling error.",
])
def test_detector_does_not_capture_educational_or_unrelated_language(message):
    assert not detect_sensitive_disclosure(message)


def test_sensitive_response_is_static_warm_and_skips_all_clinical_processing():
    sovereign = Sovereign()
    calls: list[tuple[str, dict]] = []

    async def must_not_route(request):
        raise AssertionError("sensitive disclosure must not reach the router")

    async def must_not_complete(**kwargs):
        raise AssertionError("sensitive disclosure must not reach a model")

    async def must_not_arbitrate(**kwargs):
        raise AssertionError("sensitive disclosure must not reach the arbiter")

    class MustNotRunAgent:
        async def run(self, request, profile):
            raise AssertionError("sensitive disclosure must not reach an agent")

    def audit_spy(**kwargs):
        calls.append((kwargs["event"], kwargs))

    sovereign.router.route = must_not_route
    sovereign.llm.complete = must_not_complete
    sovereign.arbiter.review = must_not_arbitrate
    sovereign.agents = {intent: MustNotRunAgent() for intent in sovereign.agents}
    sovereign.audit.record = audit_spy

    raw_disclosure = "My husband passed away last night."
    response = _run(sovereign.handle(ChatRequest(
        persona=Persona.PROVIDER,
        message=raw_disclosure,
        metadata={"capabilities": ["ehr:labs:read"]},
    )))

    assert response.intent == Intent.SENSITIVE_DISCLOSURE
    assert response.persona == Persona.PATIENT
    assert response.answer == SENSITIVE_DISCLOSURE_RESPONSE
    assert response.answer.startswith("I'm so sorry")
    assert "someone you trust" in response.answer
    assert "logged" not in response.answer.lower()
    assert "care team" not in response.answer.lower()
    assert "legal" not in response.answer.lower()
    assert "system" not in response.answer.lower()
    assert "feature" not in response.answer.lower()
    assert safety.PATIENT_DISCLAIMER not in response.answer
    assert safety.PROVIDER_DISCLAIMER not in response.answer
    assert response.presentation.mode == "sensitive_disclosure"
    assert set(response.presentation.suppress) == set(ALL_PRESENTATION_SUPPRESSIONS)
    assert response.presentation.human_contact.action_id == "human-contact"

    disclosure_events = [kwargs for event, kwargs in calls
                         if event == "sensitive_disclosure"]
    assert len(disclosure_events) == 1
    assert disclosure_events[0]["details"] == {
        "intent": Intent.SENSITIVE_DISCLOSURE.value,
    }
    assert "content_for_hash" not in disclosure_events[0]
    assert all(raw_disclosure not in repr(kwargs) for _, kwargs in calls)
    assert {event for event, _ in calls} == {"sensitive_disclosure", "egress"}


@pytest.mark.parametrize("message", [
    "I had a miscarriage and now I can't breathe.",
    "I had a miscarriage and now have severe bleeding.",
])
def test_acute_emergency_wins_when_messages_overlap(message):
    sovereign = Sovereign()

    class MountMustNotResolve:
        async def resolve(self, request):
            raise AssertionError("emergency must remain before sensitive mode")

    sovereign.mount_resolver = MountMustNotResolve()
    response = _run(sovereign.handle(ChatRequest(
        persona=Persona.PATIENT,
        message=message,
    )))
    assert response.intent == Intent.EMERGENCY
    assert "911" in response.answer


def test_body_persona_cannot_select_patient_mode_on_provider_mount():
    sovereign = Sovereign(Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
    ))

    async def fixed_route(request):
        return Intent.SMALL_TALK, DoctrinalSensitivity.NONE, "test"

    async def fixed_answer(**kwargs):
        return "Provider response."

    sovereign.router.route = fixed_route
    sovereign.llm.complete = fixed_answer
    response = _run(sovereign.handle(
        ChatRequest(
            persona=Persona.PATIENT,
            message="I had a miscarriage.",
        ),
        principal=Principal(
            subject="provider-test-actor",
            persona=Persona.PROVIDER,
            roles=["provider"],
        ),
    ))
    assert response.intent == Intent.SMALL_TALK
    assert response.persona == Persona.PROVIDER
    assert safety.PROVIDER_DISCLAIMER in response.answer


def test_http_sensitive_response_has_no_trailing_disclaimer():
    client = TestClient(main_module.app)
    with patch.object(main_module, "_sovereign", Sovereign()):
        response = client.post("/chat", json={
            "client_turn_id": str(uuid.uuid4()),
            "persona": "provider",
            "message": "My mother died this morning.",
        }, headers={"X-Cosmaneura-Embed-Version": "1.0"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "sensitive_disclosure"
    assert payload["persona"] == "patient"
    assert payload["answer"] == SENSITIVE_DISCLOSURE_RESPONSE
    assert "educational" not in payload["answer"].lower()
    assert payload["presentation"]["mode"] == "sensitive_disclosure"
    assert set(payload["presentation"]["suppress"]) == set(
        ALL_PRESENTATION_SUPPRESSIONS
    )
    assert payload["presentation"]["human_contact"]["action_id"] == "human-contact"


def test_sensitive_persistence_failure_is_value_free_nonretryable_503_fallback():
    error = Sovereign._delivery_mount_error(
        error=ConversationDeliveryError("unavailable", retryable=True),
        owner="conversation",
        trace_id="trace_sensitive_test",
        safety_fallback=_sensitive_fallback("user_accepted"),
    )
    response = _run(main_module._mount_resolution_error(None, error))
    payload = response.body.decode()

    assert response.status_code == 503
    assert '"code":"SERVICE_UNAVAILABLE"' in payload
    assert '"retryable":false' in payload
    assert '"failure_stage":"user_accepted"' in payload
    assert '"mode":"sensitive_disclosure"' in payload


def test_regular_response_still_receives_its_disclaimer():
    sovereign = Sovereign()

    async def fixed_route(request):
        return Intent.SMALL_TALK, DoctrinalSensitivity.NONE, "test"

    async def fixed_answer(**kwargs):
        return "Hello."

    sovereign.router.route = fixed_route
    sovereign.llm.complete = fixed_answer
    response = _run(sovereign.handle(ChatRequest(
        persona=Persona.PATIENT,
        message="Hello there.",
    )))
    assert response.intent == Intent.SMALL_TALK
    assert safety.PATIENT_DISCLAIMER in response.answer


def test_capability_refusal_still_receives_its_disclaimer():
    sovereign = Sovereign()

    async def fixed_route(request):
        return Intent.LAB_REVIEW, DoctrinalSensitivity.LOW, "test"

    class MustNotRunAgent:
        async def run(self, request, profile):
            raise AssertionError("capability refusal must precede the agent")

    sovereign.router.route = fixed_route
    sovereign.agents[Intent.LAB_REVIEW] = MustNotRunAgent()
    response = _run(sovereign.handle(ChatRequest(
        persona=Persona.PATIENT,
        message="Please review these labs.",
    )))
    assert response.safety_notice == "capability_denied"
    assert safety.PATIENT_DISCLAIMER in response.answer
