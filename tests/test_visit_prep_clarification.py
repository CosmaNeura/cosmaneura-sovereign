"""Invented words and offline model transport through the actual metered client."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.clients.llm import LLMClient
from app.clients.platform_conversation import PlatformTurn
from app.clients.platform_usage import (
    ProviderUsageFacts,
    UsageWriteReceipt,
    activate_usage_context,
    deactivate_usage_context,
)
from app.clients.visit_prep_clarification import (
    ClarificationUnavailable,
    ProtectedTopic,
    generate_clarification,
)
from app.config import Settings


def _topic(**changes):
    values = {
        "contract_version": "1",
        "protection_profile": "core_azure_deid_surrogate_2024_11_15_v1",
        "actor_role": "patient",
        "kind": "story",
        "physician_prompt": "Tell me what changed since our last visit.",
        "reported_words": "My invented sleep concern comes first.",
        "turns": [],
    }
    values.update(changes)
    return ProtectedTopic.model_validate_json(json.dumps(values))


def _reply(topic):
    return json.dumps(
        {
            "kind": "organize",
            "prompt_code": None,
            "statements": [
                {
                    "source": "answer",
                    "start": 0,
                    "end": len(topic.reported_words),
                    "quote": topic.reported_words,
                }
            ],
        }
    )


def _run(
    monkeypatch,
    *,
    topic=None,
    output=None,
    report_failure=False,
    settings_changes=None,
    context=True,
    after_model=None,
):
    topic = topic or _topic()
    calls, reports = [], []
    values = {
        "_env_file": None,
        "mock_mode": False,
        "llm_provider": "azure_openai",
        "baa_attested": True,
        "visit_prep_patient_clarification_enabled": True,
        "reasoning_model": "synthetic-deployment",
    }
    values.update(settings_changes or {})
    client = LLMClient(Settings(**values))

    async def model(**kwargs):
        calls.append(kwargs)
        if after_model:
            after_model(client)
        return output if output is not None else _reply(topic), ProviderUsageFacts(
            input_tokens=20, output_tokens=10, usage_source="provider_response"
        )

    monkeypatch.setattr(client, "_complete_unreported", model)

    class Writer:
        async def report(self, *, turn, request):
            reports.append(request)
            if report_failure:
                raise RuntimeError("invented private upstream failure")
            return UsageWriteReceipt(
                record_ref="model_usage_synthetic001", recorded_at=datetime.now(UTC)
            )

    async def scenario():
        if context:
            activate_usage_context(
                turn=PlatformTurn(
                    interaction_ref="interaction_synthetic001",
                    client_turn_id=uuid4(),
                    trace_id="trace_synthetic001",
                    mount_context_token="synthetic_token_123456",
                ),
                client=Writer(),
            )
        try:
            return await generate_clarification(topic, client=client)
        finally:
            deactivate_usage_context()

    return scenario(), calls, reports


def test_generator_uses_one_metered_call_and_only_supplied_patient_excerpts(
    monkeypatch,
):
    scenario, calls, reports = _run(monkeypatch)
    reply = asyncio.run(scenario)
    assert reply.statements[0].quote == _topic().reported_words
    assert len(calls) == len(reports) == 1
    call = calls[0]
    assert call["use_doctrinal_mcp"] is False and call["temperature"] == 0
    assert call["model"] == "synthetic-deployment"
    payload = json.loads(call["prompt"])
    assert set(payload["topic"]) == {
        "contract_version",
        "protection_profile",
        "actor_role",
        "kind",
        "physician_prompt",
        "reported_words",
        "turns",
    }
    assert reports[0].purpose == "visit_prep_patient_clarification"
    assert reports[0].token_usage.input_tokens == 20
    assert _topic().reported_words not in reports[0].model_dump_json()
    assert _topic().reported_words not in repr(reply)


def test_followup_then_organization_preserves_exact_patient_words(monkeypatch):
    question = json.dumps(
        {"kind": "clarify", "prompt_code": "priority", "statements": []}
    )
    scenario, _, _ = _run(monkeypatch, output=question)
    assert asyncio.run(scenario).prompt_code == "priority"
    topic = _topic(
        turns=[{"prompt_code": "priority", "patient_words": "Invented priority."}]
    )
    output = json.dumps(
        {
            "kind": "organize",
            "prompt_code": None,
            "statements": [
                {
                    "source": "clarification_1",
                    "start": 0,
                    "end": 18,
                    "quote": "Invented priority.",
                }
            ],
        }
    )
    scenario, _, _ = _run(monkeypatch, topic=topic, output=output)
    assert asyncio.run(scenario).statements[0].source == "clarification_1"


@pytest.mark.parametrize(
    "changes,context",
    [
        ({"visit_prep_patient_clarification_enabled": False}, True),
        ({"mock_mode": True}, True),
        ({"baa_attested": False}, True),
        ({"llm_provider": "anthropic"}, True),
        ({}, False),
    ],
)
def test_dark_or_unqualified_requests_make_no_model_call(monkeypatch, changes, context):
    scenario, calls, _ = _run(monkeypatch, settings_changes=changes, context=context)
    with pytest.raises(
        ClarificationUnavailable, match="^Visit Prep clarification is unavailable$"
    ):
        asyncio.run(scenario)
    assert not calls


@pytest.mark.parametrize(
    "output",
    [
        '{"kind":"organize","prompt_code":null,"statements":[{"source":"answer",'
        '"start":0,"end":10,"quote":"New advice"}]}',
        '{"kind":"unavailable","prompt_code":null,"statements":[],"instructions":"private"}',
        '{"kind":"unavailable","kind":"organize","prompt_code":null,"statements":[]}',
        '```json\n{"kind":"unavailable","prompt_code":null,"statements":[]}\n```',
        "private" * 1000,
    ],
)
def test_invalid_generated_content_is_not_returned_or_echoed(monkeypatch, output):
    scenario, _, reports = _run(monkeypatch, output=output)
    with pytest.raises(ClarificationUnavailable) as error:
        asyncio.run(scenario)
    assert "private" not in str(error.value) and len(reports) == 1


def test_repeated_followup_and_third_question_are_rejected(monkeypatch):
    topic = _topic(
        turns=[
            {"prompt_code": "priority", "patient_words": "Invented priority."},
            {"prompt_code": "background", "patient_words": "Invented background."},
        ]
    )
    scenario, _, _ = _run(
        monkeypatch,
        topic=topic,
        output=json.dumps(
            {
                "kind": "clarify",
                "prompt_code": "detail",
                "statements": [],
            }
        ),
    )
    with pytest.raises(ClarificationUnavailable):
        asyncio.run(scenario)


def test_usage_failure_and_late_disabled_gate_do_not_disclose_suggestion(monkeypatch):
    for options in (
        {"report_failure": True},
        {
            "after_model": lambda client: setattr(
                client.s, "visit_prep_patient_clarification_enabled", False
            )
        },
    ):
        scenario, calls, _ = _run(monkeypatch, **options)
        with pytest.raises(ClarificationUnavailable):
            asyncio.run(scenario)
        assert len(calls) == 1


def test_obvious_unprotected_identifiers_fail_before_inference(monkeypatch):
    topic = _topic(reported_words="Invented contact: patient@example.com")
    scenario, calls, _ = _run(monkeypatch, topic=topic)
    with pytest.raises(ClarificationUnavailable):
        asyncio.run(scenario)
    assert not calls
