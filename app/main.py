"""
HTTP surface for the Sovereign.

Two logical products, one codebase. A server-resolved deployment mount selects
the compliance profile and capabilities. The legacy request persona is never
an authority input.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.compliance import safety
from app.config import AuthMode, Persona, get_settings
from app.schemas import ChatRequest, ChatResponse, DigestRequest, DigestResponse
from app.security.auth import AuthError, Principal, TokenVerifier, resolve_principal
from app.security.mount import MountResolutionError
from app.sovereign.orchestrator import Sovereign

app = FastAPI(title="CosmaNeura Sovereign", version="0.1.0")
_sovereign = Sovereign()
_settings = get_settings()
_verifier = TokenVerifier(_settings)

# auto_error=False so "disabled" mode tolerates a missing header; we enforce
# presence ourselves in the dependency for dev/oidc modes.
_bearer = HTTPBearer(auto_error=False)


async def get_principal(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[Principal]:
    """Verify the bearer token (if required by AUTH_MODE) and return a Principal."""
    return _principal_from_credentials(creds)


def _principal_from_credentials(
    creds: Optional[HTTPAuthorizationCredentials],
) -> Optional[Principal]:
    """Resolve actor identity after any pre-auth emergency short-circuit."""
    token = creds.credentials if creds else None
    try:
        return resolve_principal(token, _verifier, _settings)
    except AuthError:
        # 401 with a generic reason — never echo token contents or library detail.
        raise HTTPException(status_code=401, detail="Not authenticated.",
                            headers={"WWW-Authenticate": "Bearer"})


def _mount_http_exception(error: MountResolutionError) -> HTTPException:
    """Normalize resolver failures without exposing internal policy details."""
    status_code = error.status_code if error.status_code in {401, 403} else 503
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    return HTTPException(
        status_code=status_code,
        detail="Assistant surface is not available.",
        headers=headers,
    )


# CORS: deny by default. A browser-facing deployment must set
# CORS_ALLOW_ORIGINS to an explicit allowlist; we never use "*".
if _settings.cors_allow_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["content-type", "authorization"],
    )

# Reject oversized bodies before they are parsed (defense-in-depth on top of the
# per-field length limits in schemas.py).
_MAX_BODY_BYTES = 256 * 1024


@app.middleware("http")
async def _security_middleware(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > _MAX_BODY_BYTES:
                return JSONResponse(status_code=413, content={"detail": "Request too large."})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Bad Content-Length."})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "mock_mode": _settings.mock_mode,
            "auth_mode": _settings.auth_mode.value}


@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> ChatResponse:
    # The only pre-context exception is deterministic acute-emergency guidance.
    # Do not let missing/invalid auth suppress 911/988 instructions.
    if safety.detect_emergency(request.message):
        return await _sovereign.handle(request)
    principal = _principal_from_credentials(creds)
    try:
        return await _sovereign.handle(request, principal=principal)
    except MountResolutionError as error:
        raise _mount_http_exception(error) from None


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


@app.get("/welcome")
async def welcome(persona: Persona = Persona.PATIENT) -> dict:
    name = _settings.partner_name
    if persona == Persona.PROVIDER:
        greeting = (f"Welcome — I'm the {name} clinical assistant. I can prep your "
                    "next session, prepare an ERD ethics consult, explain a "
                    "diagnosis for a visit, or find a faithful specialist.")
    else:
        greeting = (f"Welcome — I'm the {name} health assistant, here to give you "
                    "clear, trustworthy health information guided by Catholic "
                    "teaching. I'm not a doctor and I can't diagnose or treat "
                    "anything — think of me as preparation for real care, not a "
                    "replacement for it.")
    paths = _PROVIDER_PATHS if persona == Persona.PROVIDER else _PATIENT_PATHS
    # The lab chip only shows where the EMR feature is actually on — a chip
    # that leads to "not enabled on this deployment" is UI noise.
    if not _settings.feature_athena_labs:
        paths = [p for p in paths if p["intent"] != "lab_review"]
    return {
        "partner_name": name,
        "persona": persona.value,
        "greeting": greeting,
        "paths": paths,
        "disclaimer": safety.disclaimer_for(persona),
        "emergency_note": "If this is an emergency, call 911 now.",
    }


# Journey digest: the host app (Cosmas dashboard / website) calls this once
# per session at note-write time; the returned digest is stored per patient
# and later sent to /chat as one compact metadata.journey_digest field. This
# is how multi-visit context scales without shipping N transcripts per chat
# message. Same actor + mount capability gates as /chat; BAA tripwire inside.
@app.post("/digest", response_model=DigestResponse)
async def digest(request: DigestRequest,
                 principal=Depends(get_principal)) -> DigestResponse:
    try:
        return await _sovereign.digest(request, principal=principal)
    except MountResolutionError as error:
        raise _mount_http_exception(error) from None
    except PermissionError:
        raise HTTPException(status_code=403,
                            detail="This deployment is not configured to "
                                   "accept provider (PHI) requests.")


# Drop-in dashboard panel component (reference implementation). Static JS,
# no secrets — the mounting host supplies auth + context via callbacks.
_PANEL_JS = (Path(__file__).parent / "panel.js").read_text()


@app.get("/panel.js")
async def panel_js() -> Response:
    return Response(content=_PANEL_JS, media_type="application/javascript")


# Built-in chat demo UI (same-origin, so it talks to /chat without CORS). Gated
# off by default; the launch script tools/run_demo_web.sh enables it locally.
if _settings.demo_ui_enabled:
    _DEMO_HTML = (Path(__file__).parent / "demo.html").read_text()
    # Harness that mounts panel.js inside a stand-in for the Cosmas
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
