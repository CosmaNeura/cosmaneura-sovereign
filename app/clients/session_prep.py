"""Bounded, fail-closed client for the Core Session Prep tool."""

from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import UTC, datetime
from typing import Awaitable, Callable, Literal, Optional
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from app.clients.platform_conversation import PlatformTurn, derive_operation_token
from app.clients.session_prep_contract import (
    SessionPrepContextReadRequest,
    SessionPrepContextReadResponse,
    SessionPrepPendingResult,
    SessionPrepRefreshingResult,
    validate_session_prep_exchange,
)
from app.compliance.netsec import assert_safe_outbound_url

WorkloadTokenProvider = Callable[[], Awaitable[str]]
Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]
WallClock = Callable[[], datetime]
SessionPrepDeliveryCategory = Literal["auth", "conflict", "schema", "unavailable"]

MAX_SESSION_PREP_CALLS = 3
MAX_SESSION_PREP_SECONDS = 10.0
MAX_SESSION_PREP_RESPONSE_BYTES = 128 * 1024

class _DuplicateJSONKey(ValueError):
    pass

def _closed_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey
        result[key] = value
    return result

class SessionPrepDeliveryError(RuntimeError):
    def __init__(
        self,
        category: SessionPrepDeliveryCategory,
        *,
        status_code: int,
        retryable: bool,
    ):
        super().__init__(f"session prep {category}")
        self.category = category
        self.status_code = status_code
        self.retryable = retryable

def _failure_category(status_code: int) -> SessionPrepDeliveryCategory:
    if status_code in {401, 403}:
        return "auth"
    if status_code == 409:
        return "conflict"
    if status_code in {400, 404, 405, 415, 422}:
        return "schema"
    return "unavailable"

def _safe_retry(response: httpx.Response) -> tuple[bool, float]:
    if response.status_code not in {429, 500, 502, 503, 504}:
        return False, 0.0
    try:
        body = json.loads(
            response.content,
            object_pairs_hook=_closed_json_object,
        )
    except (TypeError, ValueError, RecursionError):
        return False, 0.0
    if not isinstance(body, dict) or body.get("retryable") is not True:
        return False, 0.0
    value = response.headers.get("Retry-After", body.get("retry_after_seconds", 1))
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return False, 0.0
    if not math.isfinite(delay) or delay < 0:
        return False, 0.0
    return True, min(delay, MAX_SESSION_PREP_SECONDS)

