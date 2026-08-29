"""
HTTP surface for the Sovereign.

Two logical products, one codebase. A server-resolved deployment mount selects
the compliance profile and capabilities. The legacy request persona is never
an authority input.
"""
from __future__ import annotations

import ipaddress
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.compliance import safety
from app.clients.labs import SANDBOX_LABEL
from app.config import (AuthMode, MountProfile, Persona, get_settings,
                        validate_deployment_configuration)
from app.schemas import (ChatRequest, ChatResponse,
                         CompassionateSafetyFallback, DigestRequest,
                         DigestResponse, EmbedChatRequest, EmbedChatResponse,
                         Intent, SovereignProblemDetails)
from app.security.auth import AuthError, Principal, TokenVerifier, resolve_principal
from app.security.mount import MountResolutionError
from app.security.rate_limit import TokenBucketRateLimiter
from app.sovereign.orchestrator import INTENT_CAPABILITIES, Sovereign

_settings = get_settings()
validate_deployment_configuration(_settings)

app = FastAPI(title="CosmaNeura Sovereign", version="0.1.0")
_sovereign = Sovereign(_settings)
_verifier = TokenVerifier(_settings)
_public_chat_limiter = TokenBucketRateLimiter(
    requests=_settings.public_chat_rate_limit_requests,
    window_seconds=_settings.public_chat_rate_limit_window_seconds,
    max_clients=_settings.public_chat_rate_limit_max_clients,
)

_EMBED_VERSION = "1.0"
_EMBED_VERSION_HEADER = "X-Cosmaneura-Embed-Version"
_PROTECTED_EMBED_PATHS = frozenset({"/welcome", "/chat", "/digest"})
_CORS_ALLOW_METHODS = ("GET", "POST")
_CORS_ALLOW_HEADERS = (
    "content-type",
    "authorization",
    "x-cosmaneura-client-turn-id",
    "x-cosmaneura-embed-version",
)
_CORS_EXPOSE_HEADERS = (
    "x-cosmaneura-embed-version",
    "x-trace-id",
    "retry-after",
)
ProblemCode = Literal[
    "CONTEXT_REQUIRED",
    "CONTEXT_INVALID",
    "CONTEXT_EXPIRED",
    "CAPABILITY_DENIED",
    "RECORD_CONTEXT_MISMATCH",
    "EMBED_VERSION_UNSUPPORTED",
    "VALIDATION_FAILED",
    "RATE_LIMITED",
    "SERVICE_UNAVAILABLE",
    "INTERNAL_ERROR",
]

_PROBLEM_COPY: dict[ProblemCode, tuple[str, str]] = {
    "CONTEXT_REQUIRED": (
        "Context required",
        "A current assistant context is required.",
    ),
    "CONTEXT_INVALID": (
        "Context unavailable",
        "The current assistant context cannot be used.",
    ),
    "CONTEXT_EXPIRED": (
        "Context expired",
        "A fresh assistant context is required.",
    ),
    "CAPABILITY_DENIED": (
        "Action unavailable",
        "This action is not available in the current context.",
    ),
    "RECORD_CONTEXT_MISMATCH": (
        "Record context mismatch",
        "The current record context cannot safely support this action.",
    ),
    "EMBED_VERSION_UNSUPPORTED": (
        "Embed version unsupported",
        "The supported assistant embed version is required.",
    ),
    "VALIDATION_FAILED": (
        "Validation failed",
        "The integration request could not be accepted.",
    ),
    "RATE_LIMITED": (
        "Rate limited",
        "The assistant is temporarily rate limited.",
    ),
    "SERVICE_UNAVAILABLE": (
        "Service unavailable",
        "The assistant is temporarily unavailable.",
    ),
    "INTERNAL_ERROR": (
        "Internal error",
        "The assistant could not complete the request.",
    ),
}


class SovereignHTTPError(RuntimeError):
    """A pre-normalized, value-free error for the public embed boundary."""

    def __init__(
        self,
        *,
        status_code: int,
        code: ProblemCode,
        retryable: bool = False,
        retry_after_seconds: Optional[int] = None,
        headers: Optional[dict[str, str]] = None,
    ):
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.headers = headers or {}


