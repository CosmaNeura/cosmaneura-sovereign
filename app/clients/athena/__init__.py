"""
Vendored athenahealth lab client (from the integration bundle, 2026-08-14).

Sandbox-verified against athenahealth Preview (practice 195900). Kept close to
the delivered code so upstream fixes stay easy to apply; the deltas are:

  * `config.py` — dropped the import-time `load_dotenv()` side effect. This
    service already reads `.env` through pydantic-settings, and a second
    config system would let athena credentials diverge from every other
    secret. `Settings` is built by `app/clients/labs.py` from the app's own
    settings object; `load_settings()` is left for standalone/CLI use.
  * `auth.py` / `fhir_source.py` — errors no longer embed response bodies.
    A FHIR error body can contain patient data, and this repo already fixed
    "PHI leakage through error paths" once; re-introducing it through a
    vendored file would be a regression.
  * NOT vendored: `proprietary_source.py` (athenaOne proprietary API — no
    contract, and the brief forbids it in any production path; leaving it out
    means it cannot be reached by accident), plus the demo/seed/chat-loop
    reference scripts.

Everything here is SYNCHRONOUS (`requests`). It must only be called through
`app/clients/labs.py`, which runs it off the event loop.
"""
from __future__ import annotations

from .adapter import LabSource
from .auth import FHIR_READ_SCOPES, TokenProvider
from .config import Settings
from .fhir_source import AthenaFhirLabSource
from .models import LabReport, LabValue

__all__ = ["LabSource", "TokenProvider", "FHIR_READ_SCOPES", "Settings",
           "AthenaFhirLabSource", "LabReport", "LabValue"]
