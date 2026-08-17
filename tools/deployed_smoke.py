"""Shared fail-closed HTTP checks for deployed Sovereign smoke tests."""
from __future__ import annotations

import os
import secrets
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
    body: Mapping[str, Any],
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


def assert_health(payload: Mapping[str, Any], mount: str, auth_mode: str) -> None:
    expected = {
        "status": "ok",
        "mock_mode": False,
        "mount_profile": mount,
        "auth_mode": auth_mode,
    }
    for name, value in expected.items():
        if payload.get(name) != value:
            raise SmokeFailure(f"health check has an unexpected {name}")


def assert_welcome(payload: Mapping[str, Any], mount: str) -> None:
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
        if "lab_review" not in intents:
            raise SmokeFailure("provider welcome did not expose sandbox labs")
        if payload.get("labs_tier") != "sandbox":
            raise SmokeFailure("provider welcome is not pinned to the sandbox labs tier")
        if payload.get("labs_label") != SANDBOX_LABEL:
            raise SmokeFailure("provider welcome has the wrong sandbox label")
    else:
        raise SmokeFailure("unknown expected mount")


def assert_chat(
    payload: Mapping[str, Any],
    *,
    persona: str,
    intent: str,
    safety_notice: str | None = None,
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