def _problem_response(
    *,
    status_code: int,
    code: ProblemCode,
    retryable: bool = False,
    retry_after_seconds: Optional[int] = None,
    trace_id: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
    safety_fallback: Optional[CompassionateSafetyFallback] = None,
) -> JSONResponse:
    """Render the closed embed problem schema without request/upstream values."""
    title, detail = _PROBLEM_COPY[code]
    problem = SovereignProblemDetails(
        type=f"https://contracts.cosmaneura.com/problems/{code.lower()}",
        title=title,
        status=status_code,
        code=code,
        detail=detail,
        trace_id=trace_id or uuid.uuid4().hex,
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
        safety_fallback=safety_fallback,
    )
    safe_headers = dict(headers or {})
    safe_headers["X-Trace-ID"] = problem.trace_id
    if retry_after_seconds is not None:
        safe_headers["Retry-After"] = str(retry_after_seconds)
    return JSONResponse(
        status_code=status_code,
        content=problem.model_dump(mode="json", exclude_none=True),
        headers=safe_headers,
        media_type="application/problem+json",
    )


def _problem_for_http_status(status_code: int) -> ProblemCode:
    if status_code == 401:
        return "CONTEXT_INVALID"
    if status_code == 403:
        return "CAPABILITY_DENIED"
    if status_code == 426:
        return "EMBED_VERSION_UNSUPPORTED"
    if status_code == 429:
        return "RATE_LIMITED"
    if status_code in {502, 503, 504}:
        return "SERVICE_UNAVAILABLE"
    if status_code >= 500:
        return "INTERNAL_ERROR"
    return "VALIDATION_FAILED"


def _problem_for_mount_error(error: MountResolutionError) -> tuple[int, ProblemCode]:
    """Normalize all internal/platform policy names to the ratified public set."""
    name = error.code.upper()
    if name in {
        "INVALID_WORKLOAD_TOKEN",
        "WORKLOAD_NOT_ALLOWED",
        "INTERACTION_REQUIRED",
        "INTERACTION_INVALID",
        "INTERACTION_EXPIRED",
        "PLATFORM_UNAVAILABLE",
        "PLATFORM_TIMEOUT",
        "INTERNAL_ERROR",
    }:
        return 503, "SERVICE_UNAVAILABLE"
    if "EXPIRED" in name and "CONTEXT" in name:
        return 401, "CONTEXT_EXPIRED"
    if name in {"MOUNT_CONTEXT_REQUIRED", "CONTEXT_REQUIRED"}:
        return 401, "CONTEXT_REQUIRED"
    if any(part in name for part in ("CLIENT_TURN", "CONFLICT", "SCHEMA")):
        return (409 if error.status_code == 409 else 422), "VALIDATION_FAILED"
    if any(part in name for part in ("SCOPE", "SUBJECT", "RECORD_CONTEXT")):
        return 409, "RECORD_CONTEXT_MISMATCH"
    if any(part in name for part in ("CAPABILITY", "SURFACE_MISMATCH", "PROVIDER_ACTOR")):
        return 403, "CAPABILITY_DENIED"
    if error.status_code == 401:
        return 401, "CONTEXT_INVALID"
    if error.status_code == 403:
        return 403, "CAPABILITY_DENIED"
    if error.status_code == 409:
        return 409, "VALIDATION_FAILED"
    if error.status_code == 429:
        return 429, "RATE_LIMITED"
    return 503, "SERVICE_UNAVAILABLE"


@app.exception_handler(SovereignHTTPError)
async def _sovereign_http_error(
    _request: Request,
    error: SovereignHTTPError,
) -> JSONResponse:
    return _problem_response(
        status_code=error.status_code,
        code=error.code,
        retryable=error.retryable,
        retry_after_seconds=error.retry_after_seconds,
        headers=error.headers,
    )


