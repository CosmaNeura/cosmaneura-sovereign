"""Source-only accepted-map contracts and normal fixed transport; no live records."""

from __future__ import annotations

import asyncio
import copy
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main
from app.clients.conversation_citation_display_contract import (
    CitationReadRequest,
    CitationReadResponse,
    RetainedDisplayRequest,
    RetainedDisplayResponse,
    TimedRetainedDisplayRequest,
    TimedRetainedDisplayResponse,
)
from app.clients.conversation_read import ConversationReadClient, ConversationReadError
from tests.test_conversation_read import projection
from tests.test_provider_question_context import TURN_ID, headers, sovereign, turn


def display(version="1.2.0"):
    value = projection()
    value["schema_version"] = version
    user, assistant = value["history"]["messages"]
    user["reviewed_citations"] = [] if version == "1.1.0" else None
    assistant["reviewed_citations"] = [
        {
            "citation_ref": f"conversation_final_phase_01m00000000000000000000000.{ordinal}",
            "ordinal": ordinal,
            "source_at": "2026-10-01T12:00:00Z",
            "reviewed_at": "2026-10-02T13:00:00Z",
            "section": "Medications",
        }
        for ordinal in (1, 2)
    ]
    if version == "1.2.0":
        user["display_state"] = None
        assistant["display_state"] = "superseded"
    return value


@pytest.mark.parametrize(
    "version,model",
    [("1.1.0", CitationReadResponse), ("1.2.0", RetainedDisplayResponse)],
)
def test_original_order_same_day_identical_sections_remains_distinct(version, model):
    value = display(version)
    assert model.model_validate(value).model_dump(mode="json") == value
    items = value["history"]["messages"][1]["reviewed_citations"]
    items.reverse()
    with pytest.raises(ValidationError):
        model.model_validate(value)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "url",
        "gap",
        "duplicate",
        "prefix",
        "suffix",
        "bool",
        "naive",
        "section",
        "map_missing",
        "user_map",
        "user_state",
        "assistant_null",
        "stale_assistant",
        "wrong_turn",
    ],
)
def test_retained_closed_rejections(mutation):
    value = display()
    user, assistant = value["history"]["messages"]
    items = assistant["reviewed_citations"]
    if mutation == "extra":
        value["record_ref"] = "not-authority"
    elif mutation == "url":
        items[0]["url"] = "https://other.example/"
    elif mutation == "gap":
        items[0]["ordinal"] = 3
    elif mutation == "duplicate":
        items[1] = copy.deepcopy(items[0])
    elif mutation == "prefix":
        items[1]["citation_ref"] = "different_final_phase.2"
    elif mutation == "suffix":
        items[0]["citation_ref"] = items[0]["citation_ref"][:-1] + "2"
    elif mutation == "bool":
        items[0]["ordinal"] = True
    elif mutation == "naive":
        items[0]["source_at"] = "2026-10-01T12:00:00"
    elif mutation == "section":
        items[0]["section"] = "x" * 201
    elif mutation == "map_missing":
        del assistant["reviewed_citations"]
    elif mutation == "user_map":
        user["reviewed_citations"] = []
    elif mutation == "user_state":
        user["display_state"] = "historical"
    elif mutation == "assistant_null":
        assistant["display_state"] = None
    elif mutation == "stale_assistant":
        value["turns"][0].update(state="stale", outcome=None)
    elif mutation == "wrong_turn":
        assistant["client_turn_id"] = "22222222-2222-4222-8222-222222222222"
    with pytest.raises(ValidationError):
        RetainedDisplayResponse.model_validate(value)


@pytest.mark.parametrize(
    "version,request_model,response_model,suffix",
    [
        ("1.1.0", CitationReadRequest, CitationReadResponse, "read-with-citations"),
        ("1.2.0", RetainedDisplayRequest, RetainedDisplayResponse, "read-retained"),
    ],
)
def test_fixed_display_endpoint_preserves_workload_mount_trace_and_read_only_lane(
    version, request_model, response_model, suffix
):
    requests = []

    async def scenario():
        def handler(request):
            requests.append(request)
            return httpx.Response(200, headers=headers(), json=display(version))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = ConversationReadClient(
                platform_base_url="https://platform.example",
                client=http,
                workload_token_provider=AsyncMock(return_value="synthetic-workload"),
            )
            return await client.read_display(
                mount_context_token=turn().mount_context_token,
                trace_id=turn().trace_id,
                request=request_model(
                    schema_version=version, mode="turn", client_turn_id=TURN_ID
                ),
            )

    assert isinstance(asyncio.run(scenario()), response_model)
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/service/v1/agent/conversation-turns/" + suffix
    assert request.headers["authorization"] == "Bearer synthetic-workload"
    assert request.headers["x-cosmaneura-mount-context"] == turn().mount_context_token
    assert request.headers["x-trace-id"] == turn().trace_id
    assert not any(
        name in request.headers
        for name in ["idempotency-key", "x-cosmaneura-interaction-ref"]
    )
    assert json.loads(request.content) == {
        "schema_version": version,
        "mode": "turn",
        "client_turn_id": str(TURN_ID),
    }


