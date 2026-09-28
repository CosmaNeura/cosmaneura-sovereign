"""Engagement output follows the selected format and exposes download metadata."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agents.engagement_agent import EngagementAgent
from app.config import ComplianceProfile, Persona, Settings
from app.schemas import ChatRequest, Intent
from app.sovereign.orchestrator import Sovereign


@pytest.mark.parametrize("selected_format", [None, "chat", "unknown"])
def test_engagement_defaults_and_falls_back_to_chat(monkeypatch, selected_format):
    agent = EngagementAgent(Settings(_env_file=None, mock_mode=True))
    complete = AsyncMock(return_value="Here are some questions for your next visit.")
    monkeypatch.setattr(agent.llm, "complete", complete)
    metadata = {} if selected_format is None else {"output_format": selected_format}

    result = asyncio.run(agent.run(
        ChatRequest(persona=Persona.PATIENT,
                    message="Help me understand this health term.", metadata=metadata),
        ComplianceProfile(Persona.PATIENT),
    ))

    assert result.content == complete.return_value
    assert result.data["output_format"] == "chat"
    assert "Write for a chat panel" in complete.call_args.kwargs["prompt"]
    assert "standalone document" not in complete.call_args.kwargs["prompt"]


def test_engagement_document_requests_markdown_and_returns_download_metadata(monkeypatch):
    agent = EngagementAgent(Settings(_env_file=None, mock_mode=True))
    document = "# Preparing for your visit\n\n## Questions\n\nWhat should I ask?"
    complete = AsyncMock(return_value=document)
    monkeypatch.setattr(agent.llm, "complete", complete)

    result = asyncio.run(agent.run(
        ChatRequest(
            persona=Persona.PROVIDER,
            message="Prepare an explanation of these health terms.",
            metadata={"output_format": "document"},
        ),
        ComplianceProfile(Persona.PROVIDER),
    ))

    assert result.content == document
    assert result.data["output_format"] == "document"
    assert "standalone document in markdown" in complete.call_args.kwargs["prompt"]


def test_engagement_document_metadata_reaches_the_panel_response():
    sovereign = Sovereign(Settings(_env_file=None, mock_mode=True))

    response = asyncio.run(sovereign.handle(ChatRequest(
        persona=Persona.PATIENT,
        message="Help me understand my health history.",
        metadata={"output_format": "document"},
    )))

    assert response.intent == Intent.ENGAGEMENT
    assert response.structured["output_format"] == "document"
    assert response.answer
