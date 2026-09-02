"""Operational Session Prep client, projection, and authority regressions."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.clients.platform_conversation import (ConversationReceipt,
                                               PlatformConversationClient,
                                               PlatformTurn)
from app.clients.session_prep import (MAX_SESSION_PREP_RESPONSE_BYTES,
                                      SessionPrepClient,
                                      SessionPrepDeliveryError)
from app.clients.session_prep_contract import (SessionPrepContextReadRequest,
                                               SessionPrepContextReadResponse,
                                               parse_session_prep_result)
from app.config import (AuthMode, DeploymentConfigurationError, MountProfile,
                        Settings, validate_deployment_configuration)
from app.security.mount import (MountContext, MountResolution,
                                MountResolutionError, MountResolver)
from app.session_prep import project_session_prep_result, render_session_prep_text
from app.sovereign.orchestrator import Sovereign

FIXTURES = Path(__file__).parents[1] / "contracts/fixtures/provider-session-prep-v1"
TURN_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OBSERVED_AT = datetime(2026, 9, 2, 12, 11, tzinfo=UTC)


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

def _turn() -> PlatformTurn:
    return PlatformTurn(
        interaction_ref="interaction_session_prep_0001",
        client_turn_id=TURN_ID,
        trace_id="trace_session_prep_00000001",
        mount_context_token="mount_context_session_prep_0001",
    )

def _request() -> SessionPrepContextReadRequest:
    return SessionPrepContextReadRequest.model_validate(_fixture("request-brief.json"))

def _envelope(result_name: str) -> dict[str, object]:
    return {
        "tool_call_ref": "toolcall_session_prep_0001",
        "tool_name": "session-prep.context.read",
        "tool_version": "1.0.0",
        "status": "completed",
        "approval_ref": None,
        "access_intent_ref": None,
        "access_outcome_ref": None,
        "result_classification": "phi_possible",
        "result": _fixture(result_name),
        "recorded_at": "2026-09-02T12:11:00Z",
    }

def _success(result_name: str, *, retry_after_seconds: int | None = None) -> httpx.Response:
    body = _envelope(result_name)
    if retry_after_seconds is not None:
        body["result"]["retry_after_seconds"] = retry_after_seconds  # type: ignore[index]
    return httpx.Response(
        200,
        headers={
            "Content-Type": "application/json",
            "X-Trace-ID": _turn().trace_id,
            "X-Cosmaneura-Client-Turn-ID": str(TURN_ID),
            "Idempotency-Replayed": "false",
        },
        json=body,
    )

class _Time:
    def __init__(self) -> None: self.value = 0.0
    def clock(self) -> float: return self.value
    async def sleep(self, seconds: float) -> None: self.value += seconds

def _run(coro):
    return asyncio.run(coro)

async def _token() -> str:
    return "workload-token"

async def _read(handler, *, follow_redirects: bool = False) -> SessionPrepContextReadResponse:
    clock = _Time()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=follow_redirects,
    ) as http_client:
        return await SessionPrepClient(
            platform_base_url="https://platform.example",
            workload_token_provider=_token,
            client=http_client,
            sleep=clock.sleep,
            clock=clock.clock,
            wall_clock=lambda: OBSERVED_AT,
        ).read(turn=_turn(), request=_request())

def test_client_polls_three_states_with_one_body_and_distinct_poll_keys() -> None:
    requests: list[httpx.Request] = []
    responses = iter([
        _success("result-pending.json", retry_after_seconds=1),
        _success("result-refreshing-brief.json", retry_after_seconds=1),
        _success("result-ready-brief.json"),
    ])
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return next(responses)
    response = _run(_read(handler))
    assert response.result.state == "ready"
    assert len(requests) == 3
    assert len({request.content for request in requests}) == 1
    assert len({request.headers["idempotency-key"] for request in requests}) == 3
    assert all(r.headers["x-cosmaneura-client-turn-id"] == str(TURN_ID) for r in requests)
    assert all(r.headers["x-cosmaneura-interaction-ref"] == _turn().interaction_ref
               for r in requests)

def test_transport_retry_reuses_key_then_poll_gets_a_new_key() -> None:
    requests: list[httpx.Request] = []
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ConnectError("sentinel upstream detail", request=request)
        if len(requests) == 2:
            return _success("result-pending.json")
        return _success("result-ready-brief.json")
    assert _run(_read(handler)).result.state == "ready"
    keys = [request.headers["idempotency-key"] for request in requests]
    assert keys[0] == keys[1]
    assert keys[1] != keys[2]
    assert len(requests) == 3

def test_poll_delay_cannot_exceed_total_deadline() -> None:
    requests: list[httpx.Request] = []
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _success("result-pending.json", retry_after_seconds=10)
    assert _run(_read(handler)).result.state == "pending"
    assert len(requests) == 1

def test_mid_poll_revocation_discards_the_refreshing_brief() -> None:
    calls = 0
    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _success("result-refreshing-brief.json", retry_after_seconds=1)
        return httpx.Response(401, json={"code": "CONTEXT_EXPIRED", "retryable": False})
    with pytest.raises(SessionPrepDeliveryError) as caught:
        _run(_read(handler))
    assert (caught.value.category, caught.value.status_code, calls) == ("auth", 401, 2)

@pytest.mark.parametrize("failure", [
    "oversized", "declared", "encoded", "duplicate", "recursive",
    "trace-duplicate", "replay-duplicate", "turn-missing", "turn-duplicate",
    "turn-mismatch", "turn-noncanonical",
])
def test_client_rejects_unbounded_or_ambiguous_responses(failure: str) -> None:
    raw = json.dumps(_envelope("result-ready-brief.json")).encode()
    def handler(_request: httpx.Request) -> httpx.Response:
        headers: list[tuple[str, str]] = [
            ("Content-Type", "application/json"),
            ("X-Trace-ID", _turn().trace_id),
            ("X-Cosmaneura-Client-Turn-ID", str(TURN_ID)),
            ("Idempotency-Replayed", "false"),
        ]
        content = raw
        if failure == "oversized":
            content = b"{" + b"x" * MAX_SESSION_PREP_RESPONSE_BYTES
        elif failure == "declared":
            headers.append(("Content-Length", str(MAX_SESSION_PREP_RESPONSE_BYTES + 1)))
        elif failure == "encoded":
            headers.append(("Content-Encoding", "gzip"))
        elif failure == "duplicate":
            content = raw.replace(b'{"tool_call_ref":', b'{"tool_call_ref":"duplicate","tool_call_ref":', 1)
        elif failure == "recursive":
            content = b"[" * 1100 + b"]" * 1100
        elif failure == "trace-duplicate":
            headers.append(("X-Trace-ID", _turn().trace_id))
        elif failure == "replay-duplicate":
            headers.append(("Idempotency-Replayed", "false"))
        elif failure == "turn-missing":
            headers = [item for item in headers if item[0] != "X-Cosmaneura-Client-Turn-ID"]
        elif failure == "turn-duplicate":
            headers.append(("X-Cosmaneura-Client-Turn-ID", str(TURN_ID)))
        elif failure == "turn-mismatch":
            headers[2] = ("X-Cosmaneura-Client-Turn-ID", str(uuid.uuid4()))
        elif failure == "turn-noncanonical":
            headers[2] = ("X-Cosmaneura-Client-Turn-ID", str(TURN_ID).upper())
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(content))
    with pytest.raises(SessionPrepDeliveryError) as caught:
        _run(_read(handler))
    assert caught.value.category == "schema"
    assert "duplicate" not in str(caught.value)

def test_client_never_follows_redirects_from_an_injected_transport() -> None:
    requests: list[httpx.Request] = []
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            307, headers={"Location": "https://platform.example/other"}
        )
    with pytest.raises(SessionPrepDeliveryError) as caught:
        _run(_read(handler, follow_redirects=True))
    assert caught.value.category == "unavailable"
    assert len(requests) == 1

def test_projection_is_direct_and_removes_all_server_side_lineage() -> None:
    source = parse_session_prep_result(_fixture("result-ready-supporting-context.json"))
    projected = project_session_prep_result(source, client_turn_id=TURN_ID)
    wire = projected.model_dump(mode="json")
    serialized = projected.model_dump_json()
    assert wire["prepared_brief"] == {
        "recap": source.prepared_brief.recap.text,
        "trajectory_and_material_changes": [item.text for item in
                                                source.prepared_brief.trajectory_and_material_changes],
        "talking_points": [item.text for item in source.prepared_brief.talking_points],
        "open_threads": [item.text for item in source.prepared_brief.open_threads],
        "cautions": [item.text for item in source.prepared_brief.cautions],
        "suggested_outline": [item.text for item in source.prepared_brief.suggested_outline],
        "patient_reported": {
            "label": source.prepared_brief.patient_reported.label,
            "summary": source.prepared_brief.patient_reported.summary,
        },
    }
    for forbidden in (
        "source_ref", "snapshot_ref", "brief_ref", "provenance",
        "supporting_context", "generator_version", "prompt_version", "digest",
    ):
        assert forbidden not in serialized
    text = render_session_prep_text(projected)
    assert source.prepared_brief.recap.text in text
    assert "2 sources" in text

@pytest.mark.parametrize("section,text", [
    ("recap", "The diagnosis is pneumonia."),
    ("trajectory_and_material_changes", "Start taking lisinopril now."),
    ("talking_points", "Stop taking metformin."),
    ("open_threads", "Increase your dose to 20 mg."),
    ("cautions", "Decrease the medication dose."),
    ("suggested_outline", "Avoid urgent care tonight."),
    ("patient_reported", "Delay emergency care until tomorrow."),
])
def test_unsafe_text_in_every_brief_section_fails_to_content_free_unavailable(
    section: str, text: str,
) -> None:
    result = _fixture(
        "result-ready-supporting-context.json"
        if section == "patient_reported" else "result-ready-brief.json"
    )
    brief = result["prepared_brief"]  # type: ignore[index]
    item = {"text": text, "source_refs": ["note_synthetic_001"]}
    if section == "recap":
        brief[section]["text"] = text  # type: ignore[index]
    elif section == "patient_reported":
        brief[section]["summary"] = text  # type: ignore[index]
    else:
        brief[section] = [item]  # type: ignore[index]
    projection = project_session_prep_result(
        parse_session_prep_result(result), client_turn_id=TURN_ID,
    )
    assert projection.state == "unavailable" and projection.prepared_brief is None
    assert text not in projection.model_dump_json()

def test_maximal_valid_brief_fails_closed_before_an_oversized_canonical_write() -> None:
    result = _fixture("result-ready-brief.json")
    brief = result["prepared_brief"]  # type: ignore[index]
    brief["recap"]["text"] = "x" * 4_000  # type: ignore[index]
    item = {"text": "x" * 1_000, "source_refs": ["note_synthetic_001"]}
    for section in (
        "trajectory_and_material_changes", "talking_points", "open_threads",
        "cautions", "suggested_outline",
    ):
        brief[section] = [item] * 8  # type: ignore[index]
    projection = project_session_prep_result(
        parse_session_prep_result(result), client_turn_id=TURN_ID,
    )
    assert projection.state == "unavailable" and projection.prepared_brief is None

def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "mount_profile": MountProfile.PROVIDER_ASSISTANT,
        "mount_tenant_id": "tenant_synthetic",
        "auth_mode": AuthMode.DISABLED,
        "platform_agent_api_enabled": True,
        "platform_agent_api_base_url": "https://platform.example",
        "platform_agent_api_scope": "api://platform-app/.default",
        "platform_workload_client_id": "workload-client",
        "feature_session_prep": True,
        "session_prep_synthetic_context_ref": "context_synthetic_001",
    }
    values.update(updates)
    return Settings(**values)

class _Resolver(MountResolver):
    def __init__(self, *, capability: bool = True, error: Exception | None = None):
        self.capability = capability
        self.error = error
        self.calls = 0
    async def resolve(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        capabilities = ("session-prep:read",) if self.capability else ()
        return MountResolution(
            context=MountContext(
                surface="provider_assistant",
                auth_tier="provider",
                capabilities=capabilities,
                tenant_id="tenant_synthetic",
                actor_id="actor_synthetic",
            ),
            client_turn_id=request.client_turn_id,
            trace_id=request.trace_id,
            interaction_ref="interaction_session_prep_0001",
        )

class _Conversation:
    def __init__(self) -> None:
        self.accepted: list[dict[str, object]] = []
        self.final: list[dict[str, object]] = []
    async def write_provider_action(self, **kwargs):
        self.accepted.append(kwargs)
        turn = kwargs["turn"]
        return ConversationReceipt(
            client_turn_id=turn.client_turn_id,
            turn_ref="turn_session_prep_0001",
            phase="user_accepted",
            state="accepted",
            recorded_at=OBSERVED_AT,
            replayed=False,
        )
    async def write_final_response(self, **kwargs):
        self.final.append(kwargs)
        turn = kwargs["turn"]
        return ConversationReceipt(
            client_turn_id=turn.client_turn_id,
            turn_ref=kwargs["expected_turn_ref"],
            phase="final_response",
            state="completed",
            recorded_at=OBSERVED_AT,
            replayed=False,
        )

class _SessionPrep:
    def __init__(self, result_name: str = "result-ready-brief.json") -> None:
        self.calls: list[dict[str, object]] = []
        self.response = SessionPrepContextReadResponse.model_validate(
            _envelope(result_name)
        )
    async def read(self, **kwargs):
        self.calls.append(kwargs)
        return self.response

def _sovereign(
    *,
    settings: Settings | None = None,
    resolver: _Resolver | None = None,
    session: _SessionPrep | None = None,
    conversation: _Conversation | None = None,
) -> tuple[Sovereign, _Resolver, _SessionPrep, _Conversation]:
    resolver = resolver or _Resolver()
    session = session or _SessionPrep()
    conversation = conversation or _Conversation()
    sovereign = Sovereign(
        settings or _settings(),
        mount_resolver=resolver,
        mount_bootstrapper=object(),  # not used by this path
        platform_audit_client=object(),
        platform_conversation_client=conversation,
        platform_usage_client=object(),
        session_prep_client=session,
    )
    return sovereign, resolver, session, conversation

def test_sovereign_uses_canonical_action_and_never_calls_a_model() -> None:
    sovereign, _resolver, session, conversation = _sovereign()
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Session Prep must not call a model or arbiter")
    sovereign.llm.complete = forbidden
    sovereign.magisterium.chat = forbidden
    sovereign.arbiter.review = forbidden
    outcome = _run(sovereign.read_session_prep(
        client_turn_id=TURN_ID,
        context_snapshot_ref="context_synthetic_001",
        mount_context_token="mount_context_session_prep_0001",
    ))
    assert outcome.response.state == "ready"
    assert conversation.accepted[0]["action_code"] == "session-prep.open"
    request = session.calls[0]["request"]
    assert request.arguments.source_turn_ref == "turn_session_prep_0001"
    assert request.arguments.context_snapshot_ref == "context_synthetic_001"
    assert request.arguments.detail.value == "brief"
    assert conversation.final[0]["answer"] == render_session_prep_text(outcome.response)
    unsafe = _envelope("result-ready-brief.json")
    unsafe["result"]["prepared_brief"]["recap"]["text"] = "Stop taking medication."  # type: ignore[index]
    session.response = SessionPrepContextReadResponse.model_validate(unsafe)
    blocked = _run(sovereign.read_session_prep(
        client_turn_id=TURN_ID,
        context_snapshot_ref="context_synthetic_001",
        mount_context_token="mount_context_session_prep_0001",
    ))
    assert blocked.response.state == "unavailable"
    assert "Stop taking" not in conversation.final[-1]["answer"]

@pytest.mark.parametrize(
    "settings,resolver,error_code",
    [
        (_settings(feature_session_prep=False), _Resolver(), "CAPABILITY_DENIED"),
        (_settings(session_prep_synthetic_context_ref=None), _Resolver(), "RECORD_CONTEXT_MISMATCH"),
        (_settings(), _Resolver(capability=False), "CAPABILITY_DENIED"),
        (
            _settings(),
            _Resolver(error=MountResolutionError(
                status_code=401, code="CONTEXT_EXPIRED", retryable=False,
            )),
            "CONTEXT_EXPIRED",
        ),
    ],
)
def test_disabled_mismatched_or_revoked_context_never_reaches_core(
    settings: Settings,
    resolver: _Resolver,
    error_code: str,
) -> None:
    session = _SessionPrep()
    conversation = _Conversation()
    sovereign, _resolver, _session, _conversation = _sovereign(
        settings=settings, resolver=resolver, session=session,
        conversation=conversation,
    )
    with pytest.raises(MountResolutionError) as caught:
        _run(sovereign.read_session_prep(
            client_turn_id=TURN_ID,
            context_snapshot_ref="context_synthetic_wrong",
            mount_context_token="mount_context_session_prep_0001",
        ))
    assert caught.value.code == error_code
    assert session.calls == []
    assert conversation.accepted == []

def test_feature_configuration_is_default_off_and_synthetic_only() -> None:
    assert Settings().feature_session_prep is False
    with pytest.raises(DeploymentConfigurationError, match="platform Agent API"):
        validate_deployment_configuration(Settings(
            mount_profile=MountProfile.PROVIDER_ASSISTANT,
            mount_tenant_id="tenant_synthetic",
            auth_mode=AuthMode.DEV_HS256,
            feature_session_prep=True,
            session_prep_synthetic_context_ref="context_synthetic_001",
        ))
    with pytest.raises(DeploymentConfigurationError, match="exact synthetic staging"):
        validate_deployment_configuration(_settings(
            session_prep_synthetic_context_ref="context_real_001",
        ))
    validate_deployment_configuration(_settings())

def test_provider_action_uses_the_ratified_action_ingress_shape() -> None:
    requests: list[httpx.Request] = []
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            headers={
                "X-Trace-ID": _turn().trace_id,
                "X-Cosmaneura-Client-Turn-ID": str(TURN_ID),
                "Idempotency-Replayed": "false",
            },
            json={
                "client_turn_id": str(TURN_ID),
                "turn_ref": "turn_session_prep_0001",
                "phase": "user_accepted",
                "state": "accepted",
                "recorded_at": OBSERVED_AT.isoformat(),
            },
        )
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = PlatformConversationClient(
                platform_base_url="https://platform.example",
                workload_token_provider=_token,
                client=http_client,
            )
            return await client.write_provider_action(
                turn=_turn(),
                action_code="session-prep.open",
                message="Open Session Prep",
            )
    _run(scenario())
    assert json.loads(requests[0].content) == {
        "phase": "user_accepted",
        "ingress_kind": "provider_ui_action",
        "action_code": "session-prep.open",
        "message": {"content_type": "text/plain", "content": "Open Session Prep"},
    }
