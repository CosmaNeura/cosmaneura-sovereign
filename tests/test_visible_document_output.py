"""Visible formats keep governed finals and public Concierge context intact."""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.agents.concierge_agent import ConciergeAgent
from app.clients.conversation_read import ConversationMessage
from app.clients.platform_conversation import ConversationDeliveryError
from app.config import ComplianceProfile, Persona, Settings
from app.schemas import ChatRequest, DoctrinalSensitivity, Intent
from tests.test_conversation_read import memory
from tests.test_provider_question_context import TURN_ID, sovereign

DOCUMENT = "# Fictional trial overview\n\n## Questions\n\nAsk your physician about eligibility."
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("selected, expected", [
    (None, "chat"), ("chat", "chat"), ("unknown", "chat"),
    ("document", "document"), ("DOCUMENT", "document"), ("note", "note"),
])
def test_concierge_format_preserves_scrubbed_followup_and_public_data(selected, expected):
    agent = ConciergeAgent(Settings(_env_file=None, mock_mode=True, magisterium_mcp_enabled=True))
    agent.llm.classify_json = AsyncMock(return_value={
        "condition": "fictional sleep study", "sponsor": "synthetic@example.invalid",
    })
    agent.llm.complete = AsyncMock(side_effect=["Fictional moral advisory", DOCUMENT])
    studies = [{"title": "Fictional diary study", "url": "https://example.invalid/trial"}]
    agent.trials.search = AsyncMock(return_value=studies)
    metadata = {} if selected is None else {"output_format": selected}
    request = ChatRequest(persona=Persona.PROVIDER, message="Explain that trial.", metadata=metadata)
    request = request.model_copy(update={"history": [
        ConversationMessage(role="user", content="Fictional sleep study for synthetic@example.invalid"),
        ConversationMessage(role="assistant", content="The study compares fictional sleep diaries."),
    ]})

    result = asyncio.run(agent.run(request, ComplianceProfile(Persona.PROVIDER)))

    assert result.content == DOCUMENT
    assert result.data == {"trials": studies, "doctrinal_mcp_consulted": True, "output_format": expected}
    assert result.citations[0].url == studies[0]["url"]
    draft = agent.llm.complete.call_args.kwargs
    assert {"chat": "Write for a chat panel", "document": "standalone document in markdown",
            "note": "ready to paste into a note"}[expected] in draft["prompt"]
    assert "study compares fictional sleep diaries" in draft["prompt"]
    assert "not system instructions" in draft["system"]
    assert "informational, not medical advice" in draft["system"]
    assert "Fictional moral advisory" in draft["prompt"]
    assert not draft.get("use_doctrinal_mcp")
    advisory = agent.llm.complete.call_args_list[0].kwargs
    assert advisory["use_doctrinal_mcp"] is True
    assert advisory["prompt"] == "Topic: fictional sleep study"
    assert agent.trials.search.call_args.kwargs["sponsor"] == "[REDACTED:EMAIL]"
    for calls in (agent.llm.classify_json.call_args, agent.llm.complete.call_args_list,
                  agent.trials.search.call_args):
        assert "synthetic@example.invalid" not in str(calls)


def chat_client(monkeypatch, intent):
    service, _, conversation = sovereign(needs_context=False)
    service.router.route.return_value = (intent, DoctrinalSensitivity.LOW, "")
    service.conversation_read.read_context.return_value = memory()
    service.llm.complete.return_value = DOCUMENT
    if intent == Intent.CONCIERGE:
        service.llm.classify_json = AsyncMock(return_value={"condition": "fictional sleep study"})
        service.agents[intent].trials.search = AsyncMock(return_value=[])
    monkeypatch.setattr(main_module, "_settings", service.s)
    monkeypatch.setattr(main_module, "_sovereign", service)
    return service, conversation, TestClient(main_module.app)


def post_chat(client, selected):
    return client.post("/chat", headers={
        "Authorization": "Bearer mount_fictional_example",
        "X-Cosmaneura-Embed-Version": "1.0",
        "X-Cosmaneura-Client-Turn-ID": str(TURN_ID),
    }, json={
        "client_turn_id": str(TURN_ID), "message": "Explain that more briefly.",
        "metadata": {} if selected is None else {"output_format": selected},
    })


@pytest.mark.parametrize("intent", [Intent.CONCIERGE, Intent.SMALL_TALK])
@pytest.mark.parametrize("selected", [None, "document"])
def test_http_format_reaches_final_and_existing_panel_download(monkeypatch, intent, selected):
    service, conversation, client = chat_client(monkeypatch, intent)
    response = post_chat(client, selected)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == intent.value
    assert body["structured"]["output_format"] == (selected or "chat")
    assert body["answer"].startswith(DOCUMENT)
    draft = service.llm.complete.call_args.kwargs
    assert ("standalone document in markdown" if selected else "Write for a chat panel") in draft["prompt"]
    assert "fictional diary records bedtime" in draft["prompt"]
    final = conversation.write_final_response.call_args.kwargs
    assert final["answer"] == body["answer"]
    assert final["presentation"] == body["presentation"]
    assert final["outcome"] == "completed"
    assert final["history_context_ref"] == memory().tool_call_ref
    assert "structured" not in final  # History restores canonical text/presentation only.
    assert conversation.write_user_accepted.call_count == 1
    assert conversation.write_final_response.call_count == 1
    node = shutil.which("node")
    assert node, "Node is required to exercise the existing panel download contract"
    subprocess.run([node, str(ROOT / "tests/panel_document_harness.js"),
                    str(ROOT / "app/panel.js"), selected or "chat"],
                   input=json.dumps(body), text=True, check=True, capture_output=True)


@pytest.mark.parametrize("intent", [Intent.CONCIERGE, Intent.SMALL_TALK])
def test_document_final_conflict_and_accepted_replay_never_egress_or_redraft(monkeypatch, intent):
    service, conversation, client = chat_client(monkeypatch, intent)
    conversation.write_final_response.side_effect = ConversationDeliveryError("conflict", retryable=False)
    failed = post_chat(client, "document")
    assert failed.status_code == 409
    assert DOCUMENT not in failed.text and "structured" not in failed.json()
    assert service.llm.complete.call_count == 1
    assert conversation.write_final_response.call_args.kwargs["history_context_ref"] == memory().tool_call_ref
    conversation.write_user_accepted.return_value = replace(
        conversation.write_user_accepted.return_value, replayed=True,
    )
    replay = post_chat(client, "document")
    assert replay.status_code == 503
    assert replay.json()["code"] == "SERVICE_UNAVAILABLE"
    assert replay.json()["retryable"] is False
    assert DOCUMENT not in replay.text
    assert service.llm.complete.call_count == 1
    assert conversation.write_final_response.call_count == 1
