"""Durable observational audit delivery to the platform Agent API.

The platform is the audit system of record.  This client sends only the closed,
ratified observational schema: opaque references and full content digests, never
prompt/response text, identity claims, record identifiers, or authority facts.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, Literal, Optional
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.compliance.netsec import assert_safe_outbound_url
from app.clients.platform_conversation import (
    DeliveryCategory,
    classify_platform_failure,
    derive_operation_token,
    derive_operation_uuid,
    safe_retry_directive,
)


_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._~-]{16,128}$")
_INTERACTION_REF = re.compile(r"^[A-Za-z0-9._~-]{16,256}$")
_EVENT_NAME = re.compile(r"^[a-z][a-z0-9._-]{1,127}$")

WorkloadTokenProvider = Callable[[], Awaitable[str]]
ObservationStatus = Literal["completed", "refused", "failed", "cancelled"]


class AuditDeliveryError(RuntimeError):
    """A value-free durable-delivery failure safe for operational handling."""

    def __init__(self, category: DeliveryCategory, *, retryable: bool):
        super().__init__(f"platform audit {category}")
        self.category = category
        self.retryable = retryable


class AuditWriteReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_ref: str = Field(pattern=r"^[A-Za-z0-9._~-]{8,160}$")
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def _aware_recorded_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("audit receipt timestamp must be timezone-aware")
        return value


class PlatformReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        "person",
        "conversation_turn",
        "user_request",
        "source_evidence",
        "session_snapshot",
        "journey_digest",
        "consent_offer",
        "consent_event",
        "trigger",
        "tool_call",
        "access_intent",
        "access_outcome",
        "approval",
        "model_usage",
        "other",
    ]
    ref: str = Field(pattern=r"^[A-Za-z0-9._~-]{8,160}$")


class AuditWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_event_id: uuid.UUID
    observed_at: datetime
    event_type: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    observation_status: ObservationStatus
    route: Optional[str] = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9._-]{1,127}$",
    )
    intent: Optional[str] = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9._-]{1,127}$",
    )
    content_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    platform_refs: tuple[PlatformReference, ...] = Field(default=(), max_length=50)

    @field_validator("platform_refs")
    @classmethod
    def _unique_platform_refs(
        cls,
        values: tuple[PlatformReference, ...],
    ) -> tuple[PlatformReference, ...]:
        if len(values) != len(set(values)):
            raise ValueError("platform references must be unique")
        return values


@dataclass(frozen=True)
class AuditTurn:
    """Request-scoped audit identity reused only for exact delivery retries."""

    interaction_ref: str
    client_turn_id: uuid.UUID
    trace_id: str
    observed_at: datetime
    conversation_turn_ref: str
    mount_context_token: Optional[str] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not _INTERACTION_REF.fullmatch(self.interaction_ref):
            raise ValueError("invalid interaction reference")
        if not _SAFE_TOKEN.fullmatch(self.trace_id):
            raise ValueError("invalid trace identifier")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if not re.fullmatch(r"^[A-Za-z0-9._~-]{8,160}$", self.conversation_turn_ref):
            raise ValueError("invalid conversation turn reference")
        derive_operation_token(self.client_turn_id, "audit.turn.completed")
        token = self.mount_context_token
        if token is not None and (
            not 16 <= len(token) <= 8192 or "\r" in token or "\n" in token
        ):
            raise ValueError("invalid mount context")

    def idempotency_key(self, event_type: str) -> str:
        return derive_operation_token(self.client_turn_id, f"audit.{event_type}")

    def observation(
        self,
        *,
        event_type: str,
        status: ObservationStatus,
        route: Optional[str],
        intent: Optional[str],
        content: Optional[str],
        platform_refs: tuple[PlatformReference, ...] = (),
    ) -> AuditWriteRequest:
        if not _EVENT_NAME.fullmatch(event_type):
            raise ValueError("invalid event type")
        digest = (
            hashlib.sha256(content.encode("utf-8")).hexdigest()
            if content is not None
            else None
        )
        return AuditWriteRequest(
            client_event_id=derive_operation_uuid(
                self.client_turn_id,
                f"audit.{event_type}",
            ),
            observed_at=self.observed_at,
            event_type=event_type,
            observation_status=status,
            route=route,
            intent=intent,
            content_sha256=digest,
            platform_refs=platform_refs,
        )


class PlatformAuditClient:
    """Small, idempotent Agent API writer with bounded same-key retries."""

    def __init__(
        self,
        *,
        platform_base_url: str,
        workload_token_provider: WorkloadTokenProvider,
        timeout_seconds: float = 10.0,
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
            raise ValueError("platform audit timeout is out of bounds")
        endpoint = (
            f"{platform_base_url.rstrip('/')}/service/v1/agent/audit-events/write"
        )
        assert_safe_outbound_url(endpoint, allow_private=True)
        self._endpoint = endpoint
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
                self._client.post(
                    self._endpoint,
                    headers=headers,
                    json=payload,
                ),
                timeout=timeout_seconds,
            )
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            return await asyncio.wait_for(
                client.post(
                    self._endpoint,
                    headers=headers,
                    json=payload,
                ),
                timeout=timeout_seconds,
            )

    async def write(
        self,
        *,
        turn: AuditTurn,
        observation: AuditWriteRequest,
    ) -> AuditWriteReceipt:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_seconds
        try:
            token = await asyncio.wait_for(
                self._workload_token_provider(),
                timeout=max(0.001, deadline - loop.time()),
            )
        except asyncio.TimeoutError:
            raise AuditDeliveryError("unavailable", retryable=True) from None
        except Exception:
            raise AuditDeliveryError("auth", retryable=False) from None
        if not token or "\n" in token or "\r" in token:
            raise AuditDeliveryError("auth", retryable=False)

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Idempotency-Key": turn.idempotency_key(observation.event_type),
            "X-Cosmaneura-Interaction-Ref": turn.interaction_ref,
            "X-Trace-ID": turn.trace_id,
        }
        if turn.mount_context_token is not None:
            headers["X-Cosmaneura-Mount-Context"] = turn.mount_context_token
        payload = observation.model_dump(mode="json")

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
            raise AuditDeliveryError(
                "unavailable", retryable=transport_failure or loop.time() >= deadline
            )
        if response.status_code != 201:
            retryable, _ = safe_retry_directive(response)
            raise AuditDeliveryError(
                classify_platform_failure(response), retryable=retryable
            )
        try:
            receipt = AuditWriteReceipt.model_validate(response.json())
            replayed = response.headers["Idempotency-Replayed"].lower()
            returned_trace = response.headers["X-Trace-ID"]
            if replayed not in {"true", "false"} or returned_trace != turn.trace_id:
                raise ValueError
        except (KeyError, TypeError, ValueError, ValidationError):
            raise AuditDeliveryError("schema", retryable=False) from None
        return receipt


class ManagedIdentityTokenProvider:
    """Passwordless token source for one explicitly selected user-assigned UAI."""

    def __init__(self, *, client_id: str, scope: str):
        if not client_id or not scope.endswith("/.default"):
            raise ValueError("managed identity token configuration is incomplete")
        from azure.identity import ManagedIdentityCredential

        self._credential = ManagedIdentityCredential(client_id=client_id)
        self._scope = scope

    async def __call__(self) -> str:
        token = await asyncio.to_thread(self._credential.get_token, self._scope)
        return token.token
