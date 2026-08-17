"""OAuth2 client-credentials ("2-legged") auth against athenahealth.

Token endpoint: {base}/oauth2/v1/token  (Okta-backed)
Access tokens last ~60 minutes; we cache and refresh 60s early.

The governed labs client requests only DiagnosticReport.read and
Observation.read. Patient search, documents, and proprietary APIs are outside
the ``labs.read`` operation.
"""
from __future__ import annotations

import time

import requests

from .config import Settings

FHIR_LABS_READ_SCOPES = [
    "system/DiagnosticReport.read",
    "system/Observation.read",
]
# Compatibility alias for older imports. The value is deliberately the narrow
# labs-only scope set: patient search and report-document retrieval are not part
# of the governed ``labs.read`` operation.
FHIR_READ_SCOPES = FHIR_LABS_READ_SCOPES
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
