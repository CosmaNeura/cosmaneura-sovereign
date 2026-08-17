"""Smoke both deployed, physically separated pinned-mount instances.

Required environment variables:

* ``SOVEREIGN_PUBLIC_URL`` — exact HTTPS origin of the public instance.
* ``SOVEREIGN_PROVIDER_URL`` — exact HTTPS origin of the provider instance.
* ``SOVEREIGN_PROVIDER_TOKEN`` — real Clerk JWT from the provider template.
* ``SOVEREIGN_NON_PROVIDER_TOKEN`` — valid Clerk JWT without the provider role.

The prompts are synthetic. The script never prints a token, response body, or
answer and refuses redirects so provider credentials cannot cross origins.
"""
from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable

import httpx

from tools.deployed_smoke import (
    SmokeFailure,
    assert_chat,
    assert_health,
    assert_welcome,
    bearer_headers,
    distinct_origins,
    distinct_tokens,
    expect_status,
    request_json,
    required_base_url,
    required_token,
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


async def run(
    client: httpx.AsyncClient,
    public_url: str,
    provider_url: str,
    provider_token: str,
    non_provider_token: str,
    *,
    emit: Callable[[str], None] = print,
) -> None:
    distinct_origins(public_url, provider_url)
    distinct_tokens(provider_token, non_provider_token)
    provider_headers = bearer_headers(provider_token)

    public_health = await request_json(client, "GET", public_url, "/healthz")
    assert_health(public_health, "public", "disabled")
    provider_health = await request_json(client, "GET", provider_url, "/healthz")
    assert_health(provider_health, "provider_assistant", "oidc")
    emit("[PASS] both health endpoints report distinct pinned mounts with MOCK_MODE=false")

    public_welcome = await request_json(client, "GET", public_url, "/welcome")
    assert_welcome(public_welcome, "public")
    provider_welcome = await request_json(client, "GET", provider_url, "/welcome")
    assert_welcome(provider_welcome, "provider_assistant")
    emit("[PASS] welcome capabilities are mount-specific and provider labs are sandbox-only")

    await expect_status(
        client,
        "POST",
        provider_url,
        "/chat",
        401,
        body=_PROVIDER_EDUCATION,
    )
    emit("[PASS] provider chat rejects a missing Clerk token")

    await expect_status(
        client,
        "POST",
        provider_url,
        "/chat",
        403,
        headers=bearer_headers(non_provider_token),
        body=_PROVIDER_EDUCATION,
    )
    emit("[PASS] provider chat rejects a valid actor without the provider role")

    denied = await request_json(
        client,
        "POST",
        public_url,
        "/chat",
        body=_PUBLIC_ESCALATION_ATTEMPT,
    )
    assert_chat(
        denied,
        persona="patient",
        intent="lab_review",
        safety_notice="capability_denied",
    )
    if denied.get("structured"):
        raise SmokeFailure("public capability denial unexpectedly returned structured data")
    emit("[PASS] public body claims cannot reach the provider labs capability")

    public_chat = await request_json(
        client,
        "POST",
        public_url,
        "/chat",
        body=_PUBLIC_EDUCATION,
    )
    assert_chat(public_chat, persona="patient", intent="engagement")
    emit("[PASS] public live model pipeline answered a synthetic education request")

    provider_chat = await request_json(
        client,
        "POST",
        provider_url,
        "/chat",
        headers=provider_headers,
        body=_PROVIDER_EDUCATION,
    )
    assert_chat(provider_chat, persona="provider", intent="engagement")
    emit("[PASS] provider live model pipeline accepted the verified Clerk actor")


async def main() -> int:
    try:
        public_url = required_base_url("SOVEREIGN_PUBLIC_URL")
        provider_url = required_base_url("SOVEREIGN_PROVIDER_URL")
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
