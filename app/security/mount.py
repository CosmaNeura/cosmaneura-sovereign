"""Server-resolved mount authority for Sovereign requests.

The legacy resolver pins exactly one mount in deployment configuration
(ADR 0014). The provider-first profile selects the platform resolver and
uses its ratified interaction lineage as the sole authority source.
Neither resolver accepts surface, tier, capability, tenant, or actor values
from a chat request body.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Optional, TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from app.compliance.netsec import assert_safe_outbound_url
from app.config import Persona

if TYPE_CHECKING:
    from app.config import Settings


_CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9-]*(?::[a-z][a-z0-9-]*)+$")
_OPAQUE_REF_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{8,160}$")
_INTERACTION_REF_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{16,256}$")
_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{16,128}$")
_SAFE_PLATFORM_PROBLEM_CODES = frozenset({
    "CONTEXT_REQUIRED",
    "CONTEXT_INVALID",
    "CONTEXT_EXPIRED",
    "CAPABILITY_DENIED",
    "SCOPE_DENIED",
    "CLIENT_TURN_CONFLICT",
    "RATE_LIMITED",
    "INVALID_WORKLOAD_TOKEN",
    "WORKLOAD_NOT_ALLOWED",
    "INTERACTION_REQUIRED",
    "INTERACTION_INVALID",
    "INTERACTION_EXPIRED",
    "PLATFORM_UNAVAILABLE",
    "PLATFORM_TIMEOUT",
    "INTERNAL_ERROR",
})


class MountContext(BaseModel):
    """The exact, immutable five-field mount context required by governance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    surface: Literal["public", "provider_assistant"]
    auth_tier: Literal["anonymous", "prospect", "identified", "enrolled", "provider"]
    capabilities: tuple[str, ...]
    tenant_id: Optional[str]
    actor_id: Optional[str]

    @field_validator("capabilities")
    @classmethod
    def _validate_capabilities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("capabilities must be unique")
        if any(len(value) > 128 or not _CAPABILITY_PATTERN.fullmatch(value)
               for value in values):
            raise ValueError("invalid capability")
        return values

    @field_validator("tenant_id", "actor_id")
    @classmethod
    def _validate_opaque_refs(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _OPAQUE_REF_PATTERN.fullmatch(value):
            raise ValueError("invalid opaque reference")
        return value

    @model_validator(mode="after")
    def _validate_mount_pair(self) -> "MountContext":
        if self.surface == "provider_assistant" and self.auth_tier != "provider":
            raise ValueError("provider_assistant requires provider auth tier")
        if self.surface == "public" and self.auth_tier == "provider":
            raise ValueError("public surface cannot use provider auth tier")
        if "ehr:labs:read" in self.capabilities and (
            self.surface != "provider_assistant" or self.auth_tier != "provider"
        ):
            raise ValueError("ehr:labs:read requires the provider mount")
        return self

    @property
    def persona_hint(self) -> Persona:
        """Derive a non-authorizing prompt/tone hint from the mount."""
        if self.surface == "provider_assistant" and self.auth_tier == "provider":
            return Persona.PROVIDER
        return Persona.PATIENT


@dataclass(frozen=True)
class MountResolveRequest:
    """Inputs sourced from transport/authentication, never the chat body."""

    client_turn_id: uuid.UUID
    trace_id: str
    mount_context_token: Optional[str] = field(default=None, repr=False)
    verified_actor_id: Optional[str] = field(default=None, repr=False)
    verified_actor_is_provider: bool = field(default=False, repr=False)


@dataclass(frozen=True)
class MountResolution:
    """A resolved snapshot plus the platform's non-authorizing lineage data."""

    context: MountContext
    client_turn_id: uuid.UUID
    trace_id: str
    interaction_ref: Optional[str] = None
    interaction_reused: bool = False


class MountResolutionError(RuntimeError):
    """Safe failure at the mount-resolution boundary."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        retryable: bool = False,
        safety_fallback: object | None = None,
    ):
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.retryable = retryable
        self.safety_fallback = safety_fallback


class MountResolver(ABC):
    """Resolve the sole authority context for one logical request."""

    @abstractmethod
    async def resolve(self, request: MountResolveRequest) -> MountResolution:
        raise NotImplementedError


class PinnedResolver(MountResolver):
    """Resolve one deployment-pinned mount, optionally binding a verified actor."""

    def __init__(self, context: MountContext):
        if context.actor_id is not None:
            raise ValueError("PinnedResolver actor must come from verified request identity")
        self._context = context

    @classmethod
    def from_settings(cls, settings: "Settings") -> "PinnedResolver":
        deployment = settings.deployment_mount
        return cls(MountContext(
            surface=deployment.surface,
            auth_tier=deployment.auth_tier,
            capabilities=deployment.capabilities,
            tenant_id=settings.mount_tenant_id or None,
            actor_id=None,
        ))

    def _opaque_actor_ref(self, subject: str) -> str:
        scope = self._context.tenant_id or "unbound"
        material = f"{scope}\0{subject}".encode("utf-8")
        digest = hashlib.sha256(material).hexdigest()[:32]
        return f"actor_{digest}"

    async def resolve(self, request: MountResolveRequest) -> MountResolution:
        actor_id = None
        if (request.verified_actor_id
                and request.verified_actor_id.strip()
                and self._context.auth_tier != "anonymous"):
            actor_id = self._opaque_actor_ref(request.verified_actor_id)
        if self._context.auth_tier == "provider" and actor_id is None:
            raise MountResolutionError(
                status_code=401, code="VERIFIED_ACTOR_REQUIRED", retryable=False,
            )
        if (self._context.auth_tier == "provider"
                and not request.verified_actor_is_provider):
            raise MountResolutionError(
                status_code=403, code="PROVIDER_ACTOR_REQUIRED", retryable=False,
            )
        context = self._context.model_copy(update={"actor_id": actor_id})
        return MountResolution(
            context=context,
            client_turn_id=request.client_turn_id,
            trace_id=request.trace_id,
        )


WorkloadTokenProvider = Callable[[], Awaitable[str]]


class PlatformResolver(MountResolver):
    """Call the ratified platform mount-context resolver without faking it."""

    def __init__(
        self,
        *,
        platform_base_url: str,
        workload_token_provider: WorkloadTokenProvider,
        timeout_seconds: float = 10.0,
        client: Optional[httpx.AsyncClient] = None,
    ):
        parsed_base = urlparse(platform_base_url)
        if (
            parsed_base.scheme != "https"
            or not parsed_base.hostname
            or parsed_base.username is not None
            or parsed_base.password is not None
            or parsed_base.query
            or parsed_base.fragment
            or parsed_base.path not in {"", "/"}
        ):
            raise ValueError("platform_base_url must be an HTTPS origin")
        endpoint = (
            f"{platform_base_url.rstrip('/')}"
            "/service/v1/agent/mount-context/resolve"
        )
        # The agent API is an operator-configured, workload-authenticated
        # internal service and may intentionally resolve to private address
        # space. The strict HTTPS-origin check above still rejects userinfo,
        # paths, query strings, and fragments.
        assert_safe_outbound_url(endpoint, allow_private=True)
        self._endpoint = endpoint
        self._workload_token_provider = workload_token_provider
        self._timeout_seconds = timeout_seconds
        self._client = client

    async def _post(self, headers: dict[str, str]) -> httpx.Response:
        try:
            if self._client is not None:
                return await asyncio.wait_for(
                    self._client.post(self._endpoint, headers=headers),
                    timeout=self._timeout_seconds,
                )
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                return await asyncio.wait_for(
                    client.post(self._endpoint, headers=headers),
                    timeout=self._timeout_seconds,
                )
        except (httpx.TransportError, asyncio.TimeoutError):
            raise MountResolutionError(
                status_code=503, code="MOUNT_RESOLUTION_UNAVAILABLE", retryable=True,
            ) from None

    async def resolve(self, request: MountResolveRequest) -> MountResolution:
        mount_token = request.mount_context_token
        if mount_token is None:
            raise MountResolutionError(
                status_code=401, code="MOUNT_CONTEXT_REQUIRED", retryable=False,
            )
        if not 16 <= len(mount_token) <= 8192:
            raise MountResolutionError(
                status_code=401, code="MOUNT_CONTEXT_INVALID", retryable=False,
            )
        if not _TRACE_ID_PATTERN.fullmatch(request.trace_id):
            raise MountResolutionError(
                status_code=503, code="MOUNT_RESOLUTION_INVALID", retryable=False,
            )
        try:
            workload_token = await asyncio.wait_for(
                self._workload_token_provider(),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise MountResolutionError(
                status_code=503, code="WORKLOAD_TOKEN_UNAVAILABLE", retryable=True,
            ) from None
        except Exception:
            raise MountResolutionError(
                status_code=503, code="WORKLOAD_TOKEN_UNAVAILABLE", retryable=False,
            ) from None
        if not workload_token or "\n" in workload_token or "\r" in workload_token:
            raise MountResolutionError(
                status_code=503, code="WORKLOAD_TOKEN_UNAVAILABLE", retryable=False,
            )
        response = await self._post({
            "Authorization": f"Bearer {workload_token}",
            "X-Cosmaneura-Mount-Context": mount_token,
            "X-Cosmaneura-Client-Turn-ID": str(request.client_turn_id),
            "X-Trace-ID": request.trace_id,
        })
        if response.status_code != 200:
            status = response.status_code if 400 <= response.status_code <= 599 else 503
            safe_code = None
            upstream_retryable = False
            try:
                problem = response.json()
                if isinstance(problem, dict):
                    candidate = problem.get("code")
                    if candidate in _SAFE_PLATFORM_PROBLEM_CODES:
                        safe_code = candidate
                    upstream_retryable = problem.get("retryable") is True
            except (TypeError, ValueError):
                pass
            retryable = (
                status in {429, 503, 504}
                and upstream_retryable
            )
            raise MountResolutionError(
                status_code=status,
                code=(
                    safe_code
                    or (
                        "MOUNT_RESOLUTION_REJECTED" if status < 500
                        else "MOUNT_RESOLUTION_UNAVAILABLE"
                    )
                ),
                retryable=retryable,
            )
        try:
            context = MountContext.model_validate(response.json())
            if context.auth_tier == "provider" and context.actor_id is None:
                raise ValueError
            interaction_ref = response.headers["X-Cosmaneura-Interaction-Ref"]
            echoed_turn_text = response.headers["X-Cosmaneura-Client-Turn-ID"]
            echoed_turn = uuid.UUID(echoed_turn_text)
            returned_trace = response.headers["X-Trace-ID"]
            reused_text = response.headers[
                "X-Cosmaneura-Interaction-Reused"
            ].lower()
            if not _INTERACTION_REF_PATTERN.fullmatch(interaction_ref):
                raise ValueError
            if (
                echoed_turn != request.client_turn_id
                or echoed_turn_text != str(request.client_turn_id)
                or reused_text not in {"true", "false"}
            ):
                raise ValueError
            if not _TRACE_ID_PATTERN.fullmatch(returned_trace):
                raise ValueError
        except (KeyError, TypeError, ValueError, ValidationError):
            raise MountResolutionError(
                status_code=503, code="MOUNT_RESOLUTION_INVALID", retryable=False,
            ) from None
        return MountResolution(
            context=context,
            client_turn_id=request.client_turn_id,
            trace_id=returned_trace,
            interaction_ref=interaction_ref,
            interaction_reused=reused_text == "true",
        )
