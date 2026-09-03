"""Run a fail-closed Session Prep canary against a synthetic provider record.

Required environment variables:

* ``SOVEREIGN_PROVIDER_URL`` — exact HTTPS origin of the provider instance.
* ``SOVEREIGN_PLATFORM_URL`` — exact HTTPS origin of the platform token issuer.
* ``SOVEREIGN_PROVIDER_TOKEN`` — provider Clerk JWT used only for exchange.
* ``SOVEREIGN_SYNTHETIC_PLATFORM_RECORD_REF`` — opaque synthetic record reference.
* ``SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED=true`` — explicit operator assertion.

Select exactly one expected state with ``--expect disabled`` or
``--expect synthetic-ready``. The canary never prints credentials, record
references, response bodies, prepared-brief text, or upstream error bodies.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from app.schemas import SessionPrepResponse, SovereignProblemDetails
from tools.deployed_smoke import (
    SmokeFailure,
    assert_health,
    mount_headers,
    required_base_url,
    required_opaque_ref,
    required_token,
    welcome_headers,
)


ExpectedState = Literal["disabled", "synthetic-ready"]
MAX_CANARY_RESPONSE_BYTES = 64 * 1024
_TRACE_ID_RE = re.compile(r"^[A-Za-z0-9._~-]{8,160}$")
_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_OFFSET_SUFFIX_RE = re.compile(r"(?:Z|[+-]\d{2}:\d{2})$")
_SESSION_PREP_PATH = {
    "label": "Open Session Prep",
    "message": "Open Session Prep",
    "intent": "session_prep",
}
_SERVER_ONLY_RESPONSE_KEYS = {
    "access_intent_ref",
    "access_outcome_ref",
    "approval_ref",
    "brief_ref",
    "brief_supersedes_ref",
    "context_snapshot_ref",
    "interaction_ref",
    "provenance",
    "record_ref",
    "snapshot_ref",
    "snapshot_supersedes_ref",
    "source_ref",
    "source_refs",
    "source_turn_ref",
    "subject_ref",
    "supporting_context",
    "tool_call_ref",
}
_SESSION_PREP_RESPONSE_KEYS = {
    "client_turn_id",
    "state",
    "freshness",
    "current_through",
    "source_count",
    "prepared_brief",
    "retry_after_seconds",
    "reason_code",
}
_PREPARED_BRIEF_KEYS = {
    "recap",
    "trajectory_and_material_changes",
    "talking_points",
    "open_threads",
    "cautions",
    "suggested_outline",
    "patient_reported",
}
_PATIENT_REPORTED_KEYS = {"label", "summary"}


def required_synthetic_confirmation() -> bool:
    """Require the operator's exact synthetic-only assertion."""
    if os.environ.get("SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED") != "true":
        raise SmokeFailure("SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED must be exactly true")
    return True


def _require_distinct_origins(provider_url: str, platform_url: str) -> None:
    provider = urlsplit(provider_url)
    platform = urlsplit(platform_url)
    if provider.hostname.casefold() == platform.hostname.casefold():
        raise SmokeFailure("provider and platform must use different HTTPS hostnames")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate JSON key")
        result[name] = value
    return result


def _reject_json_constant(_value: str) -> None:
    """Reject NaN and infinities, which browser JSON.parse never accepts."""
    raise ValueError("non-standard JSON constant")


