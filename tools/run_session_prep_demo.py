#!/usr/bin/env python3
"""Loopback-only visual Session Prep demo using the real Sovereign runtime path.

Run ``PYTHONPATH=. python tools/run_session_prep_demo.py --self-test`` first,
then omit ``--self-test`` and open the printed loopback URL.
"""
from __future__ import annotations

# ruff: noqa: E402 -- app.main must see the closed demo environment at import time.

import argparse
import asyncio
import json
import os
import uuid
from collections import OrderedDict
from contextvars import ContextVar
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from unittest.mock import patch

# Set a closed synthetic deployment before importing app.main and its singleton.
os.environ.update({
    "MOCK_MODE": "true",
    "MOUNT_PROFILE": "provider_assistant",
    "MOUNT_TENANT_ID": "tenant_session_prep_demo",
    "AUTH_MODE": "disabled",
    "PLATFORM_AGENT_API_ENABLED": "true",
    "PLATFORM_AGENT_API_BASE_URL": "https://core.session-prep.demo",
    "PLATFORM_AGENT_API_SCOPE": "api://session-prep-demo/.default",
    "PLATFORM_WORKLOAD_CLIENT_ID": "session-prep-demo-workload",
    "FEATURE_SESSION_PREP": "true",
    "SESSION_PREP_SYNTHETIC_CONTEXT_REF": "context_synthetic_001",
    "DEMO_UI_ENABLED": "true",
    "FEATURE_ATHENA_LABS": "false",
    "LABS_ALLOW_PATIENT_SEARCH": "false",
    "MAGISTERIUM_MCP_ENABLED": "false",
    "BAA_ATTESTED": "false",
    "CORS_ALLOW_ORIGINS": "[]",
    "DEID_PROVIDER": "regex",
})
for name in (
    "ANTHROPIC_API_KEY", "MAGISTERIUM_API_KEY", "AZURE_OPENAI_API_KEY",
    "ATHENA_CLIENT_ID", "ATHENA_CLIENT_SECRET", "SOVEREIGN_RECORD_GRANT_SECRET",
):
    os.environ[name] = ""

import httpx
import uvicorn
from fastapi import Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from app import main as main_module
from app.clients.platform_conversation import ConversationReceipt, PlatformTurn
from app.clients.session_prep import SessionPrepClient
from app.security.mount import (MountBootstrap, MountBootstrapRequest,
                                MountBootstrapper, MountContext, MountResolution,
                                MountResolveRequest, MountResolver,
                                MountResolutionError)
from app.sovereign.orchestrator import Sovereign


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "contracts/fixtures/provider-session-prep-v1"
DEMO_HTML = ROOT / "tools/session_prep_demo.html"
MOUNT_TOKEN = "synthetic-session-prep-mount-token"
WORKLOAD_TOKEN = "synthetic-session-prep-workload-token"
OBSERVED_AT = datetime(2026, 9, 2, 12, 11, tzinfo=UTC)
DemoMode = Literal["ready", "refreshing", "pending", "unavailable"]
DEMO_RUN_HEADER = "X-Cosmaneura-Demo-Run-ID"
_current_demo_run_id: ContextVar[str | None] = ContextVar(
    "current_demo_run_id", default=None,
)


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class DemoAuthority(MountResolver, MountBootstrapper):
    context = MountContext(
        surface="provider_assistant", auth_tier="provider",
        capabilities=("session-prep:read",), tenant_id="tenant_demo_001",
        actor_id="actor_demo_001",
    )

    @staticmethod
    def _check(token: str | None) -> None:
        if token != MOUNT_TOKEN:
            raise MountResolutionError(
                status_code=401, code="CONTEXT_INVALID", retryable=False,
            )

    async def bootstrap(self, request: MountBootstrapRequest) -> MountBootstrap:
        self._check(request.mount_context_token)
        return MountBootstrap(context=self.context, trace_id=request.trace_id)

    async def resolve(self, request: MountResolveRequest) -> MountResolution:
        self._check(request.mount_context_token)
        return MountResolution(
            context=self.context, client_turn_id=request.client_turn_id,
            trace_id=request.trace_id,
            interaction_ref=f"interaction_demo_{request.client_turn_id.hex}",
        )