@app.exception_handler(MountResolutionError)
async def _mount_resolution_error(
    _request: Request,
    error: MountResolutionError,
) -> JSONResponse:
    status_code, code = _problem_for_mount_error(error)
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    fallback = (
        error.safety_fallback
        if code == "SERVICE_UNAVAILABLE"
        and status_code == 503
        and not error.retryable
        and isinstance(error.safety_fallback, CompassionateSafetyFallback)
        else None
    )
    return _problem_response(
        status_code=status_code,
        code=code,
        retryable=(error.retryable and code in {"RATE_LIMITED", "SERVICE_UNAVAILABLE"}),
        headers=headers,
        safety_fallback=fallback,
    )


@app.exception_handler(RequestValidationError)
async def _value_free_validation_error(
    _request: Request,
    _error: RequestValidationError,
) -> JSONResponse:
    """Keep validation failures from reflecting submitted PHI or credentials."""
    return _problem_response(
        status_code=422,
        code="VALIDATION_FAILED",
    )


@app.exception_handler(StarletteHTTPException)
async def _value_free_http_error(
    _request: Request,
    error: StarletteHTTPException,
) -> JSONResponse:
    """Normalize legacy FastAPI errors without reflecting their detail."""
    code = _problem_for_http_status(error.status_code)
    retry_after = None
    if error.status_code == 429 and error.headers:
        try:
            parsed = int(error.headers.get("Retry-After", ""))
            retry_after = parsed if parsed >= 1 else None
        except ValueError:
            retry_after = None
    headers = None
    if error.status_code == 401:
        headers = {"WWW-Authenticate": "Bearer"}
    return _problem_response(
        status_code=error.status_code,
        code=code,
        retryable=(code in {"RATE_LIMITED", "SERVICE_UNAVAILABLE"}),
        retry_after_seconds=retry_after,
        headers=headers,
    )


@app.exception_handler(Exception)
async def _value_free_internal_error(
    _request: Request,
    _error: Exception,
) -> JSONResponse:
    """Never let an exception string or default HTML cross the embed boundary."""
    return _problem_response(
        status_code=500,
        code="INTERNAL_ERROR",
        retryable=False,
    )


