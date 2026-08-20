"""Shared fail-closed HTTP checks for deployed Sovereign smoke tests."""
from __future__ import annotations

import os
import secrets
import uuid
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.clients.labs import SANDBOX_LABEL


class SmokeFailure(RuntimeError):
    """A deployed service did not satisfy an expected safety invariant."""


def required_base_url(env_name: str) -> str:
    raw = os.environ.get(env_name, "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        parsed = None
        port = None
    if (
        parsed is None
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or "*" in raw
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise SmokeFailure(f"{env_name} must be an exact credential-free HTTPS origin")
    return raw.rstrip("/")


def required_token(env_name: str = "SOVEREIGN_PROVIDER_TOKEN") -> str:
    token = os.environ.get(env_name, "").strip()
    if len(token) < 20 or token.count(".") != 2 or any(char.isspace() for char in token):
        raise SmokeFailure(f"{env_name} must contain a Clerk JWT; never pass it on the command line")
    return token


def required_opaque_ref(env_name: str) -> str:
    value = os.environ.get(env_name, "").strip()
    if (
        not 8 <= len(value) <= 160
        or not all(character.isalnum() or character in "._~-" for character in value)
    ):
        raise SmokeFailure(f"{env_name} must contain one opaque platform reference")
    return value


def distinct_origins(public_url: str, provider_url: str) -> None:
    public = urlsplit(public_url)
    provider = urlsplit(provider_url)
    if public.hostname.casefold() == provider.hostname.casefold():
        raise SmokeFailure("public and provider mounts must use different HTTPS hostnames")


def distinct_tokens(provider_token: str, non_provider_token: str) -> None:
    if secrets.compare_digest(provider_token, non_provider_token):
        raise SmokeFailure("provider-role and non-provider smoke tokens must be different")


def bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def mount_headers(token: str, client_turn_id: uuid.UUID) -> dict[str, str]:
    if not 16 <= len(token) <= 8192 or any(character.isspace() for character in token):
        raise SmokeFailure("platform exchange returned an invalid mount token")
    return {
        "Authorization": f"Bearer {token}",
        "X-Cosmaneura-Embed-Version": "1.0",
        "X-Cosmaneura-Client-Turn-ID": str(client_turn_id),
    }


def welcome_headers(token: str) -> dict[str, str]:
    """Return the browser bootstrap headers without inventing a turn ID."""
    if not 16 <= len(token) <= 8192 or any(character.isspace() for character in token):
        raise SmokeFailure("platform exchange returned an invalid mount token")
    return {
        "Authorization": f"Bearer {token}",
        "X-Cosmaneura-Embed-Version": "1.0",
    }


async def exchange_mount_token(
    client: httpx.AsyncClient,
    platform_url: str,
    session_token: str,
    *,
    record_ref: str | None = None,
) -> str:
    body: dict[str, str] = {"requested_surface": "provider_assistant"}
    if record_ref is not None:
        body["record_ref"] = record_ref
    payload = await request_json(
        client,
        "POST",
        platform_url,
        "/embed/v1/context-tokens/exchange",
        headers={
            **bearer_headers(session_token),
            "X-Cosmaneura-Embed-Version": "1.0",
        },
        body=body,
    )
    if set(payload) != {
        "context_token",
        "token_type",
        "issued_at",
        "refresh_after",
        "expires_at",
    } or payload.get("token_type") != "Bearer":
        raise SmokeFailure("platform exchange returned an unexpected response shape")
    token = payload.get("context_token")
    if (
        not isinstance(token, str)
        or not 16 <= len(token) <= 8192
        or any(character.isspace() for character in token)
    ):
        raise SmokeFailure("platform exchange returned an invalid mount token")
    return token


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    base_url: str,
    path: str,
    *,
    expected_status: int = 200,
    headers: Mapping[str, str] | None = None,
    body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        response = await client.request(
            method,
            f"{base_url}{path}",
            headers=headers,
            json=body,
        )
    except httpx.RequestError as error:
        raise SmokeFailure(
            f"{path} request failed ({error.__class__.__name__})"
        ) from None
    if response.status_code != expected_status:
        raise SmokeFailure(
            f"{path} returned HTTP {response.status_code}; expected {expected_status}"
        )
    if path in {"/welcome", "/chat", "/digest"} and (
        response.headers.get("X-Cosmaneura-Embed-Version") != "1.0"
    ):
        raise SmokeFailure(f"{path} did not echo embed version 1.0")
    try:
        payload = response.json()
    except ValueError:
        raise SmokeFailure(f"{path} did not return JSON") from None
    if not isinstance(payload, dict):
        raise SmokeFailure(f"{path} did not return a JSON object")
    return payload


async def expect_status(
    client: httpx.AsyncClient,
    method: str,
    base_url: str,
    path: str,
    status: int,
    *,
    body: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> None:
    try:
        response = await client.request(
            method,
            f"{base_url}{path}",
            headers=headers,
            json=body,
        )
    except httpx.RequestError as error:
        raise SmokeFailure(
            f"{path} request failed ({error.__class__.__name__})"
        ) from None
    if response.status_code != status:
        raise SmokeFailure(f"{path} returned HTTP {response.status_code}; expected {status}")
    if path in {"/welcome", "/chat", "/digest"} and (
        response.headers.get("X-Cosmaneura-Embed-Version") != "1.0"
    ):
        raise SmokeFailure(f"{path} did not echo embed version 1.0")


def assert_health(payload: Mapping[str, Any], mount: str, auth_mode: str) -> None:
    expected = {
        "status": "ok",
        "mock_mode": False,
        "mount_profile": mount,
        "auth_mode": auth_mode,
        "platform_agent_api_enabled": mount == "provider_assistant",
    }
    for name, value in expected.items():
        if payload.get(name) != value:
            raise SmokeFailure(f"health check has an unexpected {name}")


def assert_welcome(
    payload: Mapping[str, Any],
    mount: str,
    *,
    expect_labs: bool = True,
) -> None:
    paths = payload.get("paths")
    if not isinstance(paths, list) or not all(isinstance(item, dict) for item in paths):
        raise SmokeFailure("welcome paths are malformed")
    intents = {item.get("intent") for item in paths}
    if mount == "public":
        if payload.get("persona") != "patient":
            raise SmokeFailure("public welcome did not derive the patient persona")
        if "lab_review" in intents or payload.get("labs_tier") is not None:
            raise SmokeFailure("public welcome exposed the labs capability")
        if payload.get("labs_label") is not None:
            raise SmokeFailure("public welcome exposed a labs label")
    elif mount == "provider_assistant":
        if payload.get("persona") != "provider":
            raise SmokeFailure("provider welcome did not derive the provider persona")
        if expect_labs:
            if "lab_review" not in intents:
                raise SmokeFailure("provider welcome did not expose sandbox labs")
            if payload.get("labs_tier") != "sandbox":
                raise SmokeFailure("provider welcome is not pinned to the sandbox labs tier")
            if payload.get("labs_label") != SANDBOX_LABEL:
                raise SmokeFailure("provider welcome has the wrong sandbox label")
        elif (
            "lab_review" in intents
            or payload.get("labs_tier") is not None
            or payload.get("labs_label") is not None
        ):
            raise SmokeFailure("feature-limited provider welcome exposed sandbox labs")
    else:
        raise SmokeFailure("unknown expected mount")


def assert_chat(
    payload: Mapping[str, Any],
    *,
    persona: str,
    intent: str,
    safety_notice: str | None = None,
    client_turn_id: uuid.UUID | None = None,
) -> None:
    if payload.get("persona") != persona:
        raise SmokeFailure("chat response has the wrong mount-derived persona")
    if payload.get("intent") != intent:
        raise SmokeFailure("chat response has an unexpected routed intent")
    if payload.get("safety_notice") != safety_notice:
        raise SmokeFailure("chat response has an unexpected safety notice")
    if not isinstance(payload.get("answer"), str) or not payload["answer"].strip():
        raise SmokeFailure("chat response has no answer")
    if not isinstance(payload.get("trace_id"), str) or not payload["trace_id"]:
        raise SmokeFailure("chat response has no trace identifier")
    if client_turn_id is not None and payload.get("client_turn_id") != str(client_turn_id):
        raise SmokeFailure("chat response did not echo the client-turn identifier")