class DemoConversation:
    """Synthetic canonical store used to prove the dynamic source-turn fence."""

    def __init__(self) -> None:
        self.accepted: dict[str, dict[str, PlatformTurn]] = {}
        self.final: dict[str, list[dict[str, object]]] = {}

    def start_run(self, run_id: str) -> None:
        self.accepted[run_id] = {}
        self.final[run_id] = []

    def discard_run(self, run_id: str) -> None:
        self.accepted.pop(run_id, None)
        self.final.pop(run_id, None)

    @staticmethod
    def _run_id() -> str:
        run_id = _current_demo_run_id.get()
        if run_id is None:
            raise ValueError("missing synthetic demo run")
        return run_id

    def accepted_for(self, run_id: str) -> dict[str, PlatformTurn]:
        return self.accepted[run_id]

    def final_for(self, run_id: str) -> list[dict[str, object]]:
        return self.final[run_id]

    async def write_provider_action(
        self, *, turn: PlatformTurn, action_code: str, message: str,
    ) -> ConversationReceipt:
        if (action_code, message) != ("session-prep.open", "Open Session Prep"):
            raise ValueError("unexpected provider action")
        run_id = self._run_id()
        turn_ref = f"turn_demo_{turn.client_turn_id.hex}"
        self.accepted_for(run_id)[turn_ref] = turn
        return ConversationReceipt(
            client_turn_id=turn.client_turn_id, turn_ref=turn_ref,
            phase="user_accepted", state="accepted", recorded_at=OBSERVED_AT,
            replayed=False,
        )

    async def write_final_response(self, **values) -> ConversationReceipt:
        run_id = self._run_id()
        turn = values["turn"]
        if values["expected_turn_ref"] not in self.accepted_for(run_id):
            raise ValueError("unknown accepted turn")
        self.final_for(run_id).append({
            "run_id": run_id,
            "state": values["outcome"], "characters": len(values["answer"]),
        })
        return ConversationReceipt(
            client_turn_id=turn.client_turn_id,
            turn_ref=values["expected_turn_ref"], phase="final_response",
            state="completed", recorded_at=OBSERVED_AT, replayed=False,
        )


class SyntheticRun:
    def __init__(self, mode: DemoMode) -> None:
        self.mode = mode
        self.events: list[dict[str, object]] = []
        self.body: bytes | None = None
        self.keys: set[str] = set()


