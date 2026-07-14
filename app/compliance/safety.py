"""
Medical-safety gate.

This runs independently of the doctrinal gate. Its job is patient safety, not
orthodoxy:
  * Detect emergencies and short-circuit to "seek emergency care now" guidance
    instead of attempting to help.
  * Enforce that the system never presents itself as a diagnostic tool.
  * Append a persona-appropriate disclaimer.

Keep the emergency list conservative and easy for clinicians to review/extend.
"""
from __future__ import annotations

import re

from app.config import Persona

_EMERGENCY_PATTERNS = [
    r"\bchest pain\b", r"\bcan'?t breathe\b", r"\btrouble breathing\b",
    r"\bsuicid", r"\bkill myself\b", r"\boverdose\b", r"\bstroke\b",
    r"\bnumbness on one side\b", r"\bseizure\b", r"\bunconscious\b",
    r"\bsevere bleeding\b", r"\banaphylaxis\b", r"\bworst headache\b",
]
_EMERGENCY_RE = re.compile("|".join(_EMERGENCY_PATTERNS), re.IGNORECASE)

EMERGENCY_MESSAGE = (
    "This may be a medical emergency. Please call 911 (or your local emergency "
    "number) or go to the nearest emergency department now. If you are thinking "
    "about harming yourself, in the U.S. you can call or text 988 to reach the "
    "Suicide & Crisis Lifeline. I can't help with emergencies, but a person on "
    "those lines can right away."
)

PATIENT_DISCLAIMER = (
    "This information is educational and is not medical advice, diagnosis, or "
    "treatment. Please discuss any decisions with a licensed clinician."
)
PROVIDER_DISCLAIMER = (
    "Educational support for clinical conversation only. Not a diagnostic device "
    "and not a substitute for professional judgment."
)


def detect_emergency(text: str) -> bool:
    return bool(_EMERGENCY_RE.search(text))


def disclaimer_for(persona: Persona) -> str:
    return PROVIDER_DISCLAIMER if persona == Persona.PROVIDER else PATIENT_DISCLAIMER
