"""Canonical conversation persistence through the ratified platform Agent API.

The browser supplies one random UUID for the logical turn.  Sovereign derives
operation-specific opaque identifiers from that random input; it never sends
the client-turn UUID itself as an idempotency key.  Each phase is durably
acknowledged before the corresponding model work or successful egress.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, Literal, Optional
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.compliance.netsec import assert_safe_outbound_url


_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._~-]{16,128}$")
_INTERACTION_REF = re.compile(r"^[A-Za-z0-9._~-]{16,256}$")
_DERIVATION_PREFIX = b"cosmaneura.agent-api.v1\0"

WorkloadTokenProvider = Callable[[], Awaitable[str]]
ConversationOutcome = Literal["completed", "refused", "unavailable"]
DeliveryCategory = Literal["auth", "conflict", "schema", "unavailable"]


class ConversationDeliveryError(RuntimeError):
    """A value-free canonical-persistence failure."""

    def __init__(self, category: DeliveryCategory, *, retryable: bool):
        super().__init__(f"canonical conversation {category}")
        self.category = category
        self.retryable = retryable


def derive_operation_token(client_turn_id: uuid.UUID, operation: str) -> str:
    """Derive a stable opaque key from a random turn, separated by operation."""
    if client_turn_id.version != 4 or client_turn_id.variant != uuid.RFC_4122:
        raise ValueError("client turn must be a random UUID")
    if not re.fullmatch(r"[a-z][a-z0-9._-]{1,127}", operation):
        raise ValueError("invalid operation domain")
    digest = hashlib.sha256(
        _DERIVATION_PREFIX
        + operation.encode("ascii")
        + b"\0"
        + client_turn_id.bytes
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def derive_operation_uuid(client_turn_id: uuid.UUID, operation: str) -> uuid.UUID:
    """Derive a stable UUID-shaped event identifier in a separate domain."""
    material = hashlib.sha256(
        _DERIVATION_PREFIX
        + b"uuid\0"
        + operation.encode("ascii")
        + b"\0"
        + client_turn_id.bytes
    ).digest()[:16]
    return uuid.UUID(bytes=material, version=4)


@dataclass(frozen=True)
class PlatformTurn:
    """Request-scoped authority data for one canonical conversation turn."""

    interaction_ref: str
    client_turn_id: uuid.UUID
    trace_id: str
    mount_context_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if not _INTERACTION_REF.fullmatch(self.interaction_ref):
            raise ValueError("invalid interaction reference")
        if not _SAFE_TOKEN.fullmatch(self.trace_id):
            raise ValueError("invalid trace identifier")
        if (
            not 16 <= len(self.mount_context_token) <= 8192
            or "\r" in self.mount_context_token
            or "\n" in self.mount_context_token
        ):
            raise ValueError("invalid mount context")
        derive_operation_token(self.client_turn_id, "conversation.user_accepted")

    def idempotency_key(self, phase: str) -> str:
        if phase not in {"user_accepted", "final_response"}:
            raise ValueError("invalid conversation phase")
        return derive_operation_token(self.client_turn_id, f"conversation.{phase}")


class ConversationTurnWriteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_turn_id: uuid.UUID
    turn_ref: str = Field(pattern=r"^[A-Za-z0-9._~-]{8,160}$")
    phase: Literal["user_accepted", "final_response"]
    state: Literal["accepted", "completed"]
    recorded_at: datetime

    @model_validator(mode="after")
    def _phase_state_pair(self) -> "ConversationTurnWriteResponse":
        expected = "accepted" if self.phase == "user_accepted" else "completed"
        if self.state != expected or self.recorded_at.tzinfo is None:
            raise ValueError("invalid conversation receipt")
        return self


@dataclass(frozen=True)
class ConversationReceipt:
    client_turn_id: uuid.UUID
    turn_ref: str
    phase: Literal["user_accepted", "final_response"]
    state: Literal["accepted", "completed"]
    recorded_at: datetime
    replayed: bool


def safe_retry_directive(response: httpx.Response) -> tuple[bool, float]:
    """Read only retry metadata; never retain or expose an upstream body."""
    if response.status_code < 400 or response.status_code in {
        400, 401, 403, 404, 405, 409, 415, 422,
    }:
        return False, 0.0
    try:
        body = response.json()
    except ValueError:
        return False, 0.0
    if not isinstance(body, dict) or body.get("retryable") is not True:
        return False, 0.0
    delay_value = response.headers.get("Retry-After")
    if delay_value is None:
        delay_value = body.get("retry_after_seconds")
    try:
        delay = float(delay_value) if delay_value is not None else 0.05
    except (TypeError, ValueError):
        delay = 0.05
    if not math.isfinite(delay):
        delay = 0.05
    return True, max(0.0, delay)


def classify_platform_failure(response: httpx.Response) -> DeliveryCategory:
    """Classify by status only; upstream bodies remain outside error paths."""
    if response.status_code in {401, 403}:
        return "auth"
    if response.status_code == 409:
        return "conflict"
    if response.status_code in {400, 404, 405, 415, 422}:
        return "schema"
    return "unavailable"


class PlatformConversationClient:
    """Write canonical user/final phases with a bounded total retry budget."""

    def __init__(
        self,
        *,
        platform_base_url: str,
        workload_token_provider: WorkloadTokenProvider,
        timeout_seconds: float = 3.0,
        client: Optional[httpx.AsyncClient] = None,
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
        if not 0.1 <= timeout_seconds <= 10.0:
            raise ValueError("platform write timeout is out of bounds")
        self._endpoint = (
            f"{platform_base_url.rstrip('/')}"
            "/service/v1/agent/conversation-turns/write"
        )
        assert_safe_outbound_url(self._endpoint, allow_private=True)
        self._workload_token_provider = workload_token_provider
        self._timeout_seconds = timeout_seconds
        self._client = client

    async def _post(
        self,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
    ) -> httpx.Response:
        if self._client is not None:
            return await asyncio.wait_for(
                self._client.post(self._endpoint, headers=headers, json=payload),
                timeout=timeout_seconds,
            )
        timeout = httpx.Timeout(timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await asyncio.wait_for(
                client.post(self._endpoint, headers=headers, json=payload),
                timeout=timeout_seconds,
            )

    async def _write(
        self,
        *,
        turn: PlatformTurn,
        phase: Literal["user_accepted", "final_response"],
        payload: dict[str, object],
        expected_turn_ref: Optional[str] = None,
    ) -> ConversationReceipt:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_seconds
        try:
            workload_token = await asyncio.wait_for(
                self._workload_token_provider(),
                timeout=max(0.001, deadline - loop.time()),
            )
        except asyncio.TimeoutError:
            raise ConversationDeliveryError(
                "unavailable", retryable=True
            ) from None
        except Exception:
            raise ConversationDeliveryError("auth", retryable=False) from None
        if not workload_token or "\r" in workload_token or "\n" in workload_token:
            raise ConversationDeliveryError("auth", retryable=False)

        headers = {
            "Authorization": f"Bearer {workload_token}",
            "Content-Type": "application/json",
            "Idempotency-Key": turn.idempotency_key(phase),
            "X-Cosmaneura-Mount-Context": turn.mount_context_token,
            "X-Cosmaneura-Interaction-Ref": turn.interaction_ref,
            "X-Cosmaneura-Client-Turn-ID": str(turn.client_turn_id),
            "X-Trace-ID": turn.trace_id,
        }
        response: Optional[httpx.Response] = None
        transport_failure = False
        for attempt in range(3):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                response = await self._post(
                    headers=headers,
                    payload=payload,
                    timeout_seconds=remaining,
                )
            except (httpx.TransportError, asyncio.TimeoutError):
                response = None
                transport_failure = True
                should_retry, delay = True, (0.05, 0.2)[min(attempt, 1)]
            else:
                transport_failure = False
                should_retry, delay = safe_retry_directive(response)
            if response is not None and response.status_code == 201:
                break
            if not should_retry or attempt == 2 or delay >= deadline - loop.time():
                break
            await asyncio.sleep(delay)

        if response is None:
            raise ConversationDeliveryError(
                "unavailable", retryable=transport_failure or loop.time() >= deadline
            )
        if response.status_code != 201:
            retryable, _ = safe_retry_directive(response)
            raise ConversationDeliveryError(
                classify_platform_failure(response), retryable=retryable
            )
        try:
            parsed = ConversationTurnWriteResponse.model_validate(response.json())
            replayed_text = response.headers["Idempotency-Replayed"].lower()
            returned_trace = response.headers["X-Trace-ID"]
            returned_turn_text = response.headers[
                "X-Cosmaneura-Client-Turn-ID"
            ]
            returned_turn = uuid.UUID(returned_turn_text)
            if (
                parsed.phase != phase
                or parsed.client_turn_id != turn.client_turn_id
                or returned_turn != turn.client_turn_id
                or returned_turn_text != str(turn.client_turn_id)
                or returned_trace != turn.trace_id
                or replayed_text not in {"true", "false"}
                or (
                    expected_turn_ref is not None
                    and parsed.turn_ref != expected_turn_ref
                )
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, ValidationError):
            raise ConversationDeliveryError("schema", retryable=False) from None
        return ConversationReceipt(
            client_turn_id=parsed.client_turn_id,
            turn_ref=parsed.turn_ref,
            phase=parsed.phase,
            state=parsed.state,
            recorded_at=parsed.recorded_at,
            replayed=replayed_text == "true",
        )

    async def write_user_accepted(
        self,
        *,
        turn: PlatformTurn,
        message: str,
    ) -> ConversationReceipt:
        return await self._write(
            turn=turn,
            phase="user_accepted",
            payload={
                "phase": "user_accepted",
                "ingress_kind": "chat_text",
                "action_code": None,
                "message": {"content_type": "text/plain", "content": message},
            },
        )

    async def write_final_response(
        self,
        *,
        turn: PlatformTurn,
        expected_turn_ref: str,
        answer: str,
        outcome: ConversationOutcome,
        presentation: dict[str, object],
    ) -> ConversationReceipt:
        return await self._write(
            turn=turn,
            phase="final_response",
            expected_turn_ref=expected_turn_ref,
            payload={
                "phase": "final_response",
                "outcome": outcome,
                "response": {"content_type": "text/plain", "content": answer},
                "presentation": presentation,
            },
        )
