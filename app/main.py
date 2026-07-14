"""
HTTP surface for the Sovereign.

Two logical products, one codebase. The persona in the request body selects the
compliance profile (patient = SOC 2 only; provider = SOC 2 + HIPAA). In real
deployments you would also bind persona to authn (e.g. a provider JWT) rather
than trusting the body alone — see SETUP_AND_HANDOFF.md.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import AuthMode, get_settings
from app.schemas import ChatRequest, ChatResponse
from app.security.auth import AuthError, Principal, TokenVerifier, resolve_principal
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
    token = creds.credentials if creds else None
    try:
        return resolve_principal(token, _verifier, _settings)
    except AuthError:
        # 401 with a generic reason — never echo token contents or library detail.
        raise HTTPException(status_code=401, detail="Not authenticated.",
                            headers={"WWW-Authenticate": "Bearer"})


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
async def chat(request: ChatRequest,
               principal: Optional[Principal] = Depends(get_principal)) -> ChatResponse:
    return await _sovereign.handle(request, principal=principal)


# Built-in chat demo UI (same-origin, so it talks to /chat without CORS). Gated
# off by default; the launch script tools/run_demo_web.sh enables it locally.
if _settings.demo_ui_enabled:
    _DEMO_HTML = (Path(__file__).parent / "demo.html").read_text()

    @app.get("/demo", response_class=HTMLResponse)
    async def demo() -> str:
        return _DEMO_HTML
