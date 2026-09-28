"""Optional literature failures never bypass review or erase retrieval limits."""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.agents.ethics_agent import EthicsConsultAgent, LITERATURE_NOTICES
from app.clients.platform_conversation import ConversationDeliveryError
from app.compliance.netsec import UpstreamCallError
from app.compliance.phi import PHIBoundaryViolation
from app.config import ComplianceProfile, Persona, Settings
from app.schemas import ChatRequest, DoctrinalSensitivity, DoctrinalVerdict, Intent
from tests.test_provider_error_recovery import DRAFT, RAW_ERROR, ethics_service, post

ROOT = Path(__file__).resolve().parents[1]
GENERAL = {"pmid": "1", "title": "Synthetic general source", "url": "https://example.invalid/general"}
CATHOLIC = {"pmid": "2", "title": "Synthetic Catholic source", "url": "https://example.invalid/catholic"}


def failed():
    return UpstreamCallError(RAW_ERROR)


def searches(mode):
    return {
        "unavailable": [failed(), failed()],
        "general_only": [[GENERAL], failed()],
        "catholic_only": [failed(), [CATHOLIC]],
        "complete": [[GENERAL, CATHOLIC], [CATHOLIC]],
        "empty": [[], []],
    }[mode]


@pytest.mark.parametrize("mode,status,pmids", [
    ("unavailable", "unavailable", []), ("general_only", "partial", ["1"]),
    ("catholic_only", "partial", ["2"]), ("complete", None, ["2", "1"]),
    ("empty", None, []),
])
def test_search_results_preserve_only_actual_evidence_and_server_owned_status(mode, status, pmids):
    agent = EthicsConsultAgent(Settings(_env_file=None, mock_mode=True))
    agent.llm.classify_json = AsyncMock(return_value={"topic": "clinical ethics"})
    agent.llm.complete = AsyncMock(return_value="Synthetic consult prep")
    agent.pubmed.search = AsyncMock(side_effect=searches(mode))
    request = ChatRequest(persona=Persona.PROVIDER, message="Fictional ethics question.",
                          metadata={"output_format": "document", "literature_status": "unavailable"})
    result = asyncio.run(agent.run(request, ComplianceProfile(Persona.PROVIDER)))
    assert result.data.get("literature_status") == status
    assert result.data["output_format"] == "document"
    assert [item["pmid"] for item in result.data["evidence"]] == pmids
    assert [citation.url for citation in result.citations if citation.source == "PubMed"] == [
        item["url"] for item in result.data["evidence"]
    ]
    assert agent.pubmed.search.call_count == 2 and agent.llm.complete.call_count == 1
    prompt = agent.llm.complete.call_args.kwargs["prompt"]
    assert RAW_ERROR not in prompt
    assert ("Literature limitation:" in prompt) is (status is not None)
    if status:
        assert LITERATURE_NOTICES[status] in prompt and "do not invent publication citations" in prompt
    else:
        assert "literature_status" not in result.data


@pytest.mark.parametrize("error", [asyncio.CancelledError(), PHIBoundaryViolation("synthetic"),
                                   ValueError("synthetic validation"), KeyError("synthetic programming")])
def test_only_typed_retrieval_failures_are_optional(error):
    agent = EthicsConsultAgent(Settings(_env_file=None, mock_mode=True))
    agent.llm.classify_json = AsyncMock(return_value={"topic": "clinical ethics"})
    agent.llm.complete = AsyncMock()
    agent.pubmed.search = AsyncMock(side_effect=error)
    with pytest.raises(type(error)):
        asyncio.run(agent.run(ChatRequest(persona=Persona.PROVIDER, message="Fictional question"),
                              ComplianceProfile(Persona.PROVIDER)))
    assert agent.pubmed.search.call_count == 1
    agent.llm.complete.assert_not_called()


@pytest.mark.parametrize("mode,status", [("unavailable", "unavailable"), ("general_only", "partial"),
                                       ("catholic_only", "partial"), ("complete", None)])
