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
