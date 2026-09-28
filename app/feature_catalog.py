"""Visible guided paths shared by welcome and capability-aware introductions."""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.config import PROVIDER_BETA_CAPABILITIES, DeploymentMount, Settings
from app.schemas import Intent

if TYPE_CHECKING:
    from app.security.mount import MountContext


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
    # Rendered only when the legacy EMR lab sandbox is enabled.
    {"label": "Review labs (EMR)",
     "message": "Anything concerning in her labs? Show me the A1C trend.",
     "intent": "lab_review"},
]
_SESSION_PREP_PATH = {
    "label": "Open Session Prep",
    "message": "Open Session Prep",
    "intent": "session_prep",
}


def labs_sandbox_enabled(settings: Settings, mount: DeploymentMount | MountContext) -> bool:
    return (
        mount.surface == "provider_assistant"
        and not settings.platform_agent_api_enabled
        and settings.feature_athena_labs
        and settings.athena_env == "preview"
        and settings.athena_practice_id == "195900"
    )


def visible_paths(
    settings: Settings,
    mount: DeploymentMount | MountContext,
    intent_capabilities: Mapping[Intent, str],
    *,
    candidates: list[dict] | None = None,
) -> list[dict]:
    """Apply existing welcome gates to trusted current mount authority only."""
    if candidates is None:
        if mount.surface == "provider_assistant":
            candidates = (
                [_SESSION_PREP_PATH, *[p for p in _PROVIDER_PATHS if p["intent"] != "visit_prep"]]
                if settings.feature_session_prep else _PROVIDER_PATHS
            )
        else:
            candidates = _PATIENT_PATHS
    granted = set(mount.capabilities)
    if settings.platform_agent_api_enabled:
        granted.intersection_update(PROVIDER_BETA_CAPABILITIES)

    def required_capability(path: dict) -> str | None:
        if path.get("intent") == "session_prep":
            return "session-prep:read"
        try:
            return intent_capabilities.get(Intent(path["intent"]))
        except (KeyError, TypeError, ValueError):
            return None

    labs_sandbox = labs_sandbox_enabled(settings, mount)
    return [dict(path) for path in candidates
            if required_capability(path) in granted
            and (path["intent"] != "lab_review" or labs_sandbox)]