def test_real_review_final_and_document_keep_the_deterministic_limitation(monkeypatch, mode, status):
    service, context, conversation, usage, requests, client = ethics_service(monkeypatch, "none")
    service.agents[Intent.ETHICS_CONSULT].pubmed.search = AsyncMock(side_effect=searches(mode))
    service.arbiter.review = AsyncMock(wraps=service.arbiter.review)
    response = post(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["structured"]["literature_status"] == status if status else "literature_status" not in body["structured"]
    assert body["structured"]["output_format"] == "document"
    assert ("Literature limitation:" in body["answer"]) is (status is not None)
    assert RAW_ERROR not in response.text
    service.arbiter.review.assert_awaited_once()
    assert service.arbiter.review.call_args.kwargs["sensitivity"] == DoctrinalSensitivity.HIGH
    assert requests == ["topic", "draft", "arbitration", "verdict"]
    assert all(record.status == "succeeded" for record in usage)
    final = conversation.write_final_response.call_args.kwargs
    assert final["outcome"] == "completed" and final["answer"] == body["answer"]
    assert final["provider_context_ref"] == context.read.return_value.tool_call_ref
    assert final["history_context_ref"] == service.conversation_read.read_context.return_value.tool_call_ref
    if status:
        assert LITERATURE_NOTICES[status] in final["answer"]
    expected = body["structured"]["evidence"]
    assert [c["url"] for c in body["citations"] if c["source"] == "PubMed"] == [e["url"] for e in expected]
    node = shutil.which("node")
    assert node
    subprocess.run([node, str(ROOT / "tests/panel_document_harness.js"), str(ROOT / "app/panel.js"), "document"],
                   input=json.dumps(body), text=True, check=True, capture_output=True)


def test_doctrinal_replacement_cannot_discard_the_availability_notice(monkeypatch):
    service, _, conversation, _, _, client = ethics_service(monkeypatch, "none")
    service.agents[Intent.ETHICS_CONSULT].pubmed.search = AsyncMock(side_effect=searches("unavailable"))
    service.arbiter.review = AsyncMock(return_value=DoctrinalVerdict(
        conforms=False, rationale="Synthetic review", revised_content="Reviewed replacement without limitation",
    ))
    response = post(client)
    assert response.status_code == 200
    answer = response.json()["answer"]
    assert "Reviewed replacement without limitation" in answer and DRAFT not in answer
    assert answer.count(LITERATURE_NOTICES["unavailable"]) == 1
    assert conversation.write_final_response.call_args.kwargs["answer"] == answer


def test_mandatory_review_failure_still_withholds_the_draft_and_document(monkeypatch):
    service, _, conversation, usage, requests, client = ethics_service(monkeypatch, "arbitration")
    service.agents[Intent.ETHICS_CONSULT].pubmed.search = AsyncMock(side_effect=searches("unavailable"))
    response = post(client)
    assert response.status_code == 200
    body = response.json()
    assert body["safety_notice"] == "upstream_unavailable"
    assert body["structured"] == {} and body["citations"] == [] and body["doctrinal_review"] is None
    assert DRAFT not in body["answer"] and "Literature limitation:" not in body["answer"]
    assert requests == ["topic", "draft", "arbitration"] and usage[-1].status == "failed"
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "unavailable"


def test_final_dependency_refusal_still_blocks_reviewed_optional_literature_answer(monkeypatch):
    service, _, conversation, _, requests, client = ethics_service(monkeypatch, "none")
    service.agents[Intent.ETHICS_CONSULT].pubmed.search = AsyncMock(side_effect=searches("general_only"))
    conversation.write_final_response.side_effect = ConversationDeliveryError("conflict", retryable=False)
    response = post(client)
    assert response.status_code == 409
    assert "answer" not in response.json() and "structured" not in response.json()
    assert DRAFT not in response.text and "Literature limitation:" not in response.text
    assert requests[-1] == "verdict"
