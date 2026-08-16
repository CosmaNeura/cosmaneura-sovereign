"""
Local proof of the Clerk <-> Sovereign OIDC identity contract.

Clerk is, mechanically, an OIDC-style issuer: it publishes a JWKS and signs
RS256 JWTs. This script stands up a *fake Clerk* entirely on localhost --
- generates an RSA keypair (Clerk's signing key),
- serves its public half as a JWKS over a loopback HTTP server,
- mints RS256 tokens with an issuer / audience / role claim (a Clerk token),
and then drives the Sovereign's REAL OIDC verifier and orchestrator against it.

The only production control relaxed here is the SSRF guard on the JWKS URL: in
production it (correctly) permits Clerk's public HTTPS host and rejects
loopback; locally we permit loopback so the demo can fetch its own JWKS. Every
cryptographic and policy check -- RS256 signature, kid lookup, issuer/audience/
expiry, algorithm-confusion rejection, persona-from-verified-claim -- is the
real code path in app/security/auth.py.

Run:  PYTHONPATH=. .venv/bin/python tools/clerk_oidc_local_demo.py
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

# --- Simulated Clerk configuration --------------------------------------------
CLERK_ISSUER = "https://demo-clerk.example.com"   # OIDC_ISSUER
AUDIENCE = "cosmaneura-sovereign"                  # OIDC_AUDIENCE (Clerk JWT template)
ROLE_CLAIM = "org_role"                            # AUTH_ROLE_CLAIM (a Clerk claim)
PROVIDER_ROLE = "clinician"                        # AUTH_PROVIDER_ROLE
KID = "clerk-demo-key-1"

# 1) Clerk's signing key + the JWKS it would publish.
_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_private_key.public_key()))
_jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
JWKS = {"keys": [_jwk]}


def _serve_jwks() -> str:
    """Start a loopback HTTP server that serves the JWKS; return its URL."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps(JWKS).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):  # silence the default request logging
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    return f"http://127.0.0.1:{port}/jwks.json"


def mint(role: str | None, *, aud=AUDIENCE, iss=CLERK_ISSUER, ttl=3600, alg="RS256",
         key=None) -> str:
    """Mint a Clerk-style token. role=None => no provider role (a patient)."""
    now = int(time.time())
    claims = {"sub": "user_2abcCLERKid", "iss": iss, "aud": aud,
              "iat": now, "exp": now + ttl}
    if role is not None:
        claims[ROLE_CLAIM] = [role]
    signing_key = key if key is not None else _private_key
    return jwt.encode(claims, signing_key, algorithm=alg, headers={"kid": KID})


