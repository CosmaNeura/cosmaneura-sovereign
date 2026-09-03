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
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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
from fastapi import Response
from fastapi.responses import HTMLResponse

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
        self.accepted: dict[str, PlatformTurn] = {}
        self.final: list[dict[str, object]] = []

    async def write_provider_action(
        self, *, turn: PlatformTurn, action_code: str, message: str,
    ) -> ConversationReceipt:
        if (action_code, message) != ("session-prep.open", "Open Session Prep"):
            raise ValueError("unexpected provider action")
        turn_ref = f"turn_demo_{turn.client_turn_id.hex}"
        self.accepted[turn_ref] = turn
        return ConversationReceipt(
            client_turn_id=turn.client_turn_id, turn_ref=turn_ref,
            phase="user_accepted", state="accepted", recorded_at=OBSERVED_AT,
            replayed=False,
        )

    async def write_final_response(self, **values) -> ConversationReceipt:
        turn = values["turn"]
        if values["expected_turn_ref"] not in self.accepted:
            raise ValueError("unknown accepted turn")
        self.final.append({
            "state": values["outcome"], "characters": len(values["answer"]),
        })
        return ConversationReceipt(
            client_turn_id=turn.client_turn_id,
            turn_ref=values["expected_turn_ref"], phase="final_response",
            state="completed", recorded_at=OBSERVED_AT, replayed=False,
        )


class SyntheticCore:
    """Exact Core v1 HTTP receiver implemented as an in-memory transport."""

    def __init__(self, conversation: DemoConversation) -> None:
        self.conversation = conversation
        self.mode: DemoMode = "ready"
        self.events: list[dict[str, object]] = []
        self._body: bytes | None = None
        self._keys: list[str] = []

    def reset(self, mode: DemoMode) -> None:
        self.mode = mode
        self.events.clear()
        self._body = None
        self._keys.clear()

    def _result_name(self, call: int) -> str:
        if self.mode == "ready":
            return (
                "result-pending.json", "result-refreshing-brief.json",
                "result-ready-brief.json",
            )[min(call - 1, 2)]
        return {
            "refreshing": "result-refreshing-brief.json",
            "pending": "result-pending.json",
            "unavailable": "result-unavailable.json",
        }[self.mode]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        try:
            body = json.loads(request.content)
            arguments = body["arguments"]
            source_turn_ref = arguments["source_turn_ref"]
            accepted = self.conversation.accepted[source_turn_ref]
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
        call = len(self.events) + 1
        same_body = self._body is None or self._body == request.content
        rotated_key = not self._keys or self._keys[-1] != key
        self._body = request.content
        self._keys.append(key)
        result = deepcopy(_fixture(self._result_name(call)))
        if result["state"] in {"pending", "refreshing"}:
            result["retry_after_seconds"] = 1
        self.events.append({
            "call": call, "core_state": result["state"],
            "method": request.method, "path": request.url.path,
            "tool": body["tool_name"], "version": body["tool_version"],
            "detail": arguments["detail"], "exact_body": body == expected,
            "same_body": same_body, "new_poll_key": rotated_key,
            "authority_headers": True,
        })
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


@app.get("/demo/session-prep", response_class=HTMLResponse)
async def session_prep_demo() -> str:
    return DEMO_HTML.read_text(encoding="utf-8")


@app.post("/demo/session-prep/state/{mode}")
async def session_prep_demo_state(mode: DemoMode) -> dict[str, str]:
    core.reset(mode)
    return {"mode": mode}


@app.get("/demo/session-prep/trace")
async def session_prep_demo_trace(response: Response) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return {"mode": core.mode, "calls": core.events,
            "canonical_writes": conversation.final[-1:]}


def self_test() -> None:
    from fastapi.testclient import TestClient

    expected: dict[DemoMode, list[str]] = {
        "ready": ["pending", "refreshing", "ready"],
        "refreshing": ["refreshing"] * 3,
        "pending": ["pending"] * 3,
        "unavailable": ["unavailable"],
    }
    observed: dict[str, list[dict[str, object]]] = {}
    with TestClient(app) as client:
        for index, (mode, core_states) in enumerate(expected.items()):
            core.reset(mode)
            client_turn_id = str(uuid.uuid4())
            headers = {
                "Authorization": f"Bearer {MOUNT_TOKEN}",
                "X-Cosmaneura-Embed-Version": "1.0",
                "X-Cosmaneura-Client-Turn-ID": client_turn_id,
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
            assert [event["core_state"] for event in core.events] == core_states
            assert all(event["exact_body"] and event["authority_headers"]
                       and event["new_poll_key"] for event in core.events)
            observed[mode] = deepcopy(core.events)
        rejected_turn_id = str(uuid.uuid4())
        rejected = client.post("/session-prep", headers={
            "Authorization": f"Bearer {MOUNT_TOKEN}",
            "X-Cosmaneura-Embed-Version": "1.0",
            "X-Cosmaneura-Client-Turn-ID": rejected_turn_id,
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
