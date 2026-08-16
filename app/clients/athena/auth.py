"""OAuth2 client-credentials ("2-legged") auth against athenahealth.

Token endpoint: {base}/oauth2/v1/token  (Okta-backed)
Access tokens last ~60 minutes; we cache and refresh 60s early.

Scopes:
  - FHIR reads (Certified APIs, auto-approved): system/Patient.read,
    system/DiagnosticReport.read, system/Observation.read, system/Binary.read
  - Proprietary athenaOne APIs: athena/service/Athenanet.MDP.*
Request only the scopes your Developer Console app was granted.
"""
from __future__ import annotations

import time

import requests

from .config import Settings

FHIR_READ_SCOPES = [
    "system/Patient.read",
    "system/DiagnosticReport.read",
    "system/Observation.read",
    "system/Binary.read",
]
PROPRIETARY_SCOPE = "athena/service/Athenanet.MDP.*"


class TokenProvider:
    def __init__(self, settings: Settings, scopes: list[str]):
        self._settings = settings
        self._scopes = scopes
        self._token: str | None = None
        self._expires_at: float = 0.0

    def get_token(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        resp = requests.post(
            self._settings.token_url,
            auth=(self._settings.client_id, self._settings.client_secret),
            data={"grant_type": "client_credentials", "scope": " ".join(self._scopes)},
            timeout=30,
        )
        if resp.status_code != 200:
            # Vendoring delta: never echo the response body — token endpoints
            # can return request context, and error strings reach logs.
            raise RuntimeError(f"athenahealth token request failed ({resp.status_code})")
        payload = resp.json()
        self._token = payload["access_token"]
        self._expires_at = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    def session(self) -> requests.Session:
        s = requests.Session()
        s.headers["Authorization"] = f"Bearer {self.get_token()}"
        return s

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.get_token()}"}
