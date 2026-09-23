"""Record handles are private, correlated service response metadata."""
from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from app.security.mount import MountResolutionError, MountResolveRequest, PlatformResolver

TURN = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TRACE = "trace_provider_prep_000001"
HANDLE = "context_prep_" + "a" * 32


def _resolve(*, handles: list[str], capability: bool = True, surface: str = "provider_assistant", trace: str = TRACE, returned_turn: uuid.UUID = TURN):
    async def token():
        return "synthetic-workload-token"

    async def scenario():
        def handler(request):
            assert request.headers["Authorization"] == "Bearer synthetic-workload-token"
            headers = [
                ("X-Cosmaneura-Interaction-Ref", "interaction_provider_prep_0001"),
                ("X-Cosmaneura-Client-Turn-ID", str(returned_turn)),
                ("X-Cosmaneura-Interaction-Reused", "false"),
                ("X-Trace-ID", trace),
                *[("X-Cosmaneura-Session-Prep-Context", value) for value in handles],
            ]
            provider = surface == "provider_assistant"
            return httpx.Response(200, headers=headers, json={
                "surface": surface,
                "auth_tier": "provider" if provider else "anonymous",
                "capabilities": ["session-prep:read"] if capability else [],
                "tenant_id": "tenant_synthetic_0001" if provider else None,
                "actor_id": "actor_synthetic_0001" if provider else None,
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await PlatformResolver(
                platform_base_url="https://platform.example",
                workload_token_provider=token,
                client=client,
            ).resolve(MountResolveRequest(
                client_turn_id=TURN,
                trace_id=TRACE,
                mount_context_token="mount_context_synthetic_000001",
            ))

    return asyncio.run(scenario())


def test_authenticated_context_header_is_private_and_does_not_expand_public_mount():
    result = _resolve(handles=[HANDLE])
    assert result.session_prep_context_ref == HANDLE
    assert HANDLE not in repr(result)
    assert set(result.context.model_dump()) == {
        "surface", "auth_tier", "capabilities", "tenant_id", "actor_id",
    }


@pytest.mark.parametrize("changes", [
    {"handles": [HANDLE, HANDLE]},
    {"handles": ["context_synthetic_0001"]},
    {"handles": ["context_prep_short"]},
    {"handles": [HANDLE + " "]},
    {"handles": [HANDLE], "trace": "invalid trace"},
    {"handles": [HANDLE], "returned_turn": uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")},
    {"handles": [HANDLE], "capability": False},
    {"handles": [HANDLE], "surface": "public"},
])
def test_ambiguous_uncorrelated_or_unauthorized_record_handles_are_rejected(changes):
    with pytest.raises(MountResolutionError) as caught:
        _resolve(**changes)
    assert caught.value.code == "MOUNT_RESOLUTION_INVALID"
    assert HANDLE not in str(caught.value)


def test_missing_context_header_preserves_legacy_and_synthetic_resolution():
    assert _resolve(handles=[]).session_prep_context_ref is None


@pytest.mark.parametrize("handles", [[], [HANDLE]])
def test_platform_can_assign_a_valid_trace_with_or_without_a_record_handle(handles):
    result = _resolve(handles=handles, trace="platform_trace_provider_prep_0001")
    assert result.trace_id == "platform_trace_provider_prep_0001"
    assert result.client_turn_id == TURN
    assert result.session_prep_context_ref == (HANDLE if handles else None)
