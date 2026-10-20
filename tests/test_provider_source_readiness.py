"""Independent reviewed-source answers survive a missing or pending brief."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.clients.provider_question import ProviderQuestionClient
from app.clients.provider_question_contract import (
    ProviderQuestionArguments, ProviderQuestionRequest, ProviderQuestionResponse,
    SourceOnlyProviderQuestionResult,
)
from app.clients.session_prep import SessionPrepDeliveryError
from tests.test_provider_question_context import fixture, handle, headers, sovereign, turn
from tests.test_provider_reviewed_sources import reviewed_response


def source_only_response(brief_state="ready", *, sources_ready=True):
    raw = reviewed_response("ready" if sources_ready else "unavailable")
    result = raw["result"]
    result["schema_version"] = "1.2.0"
    result["brief_state"] = brief_state if sources_ready else "unavailable"
    if result["brief_state"] != "ready":
        result["prepared_brief"] = None
        result["generated_at"] = None
    return raw


def source_only_request():
    return ProviderQuestionRequest(tool_version="1.2.0", arguments=ProviderQuestionArguments(
        source_turn_ref="turn_synthetic_001", detail="reviewed_sources",
    ))


@pytest.mark.parametrize("name", ["ready", "brief-pending", "brief-unavailable", "unavailable"])
def test_shared_core_v12_fixtures_validate_and_roundtrip(name):
    path = (Path(__file__).parents[1] / "contracts/fixtures/provider-question-context-v1.2"
            / f"response-{name}.json")
    raw = json.loads(path.read_text())
    assert ProviderQuestionResponse.model_validate(raw).model_dump(mode="json") == raw


@pytest.mark.parametrize("brief_state", ["ready", "pending", "unavailable"])
def test_source_ready_result_roundtrips_with_independent_brief_status(brief_state):
    raw = source_only_response(brief_state)
    parsed = ProviderQuestionResponse.model_validate(raw)
    assert isinstance(parsed.result, SourceOnlyProviderQuestionResult)
    assert parsed.model_dump(mode="json") == raw
    content = json.loads(parsed.result.prompt_text())
    assert content["brief_state"] == brief_state
    assert "148/92" in parsed.result.prompt_text()
    if brief_state != "ready":
        assert content["summary"] is None
    assert "Full notes were not read" not in parsed.result.source_notice()


def test_no_reviewed_sources_remains_unavailable_and_cannot_enter_drafting():
    parsed = ProviderQuestionResponse.model_validate(source_only_response(sources_ready=False))
    assert parsed.result.state == "unavailable"
    with pytest.raises(ValueError):
        parsed.result.prompt_text()
    service, context, _ = sovereign()
    context.read.return_value = parsed
    response = handle(service)
    assert response.safety_notice == "provider_context_unavailable"
    service.llm.complete.assert_not_called()
    service.arbiter.review.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("state", "pending"), ("reason_code", "generation_pending"),
    ("brief_state", "ready"), ("current_through", "2026-10-08T14:30:00Z"),
    ("generated_at", "2026-10-20T12:00:00Z"), ("provenance", []),
    ("reviewed_sources", []),
])
def test_missing_brief_does_not_relax_source_authority_or_content_invariants(field, value):
    raw = source_only_response("unavailable")
    raw["result"][field] = value
    with pytest.raises(ValidationError):
        ProviderQuestionResponse.model_validate(raw)


@pytest.mark.parametrize("brief_state", ["pending", "unavailable"])
def test_factual_answer_can_finalize_from_sources_without_building_or_waiting_for_brief(brief_state):
    service, context, conversation = sovereign()
    context.read.return_value = ProviderQuestionResponse.model_validate(source_only_response(brief_state))
    response = handle(service, "What office blood pressure was documented on September 30?")
    prompt = service.llm.complete.call_args.kwargs["prompt"]
    assert "148/92" in prompt
    assert '"summary": null' in prompt
    assert context.read.call_count == 1
    assert service.session_prep.read.call_count == 0
    assert conversation.write_final_response.call_args.kwargs["outcome"] == "completed"
    assert conversation.write_final_response.call_args.kwargs["provider_context_ref"] == context.read.return_value.tool_call_ref
    assert response.safety_notice != "provider_context_unavailable"
    assert set(response.presentation.suppress) == {"intent_badge", "doctrinal_badge"}


@pytest.mark.parametrize("supported_version", ["1.2.0", "1.1.0", "1.0.0"])
def test_unsupported_versions_fallback_in_order_without_changing_authority(supported_version):
    sent = []
    responses = {"1.2.0": source_only_response("unavailable"),
                 "1.1.0": reviewed_response(), "1.0.0": fixture()}

    def handler(request):
        sent.append(request)
        version = json.loads(request.content)["tool_version"]
        if version != supported_version:
            return httpx.Response(404, json={"code": "RESOURCE_NOT_FOUND",
                                            "detail": "The requested tool is not available."})
        return httpx.Response(200, headers=headers(), json=responses[version])

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = ProviderQuestionClient(platform_base_url="https://platform.example", client=http,
                                            workload_token_provider=AsyncMock(return_value="synthetic-token"))
            return await client.read(turn=turn(), request=source_only_request())

    response = asyncio.run(scenario())
    versions = ["1.2.0", "1.1.0", "1.0.0"]
    assert [json.loads(request.content)["tool_version"] for request in sent] == versions[:versions.index(supported_version) + 1]
    assert response.result.schema_version == supported_version
    assert len({r.headers["idempotency-key"] for r in sent}) == len(sent)
    assert all(r.headers["x-cosmaneura-mount-context"] == turn().mount_context_token for r in sent)
    assert all(json.loads(r.content)["arguments"]["source_turn_ref"] == "turn_synthetic_001" for r in sent)


def test_v12_auth_failure_never_falls_back():
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(403, json={"code": "RESOURCE_NOT_FOUND",
                                        "detail": "The requested tool is not available."})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = ProviderQuestionClient(platform_base_url="https://platform.example", client=http,
                                            workload_token_provider=AsyncMock(return_value="synthetic-token"))
            return await client.read(turn=turn(), request=source_only_request())

    with pytest.raises(SessionPrepDeliveryError):
        asyncio.run(scenario())
    assert len(sent) == 1