class SessionPrepClient:
    _response_limit = MAX_SESSION_PREP_RESPONSE_BYTES

    def __init__(
        self,
        *,
        platform_base_url: str,
        workload_token_provider: WorkloadTokenProvider,
        total_timeout_seconds: float = MAX_SESSION_PREP_SECONDS,
        max_calls: int = MAX_SESSION_PREP_CALLS,
        client: Optional[httpx.AsyncClient] = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
        wall_clock: WallClock = lambda: datetime.now(UTC),
    ):
        parsed = urlparse(platform_base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("platform_base_url must be an HTTPS origin")
        if not 0.1 <= total_timeout_seconds <= MAX_SESSION_PREP_SECONDS:
            raise ValueError("Session Prep timeout exceeds its ten-second ceiling")
        if not 1 <= max_calls <= MAX_SESSION_PREP_CALLS:
            raise ValueError("Session Prep calls exceed the three-call ceiling")
        self._endpoint = (
            f"{platform_base_url.rstrip('/')}"
            "/service/v1/agent/tools/invoke"
        )
        assert_safe_outbound_url(self._endpoint, allow_private=True)
        self._workload_token_provider = workload_token_provider
        self._total_timeout_seconds = total_timeout_seconds
        self._max_calls = max_calls
        self._client = client
        self._sleep = sleep
        self._clock = clock
        self._wall_clock = wall_clock

    async def _post(
        self,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
        endpoint: Optional[str] = None,
        response_limit: Optional[int] = None,
    ) -> httpx.Response:
        limit = self._response_limit if response_limit is None else response_limit
        async def send(client: httpx.AsyncClient) -> httpx.Response:
            request = client.build_request(
                "POST", endpoint or self._endpoint, headers=headers, json=payload,
            )
            response = await client.send(
                request, stream=True, follow_redirects=False,
            )
            try:
                declared = response.headers.get_list("content-length")
                encodings = response.headers.get_list("content-encoding")
                if len(declared) > 1 or len(encodings) > 1 or (
                    encodings and encodings[0].strip().casefold() not in {"", "identity"}
                ):
                    raise SessionPrepDeliveryError(
                        "schema", status_code=503, retryable=False,
                    )
                if declared:
                    try:
                        if not declared[0].isdigit():
                            raise ValueError
                        declared_size = int(declared[0])
                    except ValueError:
                        declared_size = -1
                    if not 0 <= declared_size <= limit:
                        raise SessionPrepDeliveryError(
                            "schema", status_code=503, retryable=False,
                        )
                body = bytearray()
                if response.is_stream_consumed:
                    if len(response.content) > limit:
                        raise SessionPrepDeliveryError(
                            "schema", status_code=503, retryable=False,
                        )
                    body.extend(response.content)
                else:
                    async for chunk in response.aiter_raw():
                        if len(chunk) > limit - len(body):
                            raise SessionPrepDeliveryError(
                                "schema", status_code=503, retryable=False,
                            )
                        body.extend(chunk)
                if declared and len(body) != declared_size:
                    raise SessionPrepDeliveryError(
                        "schema", status_code=503, retryable=False,
                    )
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=bytes(body),
                    request=request,
                )
            finally:
                await response.aclose()
        if self._client is not None:
            return await asyncio.wait_for(send(self._client), timeout=timeout_seconds)
        timeout = httpx.Timeout(timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await asyncio.wait_for(send(client), timeout=timeout_seconds)
    async def read(
        self,
        *,
        turn: PlatformTurn,
        request: SessionPrepContextReadRequest,
    ) -> SessionPrepContextReadResponse:
        """Poll within one budget; only logical polls rotate idempotency keys."""
        deadline = self._clock() + self._total_timeout_seconds
        payload = request.model_dump(mode="json")
        try:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise asyncio.TimeoutError
            workload_token = await asyncio.wait_for(
                self._workload_token_provider(), timeout=remaining,
            )
        except asyncio.TimeoutError:
            raise SessionPrepDeliveryError(
                "unavailable", status_code=503, retryable=True,
            ) from None
        except Exception:
            raise SessionPrepDeliveryError(
                "auth", status_code=503, retryable=False,
            ) from None
        if not workload_token or "\r" in workload_token or "\n" in workload_token:
            raise SessionPrepDeliveryError(
                "auth", status_code=503, retryable=False,
            )
        calls = 0
        logical_attempt = 1
        idempotency_key = derive_operation_token(
            turn.client_turn_id,
            f"session-prep.context.read.{logical_attempt}",
        )
        while calls < self._max_calls:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise SessionPrepDeliveryError(
                    "unavailable", status_code=504, retryable=True,
                )
            headers = {
                "Authorization": f"Bearer {workload_token}",
                "Content-Type": "application/json",
                "X-Cosmaneura-Mount-Context": turn.mount_context_token,
                "X-Cosmaneura-Interaction-Ref": turn.interaction_ref,
                "X-Cosmaneura-Client-Turn-ID": str(turn.client_turn_id),
                "Idempotency-Key": idempotency_key,
                "X-Trace-ID": turn.trace_id,
            }
            calls += 1
            try:
                response = await self._post(
                    headers=headers,
                    payload=payload,
                    timeout_seconds=remaining,
                )
            except (httpx.TransportError, asyncio.TimeoutError):
                if calls >= self._max_calls:
                    raise SessionPrepDeliveryError(
                        "unavailable", status_code=503, retryable=True,
                    ) from None
                delay = min(0.05 * (4 ** (calls - 1)), MAX_SESSION_PREP_SECONDS)
                if delay >= deadline - self._clock():
                    raise SessionPrepDeliveryError(
                        "unavailable", status_code=504, retryable=True,
                    ) from None
                await self._sleep(delay)
                continue
            if response.status_code != 200:
                retryable, delay = _safe_retry(response)
                if (
                    retryable
                    and calls < self._max_calls
                    and delay < deadline - self._clock()
                ):
                    await self._sleep(delay)
                    continue
                raise SessionPrepDeliveryError(
                    _failure_category(response.status_code),
                    status_code=(
                        response.status_code
                        if 400 <= response.status_code <= 599
                        else 503
                    ),
                    retryable=retryable,
                )
            try:
                content_types = response.headers.get_list("content-type")
                trace_headers = response.headers.get_list("x-trace-id")
                replay_headers = response.headers.get_list("idempotency-replayed")
                turn_headers = response.headers.get_list(
                    "x-cosmaneura-client-turn-id"
                )
                if (
                    len(content_types) != 1
                    or len(trace_headers) != 1
                    or len(replay_headers) != 1
                    or len(turn_headers) != 1
                ):
                    raise ValueError
                media_type = content_types[0].split(";", 1)[0]
                raw = json.loads(
                    response.content,
                    object_pairs_hook=_closed_json_object,
                )
                parsed = SessionPrepContextReadResponse.model_validate(raw)
                replayed = replay_headers[0].casefold()
                returned_trace = trace_headers[0]
                returned_turn = turn_headers[0]
                if (
                    media_type.strip().casefold() != "application/json"
                    or replayed not in {"true", "false"}
                    or returned_trace != turn.trace_id
                    or returned_turn != str(turn.client_turn_id)
                ):
                    raise ValueError
                validate_session_prep_exchange(
                    request,
                    parsed.result,
                    observed_at=self._wall_clock(),
                )
            except (KeyError, TypeError, ValueError, RecursionError, ValidationError):
                raise SessionPrepDeliveryError(
                    "schema", status_code=503, retryable=False,
                ) from None
            if not isinstance(
                parsed.result,
                (SessionPrepPendingResult, SessionPrepRefreshingResult),
            ):
                return parsed
            if calls >= self._max_calls:
                return parsed
            delay = float(parsed.result.retry_after_seconds)
            if delay >= deadline - self._clock():
                return parsed
            await self._sleep(delay)
            logical_attempt += 1
            idempotency_key = derive_operation_token(
                turn.client_turn_id,
                f"session-prep.context.read.{logical_attempt}",
            )
        raise SessionPrepDeliveryError(
            "unavailable", status_code=503, retryable=True,
        )
