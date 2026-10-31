"""One bounded, turn-bound read; pending receipts are never polled into ready."""

from __future__ import annotations

import asyncio
import json

import httpx

from app.clients.platform_conversation import PlatformTurn, derive_operation_token
from app.clients.provider_question_contract import (
    ProviderQuestionRequest, ProviderQuestionResponse, ReviewedProviderQuestionResult,
)
from app.clients.session_prep import (
    SessionPrepClient, SessionPrepDeliveryError, _closed_json_object, _failure_category,
)


class ProviderQuestionClient(SessionPrepClient):
    """Reuse the fixed HTTPS endpoint and bounded, no-redirect response reader."""

    async def read(
        self, *, turn: PlatformTurn, request: ProviderQuestionRequest,
    ) -> ProviderQuestionResponse:
        deadline = self._clock() + self._total_timeout_seconds
        versions = {"1.2.0": ("1.2.0", "1.1.0", "1.0.0"),
                    "1.1.0": ("1.1.0", "1.0.0"), "1.0.0": ("1.0.0",)}
        for version in versions[request.tool_version]:
            current = ProviderQuestionRequest(
                tool_version=version,
                arguments=request.arguments.model_copy(update={
                    "detail": "prep_summary" if version == "1.0.0" else "reviewed_sources",
                }),
            )
            try:
                return await self._read_once(turn=turn, request=current, deadline=deadline)
            except _UnsupportedReviewedSourceVersion:
                # Old Core rejects an unregistered version before creating a
                # receipt. Only that explicit rejection permits the next older
                # read. Auth, conflicts, outages and invalid data never downgrade.
                continue
        raise SessionPrepDeliveryError("schema", status_code=503, retryable=False)

    async def _read_once(
        self, *, turn: PlatformTurn, request: ProviderQuestionRequest, deadline: float,
    ) -> ProviderQuestionResponse:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise SessionPrepDeliveryError("unavailable", status_code=504, retryable=True)
        try:
            token = await asyncio.wait_for(
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
        if not isinstance(token, str) or not token or "\r" in token or "\n" in token:
            raise SessionPrepDeliveryError("auth", status_code=503, retryable=False)
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise SessionPrepDeliveryError("unavailable", status_code=504, retryable=True)
        try:
            response = await self._post(
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-Cosmaneura-Mount-Context": turn.mount_context_token,
                    "X-Cosmaneura-Interaction-Ref": turn.interaction_ref,
                    "X-Cosmaneura-Client-Turn-ID": str(turn.client_turn_id),
                    "Idempotency-Key": derive_operation_token(
                        turn.client_turn_id, "provider-question.context.read"
                        + (f".{request.tool_version}" if request.tool_version != "1.0.0" else ""),
                    ),
                    "X-Trace-ID": turn.trace_id,
                },
                payload=request.model_dump(mode="json"), timeout_seconds=remaining,
                # Core's reviewed-source wire is byte-bounded separately from
                # its character budgets. Keep legacy summary reads at 128 KiB.
                response_limit=128 * 1024 if request.tool_version == "1.0.0" else 600 * 1024,
            )
        except (httpx.TransportError, asyncio.TimeoutError):
            raise SessionPrepDeliveryError(
                "unavailable", status_code=503, retryable=True,
            ) from None
        if response.status_code != 200:
            if request.tool_version != "1.0.0" and response.status_code == 404:
                try:
                    body = json.loads(response.content, object_pairs_hook=_closed_json_object)
                    if (isinstance(body, dict) and body.get("code") == "RESOURCE_NOT_FOUND"
                            and body.get("detail") == "The requested tool is not available."):
                        raise _UnsupportedReviewedSourceVersion
                except (TypeError, ValueError, RecursionError):
                    pass
            raise SessionPrepDeliveryError(
                _failure_category(response.status_code),
                status_code=response.status_code if 400 <= response.status_code <= 599 else 503,
                retryable=False,
            )
        try:
            for header in (
                "content-type", "x-trace-id", "idempotency-replayed",
                "x-cosmaneura-client-turn-id",
            ):
                if len(response.headers.get_list(header)) != 1:
                    raise ValueError
            if (
                response.headers["content-type"].split(";", 1)[0].strip().casefold()
                != "application/json"
                or response.headers["x-trace-id"] != turn.trace_id
                or response.headers["x-cosmaneura-client-turn-id"] != str(turn.client_turn_id)
                or response.headers["idempotency-replayed"].casefold() not in {"true", "false"}
            ):
                raise ValueError
            parsed = ProviderQuestionResponse.model_validate(json.loads(
                response.content, object_pairs_hook=_closed_json_object,
            ))
            if parsed.result.schema_version != request.tool_version:
                raise ValueError("provider question response version mismatch")
            support = response.headers.get_list("x-cosmaneura-reviewed-excerpts")
            if support:
                if (support != ["1.0.0"]
                        or not isinstance(parsed.result, ReviewedProviderQuestionResult)
                        or parsed.result.state != "ready"):
                    raise ValueError("invalid reviewed-excerpt support header")
                parsed.result._reviewed_excerpts_version = "1.0.0"
            return parsed
        except (KeyError, TypeError, ValueError, RecursionError):
            raise SessionPrepDeliveryError(
                "schema", status_code=503, retryable=False,
            ) from None


class _UnsupportedReviewedSourceVersion(Exception):
    """An old Core rejected the new tool before accepting a context read."""
