"""Run one governed lab review through the deployed provider mount.

Required environment variables:

* ``SOVEREIGN_PROVIDER_URL`` — exact HTTPS origin of the provider instance.
* ``SOVEREIGN_PLATFORM_URL`` — exact HTTPS origin of the platform token issuer.
* ``SOVEREIGN_PROVIDER_TOKEN`` — provider Clerk JWT used only for exchange.
* ``SOVEREIGN_SYNTHETIC_PLATFORM_RECORD_REF`` — opaque platform record bound
  to the synthetic Preview mapping.
* ``SOVEREIGN_SYNTHETIC_RECORD_GRANT`` — short-lived host-issued Fernet grant.
* ``SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED=true`` — explicit operator assertion
  that the mapped Preview record contains only vendor-owned synthetic data.

The script never searches by name or prints either token, a record reference, answer,
lab values, or an upstream response body.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import uuid
from collections.abc import Callable

import httpx

from app.clients.labs import SANDBOX_LABEL
from tools.deployed_smoke import (
    SmokeFailure,
    assert_health,
    assert_welcome,
    exchange_mount_token,
    mount_headers,
    request_json,
    required_base_url,
    required_opaque_ref,
    required_token,
)


_FERNET_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{100,4096}={0,2}$")


def required_synthetic_record_grant() -> str:
    if os.environ.get("SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED") != "true":
        raise SmokeFailure(
            "SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED must be exactly true after Preview verification"
        )
    grant = os.environ.get("SOVEREIGN_SYNTHETIC_RECORD_GRANT", "").strip()
    if not _FERNET_TOKEN_RE.fullmatch(grant):
        raise SmokeFailure(
            "SOVEREIGN_SYNTHETIC_RECORD_GRANT must be a fresh host-issued Fernet token"
        )
    return grant


def _assert_lab_response(payload: dict) -> None:
    if payload.get("persona") != "provider" or payload.get("intent") != "lab_review":
        raise SmokeFailure("provider request did not route to the lab-review lane")
    if payload.get("safety_notice") is not None:
        raise SmokeFailure("lab-review response carried an unexpected safety notice")
    if not isinstance(payload.get("trace_id"), str) or not payload["trace_id"]:
        raise SmokeFailure("lab-review response has no trace identifier")
    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.startswith(SANDBOX_LABEL):
        raise SmokeFailure("lab-review response is missing the persistent sandbox label")
    review = payload.get("doctrinal_review")
    if not isinstance(review, dict) or not isinstance(review.get("conforms"), bool):
        raise SmokeFailure("lab-review response did not complete doctrinal arbitration")

    structured = payload.get("structured")
    if not isinstance(structured, dict):
        raise SmokeFailure("lab-review response has no structured result")
    expected = {
        "feature": "lab_review",
        "tier": "sandbox",
        "sandbox_label": SANDBOX_LABEL,
    }
    for name, value in expected.items():
        if structured.get(name) != value:
            raise SmokeFailure(f"lab-review result has an unexpected {name}")
    count = structured.get("result_count")
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 500:
        raise SmokeFailure("lab-review result count is empty or out of bounds")
    access = structured.get("access")
    if not isinstance(access, dict):
        raise SmokeFailure("lab-review result has no access proof")
    if set(access) != {"record_ref_sha256_16", "resolved_by", "sandbox"}:
        raise SmokeFailure("lab-review access proof has an unexpected shape")
    record_hash = access.get("record_ref_sha256_16")
    if (
        not isinstance(record_hash, str)
        or not re.fullmatch(r"[0-9a-f]{16}", record_hash)
        or access.get("resolved_by") != "platform_signed_open_record"
        or access.get("sandbox") is not True
    ):
        raise SmokeFailure("lab-review access proof is not bound to the host-open sandbox record")
    citations = payload.get("citations")
    if not isinstance(citations, list) or not any(
        isinstance(item, dict) and item.get("source") == "athenahealth Preview (synthetic EMR)"
        for item in citations
    ):
        raise SmokeFailure("lab-review response has no synthetic Preview citation")


async def run(
    client: httpx.AsyncClient,
    provider_url: str,
    platform_url: str,
    provider_token: str,
    platform_record_ref: str,
    record_grant: str,
    *,
    emit: Callable[[str], None] = print,
) -> None:
    mount_token = await exchange_mount_token(
        client,
        platform_url,
        provider_token,
        record_ref=platform_record_ref,
    )
    turn_id = uuid.uuid4()
    headers = mount_headers(mount_token, turn_id)
    health = await request_json(client, "GET", provider_url, "/healthz")
    assert_health(health, "provider_assistant", "disabled")
    welcome = await request_json(
        client,
        "GET",
        provider_url,
        "/welcome",
        headers={
            "Authorization": f"Bearer {mount_token}",
            "X-Cosmaneura-Embed-Version": "1.0",
        },
    )
    assert_welcome(welcome, "provider_assistant")
    emit("[PASS] deployed provider uses platform mount authority and Preview sandbox labs")

    response = await request_json(
        client,
        "POST",
        provider_url,
        "/chat",
        headers=headers,
        body={
            "client_turn_id": str(turn_id),
            "persona": "provider",
            "message": "Anything concerning in the open chart's lab results?",
            "metadata": {"record_ref": record_grant},
        },
    )
    _assert_lab_response(response)
    emit("[PASS] one bounded deployed labs.read completed with access proof and doctrinal review")


async def main() -> int:
    try:
        provider_url = required_base_url("SOVEREIGN_PROVIDER_URL")
        platform_url = required_base_url("SOVEREIGN_PLATFORM_URL")
        provider_token = required_token()
        platform_record_ref = required_opaque_ref(
            "SOVEREIGN_SYNTHETIC_PLATFORM_RECORD_REF"
        )
        record_grant = required_synthetic_record_grant()
        timeout = httpx.Timeout(120.0, connect=15.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": "cosmaneura-sovereign-labs-smoke/1"},
        ) as client:
            await run(
                client,
                provider_url,
                platform_url,
                provider_token,
                platform_record_ref,
                record_grant,
            )
    except SmokeFailure as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    print("Deployed synthetic Preview lab smoke passed without printing clinical data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