def _raw_bearer(authorization: Optional[str]) -> Optional[str]:
    """Extract one bearer without interpreting or echoing its sensitive value."""
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if (
        separator != " "
        or scheme.casefold() != "bearer"
        or not token
        or len(token) > 8192
        or token != token.strip()
        or any(character.isspace() for character in token)
    ):
        raise SovereignHTTPError(
            status_code=401,
            code="CONTEXT_INVALID",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def _one_raw_header(request: Request, name: str) -> Optional[str]:
    """Read one unconstrained header after safety checks, rejecting ambiguity."""
    values = request.headers.getlist(name)
    if len(values) > 1:
        if name.casefold() == "authorization":
            raise SovereignHTTPError(
                status_code=401,
                code="CONTEXT_INVALID",
                headers={"WWW-Authenticate": "Bearer"},
            )
        raise MountResolutionError(
            status_code=400,
            code="CLIENT_TURN_INVALID",
            retryable=False,
        )
    return values[0] if values else None


def _principal_from_token(token: Optional[str]) -> Optional[Principal]:
    """Resolve actor identity at the route's authentication boundary."""
    try:
        return resolve_principal(token, _verifier, _settings)
    except AuthError:
        # 401 with a generic reason — never echo token contents or library detail.
        raise SovereignHTTPError(
            status_code=401,
            code="CONTEXT_INVALID",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


def _is_direct_provider_host() -> bool:
    """Return whether this process serves the direct-Clerk provider mount."""
    return (
        _settings.mount_profile == MountProfile.PROVIDER_ASSISTANT
        and not _settings.platform_agent_api_enabled
    )


def _is_local_named_beta_fixture() -> bool:
    """Identify the closed, synthetic provider walkthrough profile."""
    return (
        _is_direct_provider_host()
        and _settings.demo_ui_enabled
        and _settings.mock_mode
        and _settings.auth_mode == AuthMode.DEV_HS256
        and not _settings.baa_attested
        and not _settings.feature_athena_labs
    )


_LOCAL_VISIT_PREP_MESSAGE = (
    "Using no client details, prepare a short outline for a fictional next "
    "session so I can follow the Sovereign routing walkthrough."
)
_LOCAL_REFERRAL_MESSAGE = (
    "For this synthetic walkthrough, find a faithful Catholic family medicine "
    "option available by telehealth in Texas."
)
_LOCAL_PROVIDER_MESSAGES = {
    _LOCAL_VISIT_PREP_MESSAGE,
    _LOCAL_REFERRAL_MESSAGE,
}
_LOCAL_PROVIDER_CONTEXT = {
    "clientId": "synthetic_walkthrough_01",
    "thread_ref": "thread_synthetic_walkthrough_01",
    "context_snapshot_ref": "snapshot_synthetic_walkthrough_01",
    "output_format": "chat",
    "care_model": "synthetic walkthrough",
}


def _require_local_named_beta_request(request: EmbedChatRequest) -> None:
    """Prevent the local fixture from becoming a free-form clinical surface."""
    if not _is_local_named_beta_fixture():
        return
    metadata = request.metadata.model_dump(exclude_none=True)
    if (
        request.message not in _LOCAL_PROVIDER_MESSAGES
        or metadata != _LOCAL_PROVIDER_CONTEXT
        or request.persona is not None
        or request.session_id is not None
    ):
        raise SovereignHTTPError(
            status_code=400,
            code="VALIDATION_FAILED",
            retryable=False,
        )


def _require_named_beta_provider(principal: Optional[Principal]) -> None:
    """Keep the direct provider host closed to the configured named role."""
    if not _is_direct_provider_host():
        return
    if (
        principal is None
        or not principal.subject.strip()
        or _settings.auth_provider_role not in principal.roles
    ):
        raise SovereignHTTPError(
            status_code=403,
            code="CAPABILITY_DENIED",
            retryable=False,
        )


def _require_embed_version(request: Request) -> None:
    """Require one exact v1 protocol header without reflecting its value."""
    values = request.headers.getlist(_EMBED_VERSION_HEADER)
    if values != [_EMBED_VERSION]:
        raise SovereignHTTPError(
            status_code=426,
            code="EMBED_VERSION_UNSUPPORTED",
            retryable=False,
        )


def _client_turn_uuid(value: Optional[str], *, required: bool) -> Optional[uuid.UUID]:
    """Parse only canonical random UUIDs without reflecting rejected input."""
    if value is None:
        if required:
            raise MountResolutionError(
                status_code=400,
                code="CLIENT_TURN_REQUIRED",
                retryable=False,
            )
        return None
    if len(value) != 36:
        parsed = None
    else:
        try:
            parsed = uuid.UUID(value)
        except (AttributeError, ValueError):
            parsed = None
    if (
        parsed is None
        or parsed.version != 4
        or parsed.variant != uuid.RFC_4122
        or str(parsed) != value
    ):
        raise MountResolutionError(
            status_code=400,
            code="CLIENT_TURN_INVALID",
            retryable=False,
        )
    return parsed


def _matched_client_turn_id(
    *,
    header_value: Optional[str],
    body_value: Optional[uuid.UUID],
    required: bool,
) -> Optional[uuid.UUID]:
    """Enforce byte-for-byte canonical equality at the browser boundary."""
    header_id = _client_turn_uuid(header_value, required=required)
    if header_id is not None and body_value is not None and header_id != body_value:
        raise MountResolutionError(
            status_code=409,
            code="CLIENT_TURN_CONFLICT",
            retryable=False,
        )
    if not required:
        return header_id or body_value
    if body_value is None:
        raise MountResolutionError(
            status_code=400,
            code="CLIENT_TURN_REQUIRED",
            retryable=False,
        )
    if header_id != body_value:
        raise MountResolutionError(
            status_code=409,
            code="CLIENT_TURN_CONFLICT",
            retryable=False,
        )
    return header_id


# CORS: deny by default. A browser-facing deployment must set
# CORS_ALLOW_ORIGINS to an explicit allowlist; we never use "*".
if _settings.cors_allow_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=_CORS_ALLOW_METHODS,
        allow_headers=_CORS_ALLOW_HEADERS,
        expose_headers=_CORS_EXPOSE_HEADERS,
    )

# Reject oversized bodies before they are parsed (defense-in-depth on top of the
# per-field length limits in schemas.py).
_MAX_BODY_BYTES = 256 * 1024


@app.middleware("http")
async def _security_middleware(request: Request, call_next):
    content_lengths = request.headers.getlist("content-length")
    transfer_encodings = request.headers.getlist("transfer-encoding")
    declared_length: Optional[int] = None
    if (
        (content_lengths and transfer_encodings)
        or len(transfer_encodings) > 1
        or (transfer_encodings and transfer_encodings[0].casefold() != "chunked")
    ):
        response = _problem_response(
            status_code=400,
            code="VALIDATION_FAILED",
        )
        if request.url.path in _PROTECTED_EMBED_PATHS:
            response.headers[_EMBED_VERSION_HEADER] = _EMBED_VERSION
        return response
    if content_lengths:
        try:
            if len(content_lengths) != 1 or not content_lengths[0].isdigit():
                raise ValueError
            declared_length = int(content_lengths[0])
        except ValueError:
            response = _problem_response(
                status_code=400,
                code="VALIDATION_FAILED",
            )
            if request.url.path in _PROTECTED_EMBED_PATHS:
                response.headers[_EMBED_VERSION_HEADER] = _EMBED_VERSION
            return response
        if declared_length > _MAX_BODY_BYTES:
            response = _problem_response(
                status_code=413,
                code="VALIDATION_FAILED",
            )
            if request.url.path in _PROTECTED_EMBED_PATHS:
                response.headers[_EMBED_VERSION_HEADER] = _EMBED_VERSION
            return response

    # Content-Length is only a hint. Count the actual ASGI request chunks so
    # chunked and HTTP/2 bodies cannot bypass the 256 KiB boundary. Caching the
    # bounded bytes lets FastAPI/Pydantic consume exactly the verified body.
    chunks: list[bytes] = []
    actual_length = 0
    async for chunk in request.stream():
        actual_length += len(chunk)
        if actual_length > _MAX_BODY_BYTES:
            response = _problem_response(
                status_code=413,
                code="VALIDATION_FAILED",
            )
            if request.url.path in _PROTECTED_EMBED_PATHS:
                response.headers[_EMBED_VERSION_HEADER] = _EMBED_VERSION
            return response
        chunks.append(chunk)
    request._body = b"".join(chunks)  # Starlette's documented body cache slot.
    if declared_length is not None and declared_length != actual_length:
        response = _problem_response(
            status_code=400,
            code="VALIDATION_FAILED",
        )
        if request.url.path in _PROTECTED_EMBED_PATHS:
            response.headers[_EMBED_VERSION_HEADER] = _EMBED_VERSION
        return response
    response = await call_next(request)
    if request.url.path in _PROTECTED_EMBED_PATHS:
        response.headers[_EMBED_VERSION_HEADER] = _EMBED_VERSION
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "mock_mode": _settings.mock_mode,
            "auth_mode": _settings.auth_mode.value,
            "mount_profile": _settings.mount_profile.value,
            "platform_agent_api_enabled": _settings.platform_agent_api_enabled}


