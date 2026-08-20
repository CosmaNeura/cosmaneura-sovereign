"""Smoke the separated public deployment and platform-authorized provider deployment.

Required environment variables:

* ``SOVEREIGN_PUBLIC_URL`` — exact HTTPS origin of the public instance.
* ``SOVEREIGN_PROVIDER_URL`` — exact HTTPS origin of the provider instance.
* ``SOVEREIGN_PLATFORM_URL`` — exact HTTPS origin of the platform token issuer.
* ``SOVEREIGN_PROVIDER_TOKEN`` — provider Clerk JWT used only for exchange.
* ``SOVEREIGN_NON_PROVIDER_TOKEN`` — non-provider Clerk JWT used only for a
  negative exchange check.

The prompts are synthetic. The script never prints a token, response body, or
answer and refuses redirects so provider credentials cannot cross origins.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Callable

import httpx

from tools.deployed_smoke import (
    SmokeFailure,
    assert_chat,
    assert_health,
    assert_welcome,
    distinct_origins,
    distinct_tokens,
    exchange_mount_token,
    expect_status,
    mount_headers,
    request_json,
    required_base_url,
    required_token,
    welcome_headers,
)


_PUBLIC_EDUCATION = {
    "persona": "patient",
    "message": "Explain in general what systolic and diastolic blood pressure numbers mean.",
    "metadata": {},
}
_PROVIDER_EDUCATION = {
    "persona": "provider",
    "message": "Explain in general what systolic and diastolic blood pressure numbers mean.",
    "metadata": {},
}
_PUBLIC_ESCALATION_ATTEMPT = {
    "persona": "provider",
    "message": "Review the lab results from the open chart and show the A1C trend.",
    "metadata": {"record_ref": "synthetic-smoke-record"},
}


def _turn_body(body: dict, turn_id: uuid.UUID) -> dict:
    return {**body, "client_turn_id": str(turn_id)}


def _turn_headers(turn_id: uuid.UUID) -> dict[str, str]:
    return {
        "X-Cosmaneura-Embed-Version": "1.0",
        "X-Cosmaneura-Client-Turn-ID": str(turn_id),
    }


async def run(
    client: httpx.AsyncClient,
    public_url: str,
    provider_url: str,
    platform_url: str,
    provider_token: str,
    non_provider_token: str,
    *,
    emit: Callable[[str], None] = print,
) -> None:
    distinct_origins(public_url, provider_url)
    distinct_tokens(provider_token, non_provider_token)
    public_health = await request_json(client, "GET", public_url, "/healthz")
    assert_health(public_health, "public", "disabled")
    provider_health = await request_json(client, "GET", provider_url, "/healthz")
    assert_health(provider_health, "provider_assistant", "disabled")
    emit("[PASS] both health endpoints report distinct deployment profiles with MOCK_MODE=false")

    public_welcome = await request_json(
        client,
        "GET",
        public_url,
        "/welcome",
        headers={"X-Cosmaneura-Embed-Version": "1.0"},
    )
    assert_welcome(public_welcome, "public")

    await expect_status(
        client,
        "GET",
        provider_url,
        "/welcome",
        401,
        headers={"X-Cosmaneura-Embed-Version": "1.0"},
    )
    emit("[PASS] provider welcome rejects a missing opaque mount token")

    await expect_status(
        client,
        "POST",
        platform_url,
        "/embed/v1/context-tokens/exchange",
        403,
        headers={
            "Authorization": f"Bearer {non_provider_token}",
            "X-Cosmaneura-Embed-Version": "1.0",
        },
        body={"requested_surface": "provider_assistant"},
    )
    emit("[PASS] platform exchange rejects a valid actor without provider authority")

    mount_token = await exchange_mount_token(
        client,
        platform_url,
        provider_token,
    )
    emit("[PASS] provider session exchanged for an opaque platform mount token")

    provider_welcome = await request_json(
        client,
        "GET",
        provider_url,
        "/welcome",
        headers=welcome_headers(mount_token),
    )
    assert_welcome(provider_welcome, "provider_assistant", expect_labs=False)
    emit("[PASS] welcome uses mount bootstrap without a client turn and hides labs")

    missing_token_turn = uuid.uuid4()
    await expect_status(
        client,
        "POST",
        provider_url,
        "/chat",
        401,
        headers=_turn_headers(missing_token_turn),
        body=_turn_body(_PROVIDER_EDUCATION, missing_token_turn),
    )
    emit("[PASS] provider chat rejects a missing mount token")

    await expect_status(
        client,
        "POST",
        provider_url,
        "/chat",
        422,
        headers={
            "Authorization": f"Bearer {mount_token}",
            "X-Cosmaneura-Embed-Version": "1.0",
        },
        body=_turn_body(_PROVIDER_EDUCATION, uuid.uuid4()),
    )
    emit("[PASS] provider chat requires a canonical client-turn UUID")

    await expect_status(
        client,
        "POST",
        provider_url,
        "/chat",
        422,
        headers={
            "Authorization": f"Bearer {mount_token}",
            "X-Cosmaneura-Embed-Version": "1.0",
            "X-Cosmaneura-Client-Turn-ID": "not-a-uuid",
        },
        body=_turn_body(_PROVIDER_EDUCATION, uuid.uuid4()),
    )
    emit("[PASS] provider chat rejects a malformed client-turn UUID value-free")

    public_denied_turn = uuid.uuid4()
    denied = await request_json(
        client,
        "POST",
        public_url,
        "/chat",
        headers=_turn_headers(public_denied_turn),
        body=_turn_body(_PUBLIC_ESCALATION_ATTEMPT, public_denied_turn),
    )
    assert_chat(
        denied,
        persona="patient",
        intent="lab_review",
        safety_notice="capability_denied",
        client_turn_id=public_denied_turn,
    )
    if denied.get("structured"):
        raise SmokeFailure("public capability denial unexpectedly returned structured data")
    emit("[PASS] public body claims cannot reach the provider labs capability")

    public_turn = uuid.uuid4()
    public_chat = await request_json(
        client,
        "POST",
        public_url,
        "/chat",
        headers=_turn_headers(public_turn),
        body=_turn_body(_PUBLIC_EDUCATION, public_turn),
    )
    assert_chat(
        public_chat,
        persona="patient",
        intent="engagement",
        client_turn_id=public_turn,
    )
    emit("[PASS] public live model pipeline answered a synthetic education request")

    provider_turn = uuid.uuid4()
    provider_chat = await request_json(
        client,
        "POST",
        provider_url,
        "/chat",
        headers=mount_headers(mount_token, provider_turn),
        body=_turn_body(_PROVIDER_EDUCATION, provider_turn),
    )
    assert_chat(
        provider_chat,
        persona="provider",
        intent="engagement",
        client_turn_id=provider_turn,
    )
    emit("[PASS] provider live pipeline accepted platform mount authority")


async def main() -> int:
    try:
        public_url = required_base_url("SOVEREIGN_PUBLIC_URL")
        provider_url = required_base_url("SOVEREIGN_PROVIDER_URL")
        platform_url = required_base_url("SOVEREIGN_PLATFORM_URL")
        provider_token = required_token()
        non_provider_token = required_token("SOVEREIGN_NON_PROVIDER_TOKEN")
        timeout = httpx.Timeout(90.0, connect=15.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": "cosmaneura-sovereign-live-smoke/1"},
        ) as client:
            await run(
                client,
                public_url,
                provider_url,
                platform_url,
                provider_token,
                non_provider_token,
            )
    except SmokeFailure as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    print("All deployed mount and live-model checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
