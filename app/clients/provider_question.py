"""One bounded, turn-bound read; pending receipts are never polled into ready."""

from __future__ import annotations

import asyncio
import json

import httpx

from app.clients.platform_conversation import PlatformTurn, derive_operation_token
from app.clients.provider_question_contract import (
    ProviderQuestionRequest, ProviderQuestionResponse,
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
        try:
            token = await asyncio.wait_for(
                self._workload_token_provider(), timeout=self._total_timeout_seconds,
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
                        turn.client_turn_id, "provider-question.context.read",
                    ),
                    "X-Trace-ID": turn.trace_id,
                },
                payload=request.model_dump(mode="json"), timeout_seconds=remaining,
            )
        except (httpx.TransportError, asyncio.TimeoutError):
            raise SessionPrepDeliveryError(
                "unavailable", status_code=503, retryable=True,
            ) from None
        if response.status_code != 200:
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
            return ProviderQuestionResponse.model_validate(json.loads(
                response.content, object_pairs_hook=_closed_json_object,
            ))
        except (KeyError, TypeError, ValueError, RecursionError):
            raise SessionPrepDeliveryError(
                "schema", status_code=503, retryable=False,
            ) from None