def _client_ip(request: Request) -> str:
    """Resolve the address appended by the single trusted Container Apps ingress."""
    forwarded_for = (
        request.headers.get("x-forwarded-for")
        if _settings.public_chat_trust_forwarded_for
        else None
    )
    candidates = []
    if forwarded_for:
        # Container Apps appends the direct client to the right of any
        # caller-supplied values. Never trust an arbitrary left-most value.
        candidates.append(forwarded_for.rsplit(",", 1)[-1].strip())
    if request.client is not None:
        candidates.append(request.client.host)
    for candidate in candidates:
        try:
            return ipaddress.ip_address(candidate).compressed
        except ValueError:
            continue
    return "unknown"


@app.post("/chat", response_model=EmbedChatResponse)
async def chat(
    request: EmbedChatRequest,
    http_request: Request,
) -> ChatResponse:
    pipeline_request = ChatRequest(
        client_turn_id=request.client_turn_id,
        persona=request.persona or Persona.PATIENT,
        message=request.message,
        metadata=request.metadata.model_dump(exclude_none=True),
        session_id=request.session_id,
    )
    principal: Optional[Principal] = None
    bearer: Optional[str] = None
    direct_provider_host = _is_direct_provider_host()
    if direct_provider_host:
        # A named provider host is closed before any orchestration, including
        # the otherwise pre-auth emergency shortcut. The anonymous public mount
        # retains its deterministic emergency guidance below.
        _require_embed_version(http_request)
        authorization = _one_raw_header(http_request, "Authorization")
        bearer = _raw_bearer(authorization)
        principal = _principal_from_token(bearer)
        _require_named_beta_provider(principal)
        _require_local_named_beta_request(request)

    # The public mount's only pre-context exception is deterministic acute-
    # emergency guidance. It performs no model call or mount resolution.
    if safety.detect_emergency(request.message):
        response = await _sovereign.handle(
            pipeline_request,
            principal=principal,
        )
        return response.model_copy(
            update={"client_turn_id": request.client_turn_id}
        )
    if not direct_provider_host:
        _require_embed_version(http_request)
    if _settings.mount_profile == MountProfile.PUBLIC:
        decision = _public_chat_limiter.check(_client_ip(http_request))
        if not decision.allowed:
            raise SovereignHTTPError(
                status_code=429,
                code="RATE_LIMITED",
                retryable=True,
                retry_after_seconds=decision.retry_after_seconds,
            )
    authorization = _one_raw_header(http_request, "Authorization")
    client_turn_value = _one_raw_header(
        http_request, "X-Cosmaneura-Client-Turn-ID"
    )
    bearer = _raw_bearer(authorization)
    if _settings.platform_agent_api_enabled:
        principal = None
        mount_context_token = bearer
    else:
        if not direct_provider_host:
            principal = _principal_from_token(bearer)
        _require_named_beta_provider(principal)
        mount_context_token = None
    client_turn_id = _matched_client_turn_id(
        header_value=client_turn_value,
        body_value=request.client_turn_id,
        required=_settings.platform_agent_api_enabled,
    )
    response = await _sovereign.handle(
        pipeline_request,
        principal=principal,
        mount_context_token=mount_context_token,
        client_turn_id=client_turn_id,
    )
    return response.model_copy(update={"client_turn_id": client_turn_id})