def _assert_protected_headers(
    response: httpx.Response,
    *,
    require_trace: bool,
) -> str | None:
    if response.headers.get("X-Cosmaneura-Embed-Version") != "1.0":
        raise SmokeFailure("protected endpoint did not echo embed version 1.0")
    cache_directives = {
        item.strip().casefold().split("=", 1)[0]
        for item in response.headers.get("Cache-Control", "").split(",")
        if item.strip()
    }
    if "no-store" not in cache_directives:
        raise SmokeFailure("protected endpoint did not prohibit response caching")
    trace_id = response.headers.get("X-Trace-ID")
    if require_trace and (trace_id is None or _TRACE_ID_RE.fullmatch(trace_id) is None):
        raise SmokeFailure("Session Prep response has no valid trace identifier")
    return trace_id


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    base_url: str,
    path: str,
    *,
    expected_status: int,
    expected_media_type: str,
    headers: Mapping[str, str] | None = None,
    body: Mapping[str, Any] | None = None,
    protected: bool = False,
    require_trace: bool = False,
) -> tuple[dict[str, Any], str | None]:
    """Read one bounded JSON response without reflecting its content."""
    try:
        async with client.stream(
            method,
            f"{base_url}{path}",
            headers=headers,
            json=body,
            follow_redirects=False,
        ) as response:
            if response.status_code != expected_status:
                raise SmokeFailure(
                    f"{path} returned HTTP {response.status_code}; "
                    f"expected {expected_status}"
                )
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0]
            if media_type.casefold() != expected_media_type:
                raise SmokeFailure(f"{path} returned an unexpected media type")
            trace_id = (
                _assert_protected_headers(response, require_trace=require_trace)
                if protected
                else None
            )
            lengths = response.headers.get_list("Content-Length")
            if len(lengths) > 1 or (
                lengths
                and (
                    not lengths[0].isdigit()
                    or int(lengths[0]) > MAX_CANARY_RESPONSE_BYTES
                )
            ):
                raise SmokeFailure(f"{path} response length is invalid or too large")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(chunk) > MAX_CANARY_RESPONSE_BYTES - len(content):
                    raise SmokeFailure(f"{path} response exceeds the size limit")
                content.extend(chunk)
    except httpx.RequestError as error:
        raise SmokeFailure(
            f"{path} request failed ({error.__class__.__name__})"
        ) from None

    try:
        payload = json.loads(
            bytes(content),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, ValueError):
        raise SmokeFailure(f"{path} did not return unambiguous JSON") from None
    if not isinstance(payload, dict):
        raise SmokeFailure(f"{path} did not return a JSON object")
    return payload, trace_id


async def _exchange_mount_token(
    client: httpx.AsyncClient,
    platform_url: str,
    provider_token: str,
    record_ref: str,
) -> str:
    payload, _ = await _request_json(
        client,
        "POST",
        platform_url,
        "/embed/v1/context-tokens/exchange",
        expected_status=201,
        expected_media_type="application/json",
        headers={
            "Authorization": f"Bearer {provider_token}",
            "X-Cosmaneura-Embed-Version": "1.0",
        },
        body={
            "requested_surface": "provider_assistant",
            "record_ref": record_ref,
        },
        protected=True,
    )
    if (
        set(payload)
        != {
            "context_token",
            "token_type",
            "issued_at",
            "refresh_after",
            "expires_at",
        }
        or payload.get("token_type") != "Bearer"
    ):
        raise SmokeFailure("platform exchange returned an unexpected response shape")
    token = payload.get("context_token")
    if (
        not isinstance(token, str)
        or not 16 <= len(token) <= 8192
        or any(character.isspace() for character in token)
    ):
        raise SmokeFailure("platform exchange returned an invalid mount token")
    return token


def _assert_welcome(payload: Mapping[str, Any], *, enabled: bool) -> None:
    if payload.get("persona") != "provider":
        raise SmokeFailure("provider welcome did not derive the provider persona")
    paths = payload.get("paths")
    if not isinstance(paths, list) or not all(isinstance(item, dict) for item in paths):
        raise SmokeFailure("provider welcome paths are malformed")
    matches = [item for item in paths if item.get("intent") == "session_prep"]
    if enabled:
        if len(matches) != 1 or matches[0] != _SESSION_PREP_PATH:
            raise SmokeFailure(
                "provider welcome did not expose exactly one governed Session Prep action"
            )
    elif matches:
        raise SmokeFailure("disabled provider welcome exposed Session Prep")


def _assert_no_server_only_keys(value: Any) -> None:
    if isinstance(value, dict):
        if _SERVER_ONLY_RESPONSE_KEYS.intersection(value):
            raise SmokeFailure("Session Prep response exposed server-only fields")
        for nested in value.values():
            _assert_no_server_only_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_server_only_keys(nested)


def _browser_integer(value: Any) -> bool:
    """Match JavaScript Number.isInteger without treating booleans as numbers."""
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and value == math.trunc(value)
    )


def _browser_length(value: str) -> int:
    """Return JavaScript's UTF-16 string length for browser-contract bounds."""
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def _bounded_browser_text(value: Any, *, minimum: int, maximum: int) -> bool:
    return isinstance(value, str) and minimum <= _browser_length(value) <= maximum


def _valid_prep_text_list(value: Any, *, minimum: int) -> bool:
    return (
        isinstance(value, list)
        and minimum <= len(value) <= 8
        and all(_bounded_browser_text(item, minimum=1, maximum=1_000) for item in value)
    )