class SyntheticCore:
    """Exact Core v1 HTTP receiver implemented as an in-memory transport."""

    def __init__(self, conversation: DemoConversation) -> None:
        self.conversation = conversation
        self.runs: OrderedDict[str, SyntheticRun] = OrderedDict()

    def start(self, mode: DemoMode) -> str:
        run_id = f"demo_run_{uuid.uuid4().hex}"
        self.runs[run_id] = SyntheticRun(mode)
        self.conversation.start_run(run_id)
        if len(self.runs) > 32:
            stale_run_id, _stale = self.runs.popitem(last=False)
            self.conversation.discard_run(stale_run_id)
        return run_id

    def get(self, run_id: str) -> SyntheticRun:
        try:
            return self.runs[run_id]
        except KeyError:
            raise ValueError("unknown synthetic demo run") from None

    def snapshot(self, run_id: str) -> dict[str, object]:
        run = self.get(run_id)
        return {
            "run_id": run_id,
            "mode": run.mode,
            "calls": deepcopy(run.events),
            "canonical_writes": deepcopy(self.conversation.final_for(run_id)),
        }

    @staticmethod
    def _result_name(mode: DemoMode, call: int) -> str:
        if mode == "ready":
            return (
                "result-pending.json", "result-refreshing-brief.json",
                "result-ready-brief.json",
            )[min(call - 1, 2)]
        return {
            "refreshing": "result-refreshing-brief.json",
            "pending": "result-pending.json",
            "unavailable": "result-unavailable.json",
        }[mode]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        try:
            run_id = _current_demo_run_id.get()
            if run_id is None:
                raise ValueError("missing synthetic demo run")
            run = self.get(run_id)
            body = json.loads(request.content)
            arguments = body["arguments"]
            source_turn_ref = arguments["source_turn_ref"]
            accepted = self.conversation.accepted_for(run_id)[source_turn_ref]
            expected = {
                "tool_name": "session-prep.context.read",
                "tool_version": "1.0.0",
                "subject_ref": None,
                "arguments": {
                    "source_turn_ref": source_turn_ref,
                    "context_snapshot_ref": "context_synthetic_001",
                    "purpose": "session_prep", "detail": "brief",
                },
            }
            valid = (
                request.method == "POST"
                and request.url.path == "/service/v1/agent/tools/invoke"
                and body == expected
                and request.headers["authorization"] == f"Bearer {WORKLOAD_TOKEN}"
                and request.headers["x-cosmaneura-mount-context"] == MOUNT_TOKEN
                and request.headers["x-cosmaneura-interaction-ref"]
                == accepted.interaction_ref
                and request.headers["x-cosmaneura-client-turn-id"]
                == str(accepted.client_turn_id)
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            return httpx.Response(422, json={"retryable": False})

        key = request.headers["idempotency-key"]
        call = len(run.events) + 1
        same_body = run.body is None or run.body == request.content
        rotated_key = key not in run.keys
        run.body = request.content
        run.keys.add(key)
        result = deepcopy(_fixture(self._result_name(run.mode, call)))
        if result["state"] in {"pending", "refreshing"}:
            result["retry_after_seconds"] = 1
        run.events.append({
            "call": call, "core_state": result["state"],
            "method": request.method, "path": request.url.path,
            "tool": body["tool_name"], "version": body["tool_version"],
            "detail": arguments["detail"], "exact_body": body == expected,
            "same_body": same_body, "new_poll_key": rotated_key,
            "authority_headers": True,
        })
        if not rotated_key:
            return httpx.Response(422, json={"retryable": False})
        envelope = {
            "tool_call_ref": f"toolcall_demo_{call:04d}",
            "tool_name": "session-prep.context.read", "tool_version": "1.0.0",
            "status": "completed", "approval_ref": None,
            "access_intent_ref": None, "access_outcome_ref": None,
            "result_classification": "phi_possible", "result": result,
            "recorded_at": OBSERVED_AT.isoformat(),
        }
        return httpx.Response(200, headers={
            "Content-Type": "application/json", "Cache-Control": "no-store",
            "X-Trace-ID": request.headers["x-trace-id"],
            "X-Cosmaneura-Client-Turn-ID": request.headers[
                "x-cosmaneura-client-turn-id"
            ],
            "Idempotency-Replayed": "false",
        }, json=envelope)


async def _workload_token() -> str:
    return WORKLOAD_TOKEN


async def _no_wait(_seconds: float) -> None:
    await asyncio.sleep(0)


conversation = DemoConversation()
core = SyntheticCore(conversation)
authority = DemoAuthority()
http_client = httpx.AsyncClient(transport=httpx.MockTransport(core))
session_prep_client = SessionPrepClient(
    platform_base_url="https://core.session-prep.demo",
    workload_token_provider=_workload_token, client=http_client,
    sleep=_no_wait, wall_clock=lambda: OBSERVED_AT,
)
main_module._sovereign = Sovereign(
    main_module._settings, mount_resolver=authority, mount_bootstrapper=authority,
    platform_audit_client=object(), platform_conversation_client=conversation,
    platform_usage_client=object(), session_prep_client=session_prep_client,
)
app = main_module.app


def _demo_run_id(request: Request) -> str:
    values = request.headers.getlist(DEMO_RUN_HEADER)
    if len(values) != 1:
        raise ValueError("one synthetic demo run header is required")
    run_id = values[0]
    core.get(run_id)
    return run_id


@app.middleware("http")
async def bind_session_prep_demo_run(request: Request, call_next):
    if request.url.path != "/session-prep":
        return await call_next(request)
    try:
        run_id = _demo_run_id(request)
    except ValueError:
        return JSONResponse(
            status_code=409,
            content={"detail": "synthetic demo run unavailable"},
            headers={"Cache-Control": "no-store"},
        )
    token = _current_demo_run_id.set(run_id)
    try:
        return await call_next(request)
    finally:
        _current_demo_run_id.reset(token)


@app.get("/demo/session-prep", response_class=HTMLResponse)
async def session_prep_demo() -> str:
    return DEMO_HTML.read_text(encoding="utf-8")


@app.post("/demo/session-prep/state/{mode}")
async def session_prep_demo_state(mode: DemoMode) -> dict[str, str]:
    return {"mode": mode, "run_id": core.start(mode)}


@app.get("/demo/session-prep/trace")
async def session_prep_demo_trace(
    request: Request, response: Response,
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return core.snapshot(_demo_run_id(request))
    except ValueError:
        return JSONResponse(
            status_code=409,
            content={"detail": "synthetic demo run unavailable"},
            headers={"Cache-Control": "no-store"},
        )


def self_test() -> None:
    from fastapi.testclient import TestClient

    expected: dict[DemoMode, list[str]] = {
        "ready": ["pending", "refreshing", "ready"],
        "refreshing": ["refreshing"] * 3,
        "pending": ["pending"] * 3,
        "unavailable": ["unavailable"],
    }
    expected_outcomes: dict[DemoMode, str] = {
        "ready": "completed",
        "refreshing": "completed",
        "pending": "completed",
        "unavailable": "unavailable",
    }
    observed: dict[str, dict[str, list[dict[str, object]]]] = {}
    with TestClient(app) as client:
        for index, (mode, core_states) in enumerate(expected.items()):
            selected = client.post(f"/demo/session-prep/state/{mode}")
            assert selected.status_code == 200
            selected_body = selected.json()
            assert set(selected_body) == {"mode", "run_id"}
            assert selected_body["mode"] == mode
            run_id = selected_body["run_id"]
            assert run_id.startswith("demo_run_")
            assert len(run_id) == len("demo_run_") + 32
            run_headers = {DEMO_RUN_HEADER: run_id}
            empty_trace = client.get(
                "/demo/session-prep/trace", headers=run_headers,
            )
            assert empty_trace.status_code == 200
            assert empty_trace.json() == {
                "run_id": run_id,
                "mode": mode,
                "calls": [],
                "canonical_writes": [],
            }
            client_turn_id = str(uuid.uuid4())
            headers = {
                "Authorization": f"Bearer {MOUNT_TOKEN}",
                "X-Cosmaneura-Embed-Version": "1.0",
                "X-Cosmaneura-Client-Turn-ID": client_turn_id,
                DEMO_RUN_HEADER: run_id,
            }
            if index == 0:
                welcome = client.get("/welcome", headers=headers)
                assert welcome.status_code == 200
                assert any(path["intent"] == "session_prep"
                           for path in welcome.json()["paths"])
            response = client.post("/session-prep", headers=headers, json={
                "client_turn_id": client_turn_id,
            })
            assert response.status_code == 200
            assert response.json()["state"] == mode
            run = core.get(run_id)
            assert [event["core_state"] for event in run.events] == core_states
            assert all(event["exact_body"] and event["authority_headers"]
                       and event["new_poll_key"] for event in run.events)
            final = conversation.final_for(run_id)
            assert len(final) == 1
            assert set(final[0]) == {"run_id", "state", "characters"}
            assert final[0]["run_id"] == run_id
            assert final[0]["state"] == expected_outcomes[mode]
            assert type(final[0]["characters"]) is int
            assert final[0]["characters"] > 0
            trace = client.get(
                "/demo/session-prep/trace", headers=run_headers,
            )
            assert trace.status_code == 200
            assert trace.headers["Cache-Control"] == "no-store"
            assert trace.json() == core.snapshot(run_id)
            if mode == "ready":
                final.append(deepcopy(final[0]))
                duplicate_trace = client.get(
                    "/demo/session-prep/trace", headers=run_headers,
                )
                assert len(duplicate_trace.json()["canonical_writes"]) == 2
                final.pop()
            observed[mode] = {
                "calls": deepcopy(run.events),
                "canonical_writes": deepcopy(final),
            }

        first = client.post("/demo/session-prep/state/ready").json()
        second = client.post("/demo/session-prep/state/unavailable").json()
        assert first["run_id"] != second["run_id"]
        assert client.get("/demo/session-prep/trace").status_code == 409
        for selected, expected_state, expected_calls in (
            (first, "ready", 3),
            (second, "unavailable", 1),
        ):
            interleaved_turn_id = str(uuid.uuid4())
            interleaved_response = client.post("/session-prep", headers={
                "Authorization": f"Bearer {MOUNT_TOKEN}",
                "X-Cosmaneura-Embed-Version": "1.0",
                "X-Cosmaneura-Client-Turn-ID": interleaved_turn_id,
                DEMO_RUN_HEADER: selected["run_id"],
            }, json={"client_turn_id": interleaved_turn_id})
            assert interleaved_response.status_code == 200
            assert interleaved_response.json()["state"] == expected_state
            interleaved_trace = client.get(
                "/demo/session-prep/trace",
                headers={DEMO_RUN_HEADER: selected["run_id"]},
            ).json()
            assert interleaved_trace["run_id"] == selected["run_id"]
            assert interleaved_trace["mode"] == expected_state
            assert len(interleaved_trace["calls"]) == expected_calls
            assert {
                write["run_id"]
                for write in interleaved_trace["canonical_writes"]
            } == {selected["run_id"]}

        missing_run_turn_id = str(uuid.uuid4())
        assert client.post("/session-prep", headers={
            "Authorization": f"Bearer {MOUNT_TOKEN}",
            "X-Cosmaneura-Embed-Version": "1.0",
            "X-Cosmaneura-Client-Turn-ID": missing_run_turn_id,
        }, json={"client_turn_id": missing_run_turn_id}).status_code == 409

        reused = client.post("/demo/session-prep/state/ready").json()
        reused_run_id = reused["run_id"]
        reused_turn_id = str(uuid.uuid4())
        reused_headers = {
            "Authorization": f"Bearer {MOUNT_TOKEN}",
            "X-Cosmaneura-Embed-Version": "1.0",
            "X-Cosmaneura-Client-Turn-ID": reused_turn_id,
            DEMO_RUN_HEADER: reused_run_id,
        }
        with patch(
            "app.clients.session_prep.derive_operation_token",
            side_effect=("demo_key_a", "demo_key_b", "demo_key_a"),
        ):
            reused_response = client.post(
                "/session-prep",
                headers=reused_headers,
                json={"client_turn_id": reused_turn_id},
            )
        assert reused_response.status_code == 422
        reused_trace = client.get(
            "/demo/session-prep/trace",
            headers={DEMO_RUN_HEADER: reused_run_id},
        ).json()
        assert [event["new_poll_key"] for event in reused_trace["calls"]] == [
            True, True, False,
        ]
        assert reused_trace["canonical_writes"] == []

        rejected_turn_id = str(uuid.uuid4())
        rejected_run = client.post("/demo/session-prep/state/ready").json()
        rejected = client.post("/session-prep", headers={
            "Authorization": f"Bearer {MOUNT_TOKEN}",
            "X-Cosmaneura-Embed-Version": "1.0",
            "X-Cosmaneura-Client-Turn-ID": rejected_turn_id,
            DEMO_RUN_HEADER: rejected_run["run_id"],
        }, json={
            "client_turn_id": rejected_turn_id,
            "context_snapshot_ref": "context_synthetic_browser_forbidden",
        })
        assert rejected.status_code == 422
    sanitized_trace = json.dumps(observed)
    assert MOUNT_TOKEN not in sanitized_trace
    assert "context_synthetic_001" not in sanitized_trace
    assert "turn_demo_" not in sanitized_trace
    print(json.dumps({"status": "ok", "endpoint": "/session-prep",
                      "states": observed}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    print(f"Synthetic Session Prep demo: http://127.0.0.1:{args.port}/demo/session-prep")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