# The AI speaks first (see INTERACTION_DESIGN.md). Deterministic, config-served
# greeting + guided paths — no LLM call, nothing for the arbiter to review, and
# each path maps 1:1 to an Intent so a tapped chip is a near-perfect routing
# signal. Both surfaces (standalone page, Cosmas dashboard panel) bootstrap
# from this one endpoint; per-tenant branding arrives with the Phase 2 layer.
_PATIENT_PATHS = [
    {"label": "Find a Catholic doctor",
     "message": "Help me find a Catholic doctor who shares my values.",
     "intent": "referral"},
    {"label": "Understand a condition",
     "message": "Help me understand a condition and prepare questions for my appointment.",
     "intent": "engagement"},
    {"label": "Explore clinical trials",
     "message": "What clinical trials or emerging treatments exist for my condition?",
     "intent": "concierge"},
    {"label": "What the Church teaches",
     "message": "What does the Church teach about a health topic I'm facing?",
     "intent": "doctrinal"},
]
_PROVIDER_PATHS = [
    {"label": "Prep my next session",
     "message": "Help me organize my next session with this client — a few talking "
                "points and a short outline.",
     "intent": "visit_prep"},
    {"label": "Ethics consult (ERDs)",
     "message": "I have a case question about what the Ethical and Religious "
                "Directives permit and which alternatives I may offer.",
     "intent": "ethics_consult"},
    {"label": "Explain a diagnosis",
     "message": "Help me explain this patient's new diagnosis before the visit.",
     "intent": "engagement"},
    {"label": "Find a faithful specialist",
     "message": "Help me find a faithful Catholic specialist for a referral.",
     "intent": "referral"},
    # Rendered only when the EMR lab feature is enabled (see /welcome below).
    {"label": "Review labs (EMR)",
     "message": "Anything concerning in her labs? Show me the A1C trend.",
     "intent": "lab_review"},
]
_LOCAL_PROVIDER_PATHS = [
    {"label": "Trace synthetic session prep",
     "message": _LOCAL_VISIT_PREP_MESSAGE,
     "intent": "visit_prep"},
    {"label": "Trace an offline referral",
     "message": _LOCAL_REFERRAL_MESSAGE,
     "intent": "referral"},
]


