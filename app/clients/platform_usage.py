"""Request-scoped model usage reporting to the platform ledger.

Only provider facts and explicit availability markers cross this boundary.
Prompts, responses, tool arguments, patient data, and vendor error bodies never
enter either the usage payload or an exception/log message.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Literal, Optional
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.clients.platform_conversation import (
    DeliveryCategory,
    PlatformTurn,
    classify_platform_failure,
    derive_operation_token,
    derive_operation_uuid,
    safe_retry_directive,
)
from app.compliance.netsec import assert_safe_outbound_url


_LOGGER = logging.getLogger(__name__)
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9._-]{1,127}$")

WorkloadTokenProvider = Callable[[], Awaitable[str]]
CallKind = Literal[
    "classification",
    "routing",
    "reasoning",
    "drafting",
    "doctrinal_arbitration",
    "tool_support",
    "embedding",
    "other",
]
CallStatus = Literal["succeeded", "failed", "cancelled"]
UsageAvailability = Literal["complete", "partial", "unavailable"]
UsageSource = Literal[
    "provider_response",
    "provider_headers",
    "platform_meter",
    "not_available",
]


class UsageDeliveryError(RuntimeError):
    """A categorized, value-free platform usage delivery failure."""

    def __init__(self, category: DeliveryCategory, *, retryable: bool):
        super().__init__(f"platform usage {category}")
        self.category = category
        self.retryable = retryable


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    cached_input_tokens: Optional[int] = Field(default=None, ge=0)
    reasoning_tokens: Optional[int] = Field(default=None, ge=0)


class ModelUsageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    client_event_id: uuid.UUID
    call_kind: CallKind
    call_sequence: int = Field(ge=1)
    workstream: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    provider: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    provider_request_id: Optional[str] = Field(default=None, max_length=200)
    model: Optional[str] = Field(default=None, min_length=1, max_length=200)
    deployment: Optional[str] = Field(default=None, max_length=200)
    purpose: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    latency_ms: Optional[int] = Field(ge=0)
    status: CallStatus
    usage_availability: UsageAvailability
    usage_source: UsageSource
    token_usage: TokenUsage

    @model_validator(mode="after")
    def _aware_timestamps(self) -> "ModelUsageRequest":
        for value in (self.started_at, self.completed_at):
            if value is not None and value.tzinfo is None:
                raise ValueError("usage timestamps must be timezone-aware")
        return self


class UsageWriteReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_ref: str = Field(pattern=r"^[A-Za-z0-9._~-]{8,160}$")
    recorded_at: datetime

    @model_validator(mode="after")
    def _aware_recorded_at(self) -> "UsageWriteReceipt":
        if self.recorded_at.tzinfo is None:
            raise ValueError("usage receipt timestamp must be timezone-aware")
        return self


@dataclass(frozen=True)
class ProviderUsageFacts:
    """Allowlisted facts extracted from one provider response."""

    provider_request_id: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cached_input_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    usage_source: UsageSource = "not_available"


@dataclass(frozen=True)
class ModelCallSpan:
    sequence: int
    call_kind: CallKind
    workstream: str
    provider: str
    model: Optional[str]
    deployment: Optional[str]
    purpose: str
    started_at: datetime
    started_monotonic: float


@dataclass
class _UsageContext:
    turn: PlatformTurn
    client: "PlatformUsageClient"
    next_sequence: int = 1
    receipts: dict[int, str] = field(default_factory=dict)
    failed_sequences: set[int] = field(default_factory=set)

    def start(
        self,
        *,
        call_kind: CallKind,
        workstream: str,
        provider: str,
        model: Optional[str],
        deployment: Optional[str],
        purpose: str,
    ) -> ModelCallSpan:
        sequence = self.next_sequence
        self.next_sequence += 1
        return ModelCallSpan(
            sequence=sequence,
            call_kind=call_kind,
            workstream=workstream,
            provider=provider,
            model=model,
            deployment=deployment,
            purpose=purpose,
            started_at=datetime.now(timezone.utc),
            started_monotonic=time.monotonic(),
        )


_USAGE_CONTEXT: ContextVar[Optional[_UsageContext]] = ContextVar(
    "sovereign_platform_usage_context",
    default=None,
)


def activate_usage_context(
    *, turn: PlatformTurn, client: "PlatformUsageClient"
) -> None:
    """Begin one request-local sequence after canonical user acceptance."""
    _USAGE_CONTEXT.set(_UsageContext(turn=turn, client=client))


def deactivate_usage_context() -> None:
    _USAGE_CONTEXT.set(None)


def begin_model_call(
    *,
    call_kind: CallKind,
    provider: str,
    model: Optional[str],
    deployment: Optional[str],
    purpose: str,
    workstream: str = "sovereign",
) -> Optional[ModelCallSpan]:
    """Allocate a monotonic request-local sequence before provider I/O."""
    context = _USAGE_CONTEXT.get()
    if context is None:
        return None
    for value in (provider, purpose, workstream):
        if not _SAFE_NAME.fullmatch(value):
            raise ValueError("invalid model usage name")
    return context.start(
        call_kind=call_kind,
        workstream=workstream,
        provider=provider,
        model=_safe_optional_fact(model),
        deployment=_safe_optional_fact(deployment, allow_empty=True),
        purpose=purpose,
    )


async def finish_model_call(
    span: Optional[ModelCallSpan],
    *,
    status: CallStatus,
    facts: Optional[ProviderUsageFacts] = None,
) -> None:
    """Report one call without allowing telemetry failure to alter its result."""
    if span is None:
        return
    context = _USAGE_CONTEXT.get()
    if context is None:
        return
    facts = facts or ProviderUsageFacts()
    try:
        completed_at = datetime.now(timezone.utc)
        latency_ms = max(
            0, int((time.monotonic() - span.started_monotonic) * 1000)
        )
        counters = (
            facts.input_tokens,
            facts.output_tokens,
            facts.cached_input_tokens,
            facts.reasoning_tokens,
        )
        if all(value is not None for value in counters):
            availability: UsageAvailability = "complete"
        elif any(value is not None for value in counters):
            availability = "partial"
        else:
            availability = "unavailable"
        request = ModelUsageRequest(
            client_event_id=derive_operation_uuid(
                context.turn.client_turn_id,
                f"usage.sequence.{span.sequence}",
            ),
            call_kind=span.call_kind,
            call_sequence=span.sequence,
            workstream=span.workstream,
            provider=span.provider,
            provider_request_id=_safe_optional_fact(
                facts.provider_request_id, allow_empty=True
            ),
            model=span.model,
            deployment=span.deployment,
            purpose=span.purpose,
            started_at=span.started_at,
            completed_at=completed_at,
            latency_ms=latency_ms,
            status=status,
            usage_availability=availability,
            usage_source=facts.usage_source,
            token_usage=TokenUsage(
                input_tokens=facts.input_tokens,
                output_tokens=facts.output_tokens,
                cached_input_tokens=facts.cached_input_tokens,
                reasoning_tokens=facts.reasoning_tokens,
            ),
        )
        receipt = await context.client.report(
            turn=context.turn,
            request=request,
        )
    except UsageDeliveryError as error:
        context.failed_sequences.add(span.sequence)
        _LOGGER.warning(
            "platform model usage remains pending",
            extra={
                "trace_id": context.turn.trace_id,
                "call_sequence": span.sequence,
                "category": error.category,
                "retryable": error.retryable,
            },
        )
    except Exception:
        context.failed_sequences.add(span.sequence)
        _LOGGER.warning(
            "platform model usage remains pending",
            extra={
                "trace_id": context.turn.trace_id,
                "call_sequence": span.sequence,
                "category": "schema",
                "retryable": False,
            },
        )
    else:
        context.receipts[span.sequence] = receipt.record_ref


def successful_usage_refs() -> tuple[str, ...]:
    """Return successful opaque receipts in invocation order."""
    context = _USAGE_CONTEXT.get()
    if context is None:
        return ()
    return tuple(
        reference
        for _, reference in sorted(context.receipts.items())
    )


def usage_reporting_complete() -> bool:
    """Whether every allocated call has a durable usage receipt."""
    context = _USAGE_CONTEXT.get()
    if context is None:
        return True
    reported = set(context.receipts) | context.failed_sequences
    allocated = set(range(1, context.next_sequence))
    return not context.failed_sequences and reported == allocated


def provider_usage_facts(
    *,
    headers: Optional[httpx.Headers],
    usage: object,
    provider: str,
) -> ProviderUsageFacts:
    """Extract only known counters and a bounded request ID."""
    usage_dict = usage if isinstance(usage, dict) else {}
    request_id: Optional[str] = None
    if headers is not None:
        for name in ("request-id", "x-request-id", "apim-request-id"):
            candidate = headers.get(name)
            if candidate:
                request_id = _safe_optional_fact(candidate, allow_empty=True)
                break
    if provider == "anthropic":
        return ProviderUsageFacts(
            provider_request_id=request_id,
            input_tokens=_counter(usage_dict.get("input_tokens")),
            output_tokens=_counter(usage_dict.get("output_tokens")),
            cached_input_tokens=_counter(
                usage_dict.get("cache_read_input_tokens")
            ),
            reasoning_tokens=None,
            usage_source="provider_response" if usage_dict else "not_available",
        )
    prompt_details = usage_dict.get("prompt_tokens_details")
    completion_details = usage_dict.get("completion_tokens_details")
    return ProviderUsageFacts(
        provider_request_id=request_id,
        input_tokens=_counter(usage_dict.get("prompt_tokens")),
        output_tokens=_counter(usage_dict.get("completion_tokens")),
        cached_input_tokens=_counter(
            prompt_details.get("cached_tokens")
            if isinstance(prompt_details, dict)
            else None
        ),
        reasoning_tokens=_counter(
            completion_details.get("reasoning_tokens")
            if isinstance(completion_details, dict)
            else None
        ),
        usage_source="provider_response" if usage_dict else "not_available",
    )


def _counter(value: object) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _safe_optional_fact(
    value: Optional[str], *, allow_empty: bool = False
) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 200 or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        return None
    if not value and not allow_empty:
        return None
    return value or None


class PlatformUsageClient:
    """Bounded idempotent writer for the ratified usage ledger endpoint."""

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
            raise ValueError("platform usage timeout is out of bounds")
        self._endpoint = (
            f"{platform_base_url.rstrip('/')}"
            "/service/v1/agent/model-usage-events/report"
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
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            return await asyncio.wait_for(
                client.post(self._endpoint, headers=headers, json=payload),
                timeout=timeout_seconds,
            )

    async def report(
        self,
        *,
        turn: PlatformTurn,
        request: ModelUsageRequest,
    ) -> UsageWriteReceipt:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_seconds
        try:
            token = await asyncio.wait_for(
                self._workload_token_provider(),
                timeout=max(0.001, deadline - loop.time()),
            )
        except asyncio.TimeoutError:
            raise UsageDeliveryError("unavailable", retryable=True) from None
        except Exception:
            raise UsageDeliveryError("auth", retryable=False) from None
        if not token or "\n" in token or "\r" in token:
            raise UsageDeliveryError("auth", retryable=False)

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Idempotency-Key": derive_operation_token(
                turn.client_turn_id,
                f"usage.sequence.{request.call_sequence}",
            ),
            "X-Cosmaneura-Mount-Context": turn.mount_context_token,
            "X-Cosmaneura-Interaction-Ref": turn.interaction_ref,
            "X-Trace-ID": turn.trace_id,
        }
        payload = request.model_dump(mode="json")
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
            raise UsageDeliveryError(
                "unavailable", retryable=transport_failure or loop.time() >= deadline
            )
        if response.status_code != 201:
            retryable, _ = safe_retry_directive(response)
            raise UsageDeliveryError(
                classify_platform_failure(response), retryable=retryable
            )
        try:
            receipt = UsageWriteReceipt.model_validate(response.json())
            replayed = response.headers["Idempotency-Replayed"].lower()
            returned_trace = response.headers["X-Trace-ID"]
            if replayed not in {"true", "false"} or returned_trace != turn.trace_id:
                raise ValueError
        except (KeyError, TypeError, ValueError, ValidationError):
            raise UsageDeliveryError("schema", retryable=False) from None
        return receipt
