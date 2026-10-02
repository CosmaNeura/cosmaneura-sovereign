"""
Authentication: turn a bearer token into a trusted Principal.

This is the control that closes the biggest open gap from the security reviews:
The HTTP layer extracts and verifies actor identity. Mount context, not the
Principal's role/persona or any request-body field, is the authorization axis;
the orchestrator uses only the verified subject to bind the actor.

Three modes (config: AUTH_MODE):

  * disabled   -- no actor token required. Safe only for anonymous public
                  pinned mounts and local development. NOT for provider.
  * dev_hs256  -- tokens signed with a shared secret (HS256). Lets a developer
                  mint test tokens without an identity provider, and lets the
                  test suite run offline. NOT for production.
  * oidc       -- production. Verifies RS256/ES256 signatures against the
                  identity provider's JWKS endpoint, plus issuer / audience /
                  expiry. Provider-agnostic (Auth0, Cognito, Entra, Okta,
                  Keycloak, ...).

This module deliberately does NOT import FastAPI: the web wiring lives in
main.py so the verifier stays usable from any context (and from tests).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import asyncio
import json
from uuid import UUID
import httpx

from app.compliance.netsec import assert_safe_outbound_url
from app.config import AuthMode, Persona, Settings, get_settings

try:  # pyjwt is only required when AUTH_MODE != disabled
    import jwt
    from jwt import PyJWKClient
except Exception:  # pragma: no cover - exercised only in minimal installs
    jwt = None
    PyJWKClient = None

# Asymmetric algorithms only for OIDC. Allowing an HMAC alg with JWKS-published
# public keys enables the classic algorithm-confusion attack, so HS* is rejected
# in oidc mode (see _validate_oidc_algorithms).
_ASYMMETRIC_ALGS = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512",
                    "PS256", "PS384", "PS512", "EdDSA"}


class AuthError(Exception):
    """Token missing, malformed, expired, or otherwise not trustworthy."""


@dataclass
class Principal:
    """A verified caller identity."""
    subject: str
    persona: Persona
    roles: list[str] = field(default_factory=list)
    claims: dict[str, Any] = field(default_factory=dict)


def _dig(claims: dict[str, Any], dotted: str) -> Any:
    """Look up a (possibly nested, dotted) claim, e.g. 'realm_access.roles'."""
    cur: Any = claims
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _extract_roles(claims: dict[str, Any], settings: Settings) -> list[str]:
    val = _dig(claims, settings.auth_role_claim)
    if val is None:
        return []
    if isinstance(val, str):
        # Some IdPs put roles/scopes in a space-delimited string (e.g. `scope`).
        return val.split()
    if isinstance(val, (list, tuple)):
        return [str(x) for x in val]
    return []


def map_persona(claims: dict[str, Any], settings: Settings) -> tuple[Persona, list[str]]:
    """Derive the compliance persona from verified claims. Defaults to PATIENT."""
    roles = _extract_roles(claims, settings)
    persona = Persona.PROVIDER if settings.auth_provider_role in roles else Persona.PATIENT
    return persona, roles


def _validate_oidc_algorithms(algs: list[str]) -> list[str]:
    bad = [a for a in algs if a not in _ASYMMETRIC_ALGS]
    if bad:
        raise AuthError(
            f"OIDC mode allows asymmetric algorithms only; refusing {bad}. "
            "Allowing HMAC with JWKS public keys enables algorithm confusion."
        )
    return algs


class TokenVerifier:
    """Verifies a bearer token according to the configured AUTH_MODE."""

    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or get_settings()
        self._jwk_client = None  # built lazily in oidc mode

        if self.s.auth_mode == AuthMode.OIDC:
            _validate_oidc_algorithms(self.s.oidc_algorithms)
            if not self.s.oidc_jwks_url:
                raise AuthError("AUTH_MODE=oidc requires OIDC_JWKS_URL.")
            # Defense-in-depth: refuse a JWKS URL that points somewhere unsafe.
            assert_safe_outbound_url(self.s.oidc_jwks_url)

    def _jwks(self):
        if self._jwk_client is None:
            if PyJWKClient is None:
                raise AuthError("pyjwt is not installed; required for AUTH_MODE=oidc.")
            self._jwk_client = PyJWKClient(self.s.oidc_jwks_url)
        return self._jwk_client

    def _decode_options(self) -> tuple[dict, Optional[str], Optional[str]]:
        # Always require an expiry. Verify issuer/audience when configured.
        options = {"require": ["exp"]}
        audience = self.s.oidc_audience
        issuer = self.s.oidc_issuer
        if audience is None:
            options["verify_aud"] = False
        return options, audience, issuer

    def verify(self, token: str) -> Principal:
        if jwt is None:
            raise AuthError("pyjwt is not installed; required for token verification.")
        options, audience, issuer = self._decode_options()
        try:
            if self.s.auth_mode == AuthMode.DEV_HS256:
                if not self.s.auth_dev_secret:
                    raise AuthError("AUTH_MODE=dev_hs256 requires AUTH_DEV_SECRET.")
                claims = jwt.decode(
                    token, self.s.auth_dev_secret, algorithms=["HS256"],
                    audience=audience, issuer=issuer, options=options,
                )
            elif self.s.auth_mode == AuthMode.OIDC:
                signing_key = self._jwks().get_signing_key_from_jwt(token)
                claims = jwt.decode(
                    token, signing_key.key, algorithms=self.s.oidc_algorithms,
                    audience=audience, issuer=issuer, options=options,
                )
            else:
                raise AuthError(f"verify() called in AUTH_MODE={self.s.auth_mode}.")
        except AuthError:
            raise
        except Exception as exc:
            # Never surface library internals / token contents to the caller.
            raise AuthError(f"token rejected ({exc.__class__.__name__})") from None

        persona, roles = map_persona(claims, self.s)
        return Principal(subject=str(claims.get("sub", "")), persona=persona,
                         roles=roles, claims=claims)


def resolve_principal(
    token: Optional[str], verifier: TokenVerifier, settings: Optional[Settings] = None,
) -> Optional[Principal]:
    """
    Apply mode policy to a (possibly absent) token.

    * disabled -> None (the pinned public mount remains authoritative).
    * dev/oidc -> a token is REQUIRED; returns a verified Principal or raises.
    """
    s = settings or get_settings()
    if s.auth_mode == AuthMode.DISABLED:
        return None
    if not token:
        raise AuthError("missing bearer token")
    return verifier.verify(token)


async def verify_visit_prep_core_workload(token: str, settings: Settings) -> None:
    """Separate app-only Core admission, never patient/provider identity.

    Fixed Entra v2 authority, one client, RS256 and bounded key fetch. Disabled
    generic/dev auth never bypasses this verifier. Raw claims stay in this module.
    """
    try:
        tenant, audience, client_id = (
            str(UUID(value or "")) for value in (
                settings.visit_prep_core_tenant_id, settings.visit_prep_receiver_audience,
                settings.visit_prep_core_client_id)
        )
        if (not settings.visit_prep_patient_clarification_enabled
            or any(UUID(value).int == 0 for value in (tenant, audience, client_id))
            or jwt is None or not 1 <= len(token) <= 8192 or not token.isascii()
            or any(char.isspace() for char in token)):
            raise ValueError
        header = jwt.get_unverified_header(token)
        if (header.get("alg") != "RS256" or header.get("crit")
            or not isinstance(header.get("kid"), str) or not 1 <= len(header["kid"]) <= 200):
            raise ValueError
        issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"
        async with asyncio.timeout(4):
            async with httpx.AsyncClient(timeout=4, trust_env=False, follow_redirects=False) as client:
                async with client.stream("GET", f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
                    headers={"Accept-Encoding": "identity"}) as response:
                    if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                        raise ValueError
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        body.extend(part)
                        if len(body) > 65536:
                            raise ValueError
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        keys = json.loads(body, object_pairs_hook=unique)["keys"]
        if not isinstance(keys, list) or len(keys) > 32:
            raise ValueError
        matches = [key for key in keys if isinstance(key, dict) and key.get("kid") == header["kid"]]
        if (len(matches) != 1 or matches[0].get("kty") != "RSA"
            or matches[0].get("use") not in {None, "sig"} or matches[0].get("alg") not in {None, "RS256"}):
            raise ValueError
        claims = jwt.decode(token, jwt.PyJWK.from_dict(matches[0], algorithm="RS256").key,
            algorithms=["RS256"], issuer=issuer, audience=audience,
            options={"require": ["sub", "tid", "iat", "nbf", "exp", "aud", "iss"]})
        bounds = [claims.get(name) for name in ("nbf", "iat", "exp")]
        if (not all(type(value) is int and 0 <= value <= 253402300799 for value in bounds)
            or max(bounds[:2]) >= bounds[2] or claims.get("tid") != tenant or claims.get("aud") != audience
            or claims.get("azp") != client_id or claims.get("idtyp") != "app" or "scp" in claims
            or not isinstance(claims.get("roles"), list) or "VisitPrep.Clarify" not in claims["roles"]
            or not all(isinstance(role, str) for role in claims["roles"])):
            raise ValueError
    except Exception:
        raise AuthError("Visit Prep workload unavailable") from None