def _required_capability_for_path(path: dict) -> Optional[str]:
    """Return no grant for malformed or newly added, unmapped welcome paths."""
    try:
        intent = Intent(path["intent"])
    except (KeyError, TypeError, ValueError):
        return None
    return INTENT_CAPABILITIES.get(intent)


@app.get("/welcome")
async def welcome(http_request: Request) -> dict:
    _require_embed_version(http_request)
    name = _settings.partner_name
    if _settings.platform_agent_api_enabled:
        authorization = _one_raw_header(http_request, "Authorization")
        mount_context_token = _raw_bearer(authorization)
        bootstrap = await _sovereign.bootstrap_mount_context(
            mount_context_token=mount_context_token,
        )
        mount = bootstrap.context
        if mount.surface != "provider_assistant":
            raise MountResolutionError(
                status_code=403,
                code="MOUNT_SURFACE_MISMATCH",
                retryable=False,
            )
    else:
        mount = _settings.deployment_mount
        if mount.surface == "provider_assistant":
            authorization = _one_raw_header(http_request, "Authorization")
            bearer = _raw_bearer(authorization)
            principal = _principal_from_token(bearer)
            _require_named_beta_provider(principal)
    persona = (
        Persona.PROVIDER
        if mount.surface == "provider_assistant"
        else Persona.PATIENT
    )
    local_fixture = _is_local_named_beta_fixture()
    if local_fixture:
        greeting = ("Welcome to the local named-provider walkthrough. Use only the "
                    "synthetic guided paths below; do not enter real clinical, "
                    "visit, or account information.")
    elif persona == Persona.PROVIDER:
        greeting = (f"Welcome — I'm the {name} clinical assistant. I can prep your "
                    "next session, prepare an ERD ethics consult, explain a "
                    "diagnosis for a visit, or find a faithful specialist.")
    else:
        greeting = (f"Welcome — I'm the {name} health assistant, here to give you "
                    "clear, trustworthy health information guided by Catholic "
                    "teaching. I'm not a doctor and I can't diagnose or treat "
                    "anything — think of me as preparation for real care, not a "
                    "replacement for it.")
    candidates = (
        _LOCAL_PROVIDER_PATHS
        if local_fixture
        else _PROVIDER_PATHS if persona == Persona.PROVIDER else _PATIENT_PATHS
    )
    granted = set(mount.capabilities)
    if _settings.platform_agent_api_enabled:
        # The provider-first activation deliberately omits the labs lane even
        # if an unexpectedly broad platform response carries the future grant.
        granted.discard("ehr:labs:read")
    labs_sandbox = (
        persona == Persona.PROVIDER
        and not _settings.platform_agent_api_enabled
        and _settings.feature_athena_labs
        and _settings.athena_env == "preview"
        and _settings.athena_practice_id == "195900"
    )
    paths = [
        path
        for path in candidates
        if _required_capability_for_path(path) in granted
        and (path["intent"] != "lab_review" or labs_sandbox)
    ]
    payload = {
        "partner_name": name,
        "persona": persona.value,
        "greeting": greeting,
        "paths": paths,
        "disclaimer": safety.disclaimer_for(persona),
        "emergency_note": "If this is an emergency, call 911 now.",
    }
    if local_fixture:
        payload["walkthrough_mode"] = "local_synthetic"
    if not _settings.platform_agent_api_enabled:
        # Temporary legacy-only sandbox labeling. The ratified v1 platform
        # WelcomeResponse is closed and intentionally carries neither field.
        payload["labs_tier"] = "sandbox" if labs_sandbox else None
        payload["labs_label"] = SANDBOX_LABEL if labs_sandbox else None
    return payload