def _valid_offset_datetime(value: Any) -> bool:
    if not isinstance(value, str) or _OFFSET_SUFFIX_RE.search(value) is None:
        return False
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _assert_browser_session_prep_wire(
    payload: dict[str, Any],
    *,
    client_turn_id: uuid.UUID,
) -> None:
    """Mirror governedSessionPrep before Pydantic can coerce wire values."""
    invalid = SmokeFailure("Session Prep returned an invalid browser response")
    if set(payload) != _SESSION_PREP_RESPONSE_KEYS:
        raise invalid

    response_turn_id = payload["client_turn_id"]
    if (
        not isinstance(response_turn_id, str)
        or _UUID_V4_RE.fullmatch(response_turn_id) is None
    ):
        raise invalid
    if response_turn_id != str(client_turn_id):
        raise SmokeFailure("Session Prep response changed client-turn identity")

    state = payload["state"]
    if state not in {"ready", "refreshing", "pending", "unavailable"}:
        raise invalid
    source_count = payload["source_count"]
    if not _browser_integer(source_count) or source_count < 0 or source_count > 6:
        raise invalid

    content = state in {"ready", "refreshing"}
    if not content:
        if (
            payload["freshness"] != "none"
            or payload["current_through"] is not None
            or source_count != 0
            or payload["prepared_brief"] is not None
        ):
            raise invalid
        retry_after = payload["retry_after_seconds"]
        if state == "pending":
            if (
                payload["reason_code"] != "generation_pending"
                or not _browser_integer(retry_after)
                or retry_after < 1
                or retry_after > 300
            ):
                raise invalid
        elif retry_after is not None or payload["reason_code"] not in {
            "no_eligible_sources",
            "context_not_available",
        }:
            raise invalid
        return

    if not _valid_offset_datetime(payload["current_through"]) or source_count < 1:
        raise invalid
    retry_after = payload["retry_after_seconds"]
    if state == "ready":
        if (
            payload["freshness"] != "current"
            or retry_after is not None
            or payload["reason_code"] is not None
        ):
            raise invalid
    elif (
        payload["freshness"] != "stale"
        or payload["reason_code"] != "refresh_in_progress"
        or not _browser_integer(retry_after)
        or retry_after < 1
        or retry_after > 300
    ):
        raise invalid

    brief = payload["prepared_brief"]
    if (
        not isinstance(brief, dict)
        or set(brief) != _PREPARED_BRIEF_KEYS
        or not _bounded_browser_text(brief["recap"], minimum=1, maximum=4_000)
        or not _valid_prep_text_list(
            brief["trajectory_and_material_changes"], minimum=0
        )
        or not _valid_prep_text_list(brief["talking_points"], minimum=1)
        or not _valid_prep_text_list(brief["open_threads"], minimum=0)
        or not _valid_prep_text_list(brief["cautions"], minimum=0)
        or not _valid_prep_text_list(brief["suggested_outline"], minimum=1)
    ):
        raise invalid
    patient_reported = brief["patient_reported"]
    if patient_reported is not None and (
        not isinstance(patient_reported, dict)
        or set(patient_reported) != _PATIENT_REPORTED_KEYS
        or patient_reported["label"] != "Patient-reported Visit Prep"
        or not _bounded_browser_text(
            patient_reported["summary"], minimum=1, maximum=2_000
        )
    ):
        raise invalid


def _assert_disabled_problem(
    payload: dict[str, Any],
    *,
    trace_id: str | None,
) -> None:
    if (
        not all(
            isinstance(payload.get(name), str)
            for name in ("type", "title", "code", "detail", "trace_id")
        )
        or type(payload.get("status")) is not int
        or type(payload.get("retryable")) is not bool
        or (
            "retry_after_seconds" in payload
            and payload["retry_after_seconds"] is not None
            and type(payload["retry_after_seconds"]) is not int
        )
    ):
        raise SmokeFailure("disabled Session Prep returned an invalid problem response")
    try:
        problem = SovereignProblemDetails.model_validate(payload)
    except ValidationError:
        raise SmokeFailure(
            "disabled Session Prep returned an invalid problem response"
        ) from None
    if (
        problem.status != 403
        or problem.code != "CAPABILITY_DENIED"
        or problem.type != "https://contracts.cosmaneura.com/problems/capability_denied"
        or problem.title != "Action unavailable"
        or problem.detail != "This action is not available in the current context."
        or problem.retryable
        or problem.retry_after_seconds is not None
        or problem.safety_fallback is not None
        or problem.trace_id != trace_id
    ):
        raise SmokeFailure("disabled Session Prep did not fail closed")