def main() -> None:
    jwks_url = _serve_jwks()

    # Relax ONLY the SSRF guard, and only for loopback (prod allows Clerk's host).
    import app.security.auth as auth_mod
    _real_guard = auth_mod.assert_safe_outbound_url
    auth_mod.assert_safe_outbound_url = lambda url: None  # loopback allowed locally

    from app.config import MountProfile, Persona, Settings
    from app.schemas import ChatRequest
    from app.security.auth import AuthError, TokenVerifier, resolve_principal
    from app.security.mount import MountResolutionError
    from app.sovereign.orchestrator import Sovereign
    import app.compliance.audit as audit_mod

    # Capture audit events so we can prove persona_override is recorded.
    events: list[dict] = []
    _real_record = audit_mod.AuditLogger.record

    def _capture(self, *, trace_id, profile, event, details=None, content_for_hash=None):
        events.append({"event": event, "details": details or {}})
        return _real_record(self, trace_id=trace_id, profile=profile, event=event,
                            details=details, content_for_hash=content_for_hash)

    audit_mod.AuditLogger.record = _capture

    # Settings exactly as a provider deployment would set them for Clerk.
    settings = Settings(
        mock_mode=True,                 # no real LLM / Magisterium keys needed
        auth_mode="oidc",
        oidc_issuer=CLERK_ISSUER,
        oidc_audience=AUDIENCE,
        oidc_jwks_url=jwks_url,
        oidc_algorithms=["RS256"],
        auth_role_claim=ROLE_CLAIM,
        auth_provider_role=PROVIDER_ROLE,
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_clerk_demo",
        allow_provider_persona=False,   # irrelevant: a VERIFIED provider is allowed
    )
    verifier = TokenVerifier(settings)
    sovereign = Sovereign(settings)

    passed = failed = 0

    def check(name: str, cond: bool, extra: str = ""):
        nonlocal passed, failed
        mark = "PASS" if cond else "FAIL"
        if cond:
            passed += 1
        else:
            failed += 1
        print(f"  [{mark}] {name}" + (f"  -- {extra}" if extra else ""))

    def expect_autherror(name: str, token: str):
        try:
            verifier.verify(token)
            check(name, False, "token was accepted but should have been rejected")
        except AuthError as e:
            check(name, True, str(e))

    print("\n=== A. Token verification (real RS256 path against the JWKS) ===")
    p = verifier.verify(mint(PROVIDER_ROLE))
    check("clinician token -> PROVIDER persona", p.persona == Persona.PROVIDER,
          f"subject={p.subject} roles={p.roles}")

    p = verifier.verify(mint(None))
    check("no-role token -> PATIENT persona", p.persona == Persona.PATIENT,
          f"roles={p.roles}")

    p = verifier.verify(mint("nurse"))
    check("non-provider role -> PATIENT persona", p.persona == Persona.PATIENT,
          f"roles={p.roles}")

    print("\n=== B. Tokens that MUST be rejected ===")
    expect_autherror("expired token rejected", mint(PROVIDER_ROLE, ttl=-10))
    expect_autherror("wrong audience rejected", mint(PROVIDER_ROLE, aud="some-other-api"))
    expect_autherror("wrong issuer rejected", mint(PROVIDER_ROLE, iss="https://evil.example"))
    # Algorithm confusion: an HMAC-signed token must never be honored in oidc
    # mode (the verifier restricts to asymmetric algs), so a forged HS256 token
    # claiming the provider role is rejected outright.
    hs_forged = jwt.encode({"sub": "attacker", "iss": CLERK_ISSUER, "aud": AUDIENCE,
                            ROLE_CLAIM: [PROVIDER_ROLE],
                            "exp": int(time.time()) + 600}, "x" * 32, algorithm="HS256",
                           headers={"kid": KID})
    expect_autherror("algorithm-confusion (HS256) rejected", hs_forged)
    try:
        resolve_principal(None, verifier, settings)
        check("missing token rejected in oidc mode", False)
    except AuthError as e:
        check("missing token rejected in oidc mode", True, str(e))

    print("\n=== C. End-to-end: persona comes from the VERIFIED claim, not the body ===")

    async def handle(token: str, body_persona: Persona):
        events.clear()
        principal = verifier.verify(token)
        req = ChatRequest(persona=body_persona,
                          message="I need to find a Catholic OB-GYN near me",
                          metadata={"state": "NH", "specialty": "obstetrics"})
        resp = await sovereign.handle(req, principal=principal)
        overrides = [e for e in events if e["event"] == "persona_override"]
        return resp, overrides

    # Provider token, but body lies and says "patient": verified PROVIDER wins.
    resp, ov = asyncio.run(handle(mint(PROVIDER_ROLE), Persona.PATIENT))
    check("provider token + body=patient -> handled as PROVIDER",
          resp.persona == Persona.PROVIDER)
    check("  ...and the override attempt is audited",
          bool(ov) and ov[0]["details"].get("granted") == "provider",
          json.dumps(ov[0]["details"]) if ov else "no persona_override event")

    # A patient token cannot enter this physically separate provider mount,
    # regardless of what its body claims. It is rejected before routing.
    try:
        asyncio.run(handle(mint(None), Persona.PROVIDER))
        check("patient token rejected by provider mount", False)
    except MountResolutionError as exc:
        check("patient token rejected by provider mount",
              exc.code == "PROVIDER_ACTOR_REQUIRED", exc.code)

    # restore patched globals (tidy for any importer)
    auth_mod.assert_safe_outbound_url = _real_guard
    audit_mod.AuditLogger.record = _real_record

    print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