# Legacy journey-digest bridge. Platform mode refuses before mount resolution
# because the first provider capability set omits the ratified platform-owned
# snapshot/digest path; it must never run an unpersisted model call.
@app.post("/digest", response_model=DigestResponse)
async def digest(
    request: DigestRequest,
    http_request: Request,
) -> DigestResponse:
    _require_embed_version(http_request)
    if _settings.platform_agent_api_enabled:
        # The first platform capability set deliberately omits digest writes.
        # Refuse before reading credentials or opening an interaction lineage.
        raise SovereignHTTPError(
            status_code=503,
            code="SERVICE_UNAVAILABLE",
            retryable=False,
        )
    authorization = _one_raw_header(http_request, "Authorization")
    client_turn_value = _one_raw_header(
        http_request, "X-Cosmaneura-Client-Turn-ID"
    )
    bearer = _raw_bearer(authorization)
    principal = _principal_from_token(bearer)
    _require_named_beta_provider(principal)
    if _is_local_named_beta_fixture():
        raise SovereignHTTPError(
            status_code=503,
            code="SERVICE_UNAVAILABLE",
            retryable=False,
        )
    client_turn_id = _matched_client_turn_id(
        header_value=client_turn_value,
        body_value=request.client_turn_id,
        required=False,
    )
    try:
        response = await _sovereign.digest(
            request,
            principal=principal,
            mount_context_token=None,
            client_turn_id=client_turn_id,
        )
    except PermissionError:
        raise SovereignHTTPError(
            status_code=403,
            code="CAPABILITY_DENIED",
            retryable=False,
        ) from None
    return response.model_copy(update={"client_turn_id": client_turn_id})


# Drop-in panel component (reference implementation). Static JS, no secrets —
# the mounting host supplies auth + context via callbacks. Read bytes so the
# versioned route and its temporary compatibility alias are byte-identical.
_PANEL_JS = (Path(__file__).parent / "panel.js").read_bytes()


@app.get("/embed/v1/panel.js")
async def panel_js() -> Response:
    return Response(content=_PANEL_JS, media_type="application/javascript")


@app.get("/panel.js")
async def deprecated_panel_js() -> Response:
    """Compatibility alias; new hosts must pin the versioned embed contract."""
    return Response(
        content=_PANEL_JS,
        media_type="application/javascript",
        headers={
            "Deprecation": "true",
            "Link": '</embed/v1/panel.js>; rel="successor-version"',
        },
    )


# Built-in chat demo UI (same-origin, so it talks to /chat without CORS). Gated
# off by default; the launch script tools/run_demo_web.sh enables it locally.
if _settings.demo_ui_enabled:
    _DEMO_HTML = (Path(__file__).parent / "demo.html").read_text()
    # Harness that mounts the versioned panel inside a stand-in for the Cosmas
    # notetaking dashboard — the Surface B experience, same-origin so it
    # needs no CORS setup locally (production hosts are cross-origin and
    # must be added to CORS_ALLOW_ORIGINS).
    _DASHBOARD_HTML = (Path(__file__).parent / "demo_dashboard.html").read_text()

    @app.get("/demo", response_class=HTMLResponse)
    async def demo() -> str:
        return _DEMO_HTML

    @app.get("/demo/dashboard", response_class=HTMLResponse)
    async def demo_dashboard() -> str:
        return _DASHBOARD_HTML