def _assert_ready_response(
    payload: dict[str, Any],
    *,
    client_turn_id: uuid.UUID,
    forbidden_values: Sequence[str],
) -> None:
    _assert_no_server_only_keys(payload)
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    if "context_synthetic_" in serialized or any(
        value and value in serialized for value in forbidden_values
    ):
        raise SmokeFailure("Session Prep response exposed a sensitive identifier")
    _assert_browser_session_prep_wire(payload, client_turn_id=client_turn_id)
    try:
        response = SessionPrepResponse.model_validate(payload)
    except ValidationError:
        raise SmokeFailure(
            "Session Prep returned an invalid browser response"
        ) from None
    if response.state != "ready" or response.freshness != "current":
        raise SmokeFailure("synthetic-ready canary did not receive a current brief")
    if response.source_count < 1 or response.prepared_brief is None:
        raise SmokeFailure("synthetic-ready canary received no prepared brief")


async def run(
    client: httpx.AsyncClient,
    provider_url: str,
    platform_url: str,
    provider_token: str,
    platform_record_ref: str,
    expectation: ExpectedState,
    *,
    synthetic_confirmed: bool,
    emit: Callable[[str], None] = print,
) -> None:
    """Exercise one explicit deployed state without retrying the browser action."""
    if synthetic_confirmed is not True:
        raise SmokeFailure("synthetic record confirmation is required")
    if expectation not in {"disabled", "synthetic-ready"}:
        raise SmokeFailure("Session Prep expectation must be explicit")
    _require_distinct_origins(provider_url, platform_url)

    health, _ = await _request_json(
        client,
        "GET",
        provider_url,
        "/healthz",
        expected_status=200,
        expected_media_type="application/json",
    )
    assert_health(health, "provider_assistant", "disabled")
    emit("[PASS] provider mount reports platform authority with MOCK_MODE=false")

    mount_token = await _exchange_mount_token(
        client,
        platform_url,
        provider_token,
        platform_record_ref,
    )
    emit("[PASS] synthetic record-bound platform exchange completed")

    welcome, _ = await _request_json(
        client,
        "GET",
        provider_url,
        "/welcome",
        expected_status=200,
        expected_media_type="application/json",
        headers=welcome_headers(mount_token),
        protected=True,
    )
    enabled = expectation == "synthetic-ready"
    _assert_welcome(welcome, enabled=enabled)

    client_turn_id = uuid.uuid4()
    headers = mount_headers(mount_token, client_turn_id)
    body = {"client_turn_id": str(client_turn_id)}
    if not enabled:
        problem, trace_id = await _request_json(
            client,
            "POST",
            provider_url,
            "/session-prep",
            expected_status=403,
            expected_media_type="application/problem+json",
            headers=headers,
            body=body,
            protected=True,
            require_trace=True,
        )
        _assert_disabled_problem(problem, trace_id=trace_id)
        emit("[PASS] disabled Session Prep is absent and denied closed")
        return

    response, _ = await _request_json(
        client,
        "POST",
        provider_url,
        "/session-prep",
        expected_status=200,
        expected_media_type="application/json",
        headers=headers,
        body=body,
        protected=True,
        require_trace=True,
    )
    _assert_ready_response(
        response,
        client_turn_id=client_turn_id,
        forbidden_values=(provider_token, mount_token, platform_record_ref),
    )
    emit("[PASS] provider welcome exposes the governed Session Prep action")
    emit("[PASS] one current synthetic prepared brief passed the closed contract")


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a synthetic-only deployed Session Prep canary."
    )
    parser.add_argument(
        "--expect",
        choices=("disabled", "synthetic-ready"),
        required=True,
        help="the exact Session Prep state approved for this run",
    )
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        synthetic_confirmed = required_synthetic_confirmation()
        provider_url = required_base_url("SOVEREIGN_PROVIDER_URL")
        platform_url = required_base_url("SOVEREIGN_PLATFORM_URL")
        provider_token = required_token()
        platform_record_ref = required_opaque_ref(
            "SOVEREIGN_SYNTHETIC_PLATFORM_RECORD_REF"
        )
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": "cosmaneura-sovereign-session-prep-smoke/1"},
        ) as client:
            await run(
                client,
                provider_url,
                platform_url,
                provider_token,
                platform_record_ref,
                args.expect,
                synthetic_confirmed=synthetic_confirmed,
            )
    except SmokeFailure as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    except Exception:
        print(
            "[FAIL] Session Prep canary stopped before safe completion.",
            file=sys.stderr,
        )
        return 1
    print("Deployed synthetic Session Prep canary passed without printing content.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