@pytest.mark.parametrize(
    "failure", ["trace", "duplicate_json", "extra", "version", "403", "oversize"]
)
def test_transport_refuses_without_retry_or_payload_errors(failure):
    requests = []

    async def scenario():
        def handler(request):
            requests.append(request)
            value, hdr = display(), headers()
            if failure == "trace":
                hdr["X-Trace-ID"] = "wrong"
            if failure == "extra":
                value["unexpected"] = "private-value"
            if failure == "version":
                value["schema_version"] = "1.1.0"
            content = json.dumps(value)
            if failure == "duplicate_json":
                content = content.replace(
                    '"schema_version": "1.2.0"',
                    '"schema_version":"1.2.0","schema_version":"1.2.0"',
                )
            if failure == "oversize":
                content = "x" * (256 * 1024 + 1)
            return httpx.Response(
                403 if failure == "403" else 200, headers=hdr, content=content
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = ConversationReadClient(
                platform_base_url="https://platform.example",
                client=http,
                workload_token_provider=AsyncMock(return_value="synthetic-workload"),
            )
            await client.read_display(
                mount_context_token=turn().mount_context_token,
                trace_id=turn().trace_id,
                request=RetainedDisplayRequest(
                    schema_version="1.2.0", mode="recent", client_turn_id=None
                ),
            )

    with pytest.raises(ConversationReadError) as error:
        asyncio.run(scenario())
    assert str(error.value) == "conversation read unavailable" and len(requests) == 1


@pytest.mark.parametrize(
    "version,endpoint,response_model",
    [
        ("1.1.0", "/embed/history-with-citations", CitationReadResponse),
        ("1.2.0", "/embed/history-retained", RetainedDisplayResponse),
    ],
)
def test_http_separate_contract_no_models_or_context_inference(
    monkeypatch, version, endpoint, response_model
):
    service, _, writes = sovereign()
    service.conversation_read.read_display = AsyncMock(
        return_value=response_model.model_validate(display(version))
    )
    service.mount_resolver.resolve = AsyncMock(
        side_effect=AssertionError("no new inference authority")
    )
    monkeypatch.setattr(main, "_sovereign", service)
    client = TestClient(main.app)
    hdr = {
        "Authorization": "Bearer mount_fictional_example",
        "X-Cosmaneura-Embed-Version": "1.0",
    }
    body = {"schema_version": version, "mode": "recent", "client_turn_id": None}
    result = client.post(endpoint, headers=hdr, json=body)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert result.json() == display(version)
    writes.write_user_accepted.assert_not_called()
    service.router.route.assert_not_called()
    service.conversation_read.read_context.assert_not_called()
    assert (
        client.post(
            endpoint,
            headers=hdr,
            json={**body, "schema_version": "1.2.0" if version == "1.1.0" else "1.1.0"},
        ).status_code
        == 422
    )
    for status in [401, 403, 404, 409, 503]:
        service.conversation_read.read_display.side_effect = ConversationReadError(
            status
        )
        denied = client.post(endpoint, headers=hdr, json=body)
        assert denied.status_code == status and "citation_ref" not in denied.text


def timed_display():
    """Synthetic deadline is explicit server authority, not an answer-date estimate."""
    value = display()
    value["schema_version"] = "1.3.0"
    value["observed_at"] = "2026-11-01T12:00:00Z"
    user, assistant = value["history"]["messages"]
    user["expires_at"] = None
    assistant["expires_at"] = "2026-11-01T12:03:00Z"
    return value


def test_timed_opt_in_roundtrip_keeps_source_dates_and_old_contracts_closed():
    value = timed_display()
    assert TimedRetainedDisplayResponse.model_validate(value).model_dump(mode="json") == value
    for old_model in (CitationReadResponse, RetainedDisplayResponse):
        with pytest.raises(ValidationError):
            old_model.model_validate(value)
    for old_version in ("1.0.0", "1.1.0", "1.2.0"):
        with pytest.raises(ValidationError):
            TimedRetainedDisplayRequest.model_validate(
                {"schema_version": old_version, "mode": "recent", "client_turn_id": None}
            )
    assert value["history"]["messages"][1]["recorded_at"] == "2026-09-28T12:00:00Z"
    assert value["history"]["messages"][1]["reviewed_citations"][0]["source_at"] == "2026-10-01T12:00:00Z"


@pytest.mark.parametrize(
    "mutation",
    ["observed_missing", "expiry_missing", "naive_observed", "offset_observed",
     "numeric_observed", "user_expiry", "null_assistant", "naive_expiry",
     "offset_expiry", "expired", "boundary", "future_recorded", "extra", "map_gap"],
)
def test_timed_metadata_rejects_unverified_or_unusable_deadlines(mutation):
    value = timed_display()
    user, assistant = value["history"]["messages"]
    if mutation == "observed_missing":
        del value["observed_at"]
    elif mutation == "expiry_missing":
        del assistant["expires_at"]
    elif mutation == "naive_observed":
        value["observed_at"] = "2026-11-01T12:00:00"
    elif mutation == "offset_observed":
        value["observed_at"] = "2026-11-01T13:00:00+01:00"
    elif mutation == "numeric_observed":
        value["observed_at"] = 1793534400
    elif mutation == "user_expiry":
        user["expires_at"] = assistant["expires_at"]
    elif mutation == "null_assistant":
        assistant["expires_at"] = None
    elif mutation == "naive_expiry":
        assistant["expires_at"] = "2026-11-01T12:03:00"
    elif mutation == "offset_expiry":
        assistant["expires_at"] = "2026-11-01T13:03:00+01:00"
    elif mutation == "expired":
        assistant["expires_at"] = "2026-11-01T11:59:59Z"
    elif mutation == "boundary":
        assistant["expires_at"] = value["observed_at"]
    elif mutation == "future_recorded":
        assistant["recorded_at"] = "2026-11-01T12:00:01Z"
    elif mutation == "extra":
        assistant["estimated_expires_at"] = assistant["expires_at"]
    elif mutation == "map_gap":
        assistant["reviewed_citations"][0]["ordinal"] = 3
    with pytest.raises(ValidationError):
        TimedRetainedDisplayResponse.model_validate(value)


def test_timed_fixed_transport_preserves_exact_mount_trace_and_turn():
    requests = []

    async def scenario():
        def handler(request):
            requests.append(request)
            return httpx.Response(200, headers=headers(), json=timed_display())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = ConversationReadClient(
                platform_base_url="https://platform.example", client=http,
                workload_token_provider=AsyncMock(return_value="synthetic-workload"),
            )
            return await client.read_display(
                mount_context_token=turn().mount_context_token, trace_id=turn().trace_id,
                request=TimedRetainedDisplayRequest(
                    schema_version="1.3.0", mode="turn", client_turn_id=TURN_ID
                ),
            )

    assert isinstance(asyncio.run(scenario()), TimedRetainedDisplayResponse)
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/service/v1/agent/conversation-turns/read-display-timed"
    assert request.headers["authorization"] == "Bearer synthetic-workload"
    assert request.headers["x-cosmaneura-mount-context"] == turn().mount_context_token
    assert request.headers["x-trace-id"] == turn().trace_id
    assert "idempotency-key" not in request.headers
    assert json.loads(request.content) == {
        "schema_version": "1.3.0", "mode": "turn", "client_turn_id": str(TURN_ID)
    }


def test_timed_http_route_has_existing_auth_bounds_and_no_inference(monkeypatch):
    service, _, writes = sovereign()
    service.conversation_read.read_display = AsyncMock(
        return_value=TimedRetainedDisplayResponse.model_validate(timed_display())
    )
    service.mount_resolver.resolve = AsyncMock(side_effect=AssertionError("no inference"))
    monkeypatch.setattr(main, "_sovereign", service)
    client = TestClient(main.app)
    hdr = {"Authorization": "Bearer mount_fictional_example", "X-Cosmaneura-Embed-Version": "1.0"}
    body = {"schema_version": "1.3.0", "mode": "recent", "client_turn_id": None}
    result = client.post("/embed/history-timed", headers=hdr, json=body)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert result.json() == timed_display()
    writes.write_user_accepted.assert_not_called()
    service.router.route.assert_not_called()
    service.conversation_read.read_context.assert_not_called()
    assert client.post("/embed/history-timed", headers=hdr, json={**body, "schema_version": "1.2.0"}).status_code == 422
    assert client.post("/embed/history-timed", json=body).status_code == 426
    assert client.post("/embed/history-timed", headers={"X-Cosmaneura-Embed-Version": "1.0"}, json=body).status_code == 401
    for status in (401, 403, 404, 409, 503):
        service.conversation_read.read_display.side_effect = ConversationReadError(status)
        denied = client.post("/embed/history-timed", headers=hdr, json=body)
        assert denied.status_code == status and "expires_at" not in denied.text
