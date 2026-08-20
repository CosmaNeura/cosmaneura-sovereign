"""Deterministic handling for high-consequence patient disclosures.

This is deliberately separate from the acute-emergency detector. Emergency
guidance always runs first; this module recognizes non-acute disclosures that
need a compassionate human response rather than a clinical workflow.
"""
from __future__ import annotations

import re


_DISCLOSURE_PATTERNS = (
    # Pregnancy loss and bereavement. Require first-person or close-relation
    # framing so educational questions and phrases such as "lost my keys" do
    # not enter sensitive mode.
    r"\b(?:(?:i|we)(?:'ve|\s+have)?|my\s+(?:wife|partner|spouse))\s+"
    r"(?:just\s+)?"
    r"(?:had|suffered|experienced)\s+(?:a\s+)?miscarriage\b",
    r"\b(?:i|we)(?:'ve|\s+have)?\s+(?:just\s+)?"
    r"(?:had|suffered|experienced)\s+(?:a\s+)?"
    r"pregnancy\s+loss\b",
    r"\b(?:(?:i|we)(?:'ve|\s+have)?|my\s+(?:wife|partner|spouse))\s+"
    r"(?:just\s+)?"
    r"(?:had|suffered|experienced)\s+(?:a\s+)?stillbirth\b",
    r"\b(?:my|our)\s+(?:baby|child|son|daughter)\s+(?:was|is)\s+stillborn\b",
    r"\b(?:i(?:'ve|\s+have)?|my\s+(?:wife|partner|spouse))\s+"
    r"(?:just\s+)?miscarried\b",
    r"\b(?:my|our)\s+(?:recent\s+)?miscarriage\b"
    r"(?!\s+(?:risk|risks|chance|chances|odds|question|questions)\b)",
    r"\b(?:i|we)(?:'ve|\s+have)?\s+(?:just\s+)?lost\s+(?:my|our|the)\s+"
    r"(?:pregnancy|baby|child|son|daughter|mother|father|mom|dad|husband|wife|partner|"
    r"spouse|brother|sister|friend)\b",
    r"\b(?:my|our)\s+(?:baby|child|son|daughter|mother|father|mom|dad|husband|"
    r"wife|partner|spouse|brother|sister|friend)\s+(?:has\s+)?(?:just\s+)?"
    r"(?:died|passed\s+away)\b",
    r"\b(?:i(?:'m|\s+am)|we(?:'re|\s+are))\s+grieving\s+(?:the\s+)?"
    r"(?:death|loss)\s+of\b",
    r"\bi(?:'m|\s+am)\s+bereaved\b",

    # A terminal diagnosis, framed as the speaker's lived disclosure rather
    # than a general question containing the word "terminal".
    r"\b(?:i\s+(?:just\s+)?(?:was|got)|i(?:'ve|\s+have)\s+"
    r"(?:just\s+)?been)\s+(?:just\s+)?diagnosed\s+with\s+"
    r"(?:a\s+)?terminal\s+(?:diagnosis|condition|illness|disease|cancer)\b",
    r"\bi(?:'ve|\s+have)?\s+(?:just\s+)?(?:got|gotten|received)\s+"
    r"(?:a\s+)?terminal\s+diagnosis\b",
    r"\bi\s+have\s+(?:a\s+)?terminal\s+diagnosis\b",
    r"\bi(?:'m|\s+am)\s+terminally\s+(?:ill|sick)\b",
    r"\bi\s+have\s+(?:a\s+)?terminal\s+"
    r"(?:condition|illness|disease|cancer)\b",
    r"\bi\s+(?:was|have\s+been)\s+(?:just\s+)?told\s+(?:that\s+)?"
    r"i\s+have\s+(?:a\s+)?terminal\s+(?:condition|illness|disease|cancer)\b",
    r"\bmy\s+(?:diagnosis|condition|illness|disease|cancer)\s+"
    r"(?:is|'s|was)\s+terminal\b",
    r"\b(?:my\s+doctor|the\s+doctor|they)\s+(?:just\s+)?"
    r"(?:told\s+me|said)\s+(?:that\s+)?(?:it|this|my\s+\w+)\s+"
    r"(?:is|'s|was)\s+terminal\b",

    # Hospitalization requires admission/inpatient wording; a routine visit or
    # a question about hospitals is not enough.
    r"\b(?:i\s+(?:was|am|got)|i'm|i(?:'ve|\s+have)\s+(?:just\s+)?been|"
    r"(?:my|our)\s+(?:wife|partner|spouse|child|son|daughter|mother|father|mom|dad|"
    r"husband|brother|sister)\s+(?:was|is|has\s+been))\s+"
    r"(?:(?:just|recently|currently)\s+)?hospitali[sz]ed\b",
    r"\b(?:i\s+(?:was|am)|i'm|i\s+(?:just\s+)?got|"
    r"i(?:'ve|\s+have)\s+(?:just\s+)?been|"
    r"(?:my|our)\s+(?:wife|partner|spouse|child|son|daughter|mother|father|mom|dad|"
    r"husband|brother|sister)\s+(?:was|is|has\s+been))\s+"
    r"(?:(?:just|recently|currently)\s+)?admitted\s+to\s+(?:the\s+)?hospital\b",
    r"\bi(?:'m|\s+am)\s+(?:currently\s+)?in\s+(?:the\s+)?hospital\b"
    r"(?:\s+(?:after|because)\b|\s+for\s+"
    r"(?![^.!?,;:]{0,48}\b(?:appointment|visit|check-?up|tests?|testing|work|"
    r"meeting|tour|volunteering|lunch|coffee|directions|parking)\b)\S|"
    r"[,;:]\s+(?!(?:and\s+)?(?:visiting|to\s+visit|working|at\s+work|"
    r"here\s+for|waiting\s+for|volunteering)\b)|"
    r"\s+(?:right\s+now|today|tonight)"
    r"(?:[.!?]\s*)?$|[.!?]\s*$|$)",
    r"\b(?:my|our)\s+(?:wife|partner|spouse|child|son|daughter|mother|father|"
    r"mom|dad|husband|brother|sister)\s+(?:is|was)\s+(?:currently\s+)?"
    r"in\s+(?:the\s+)?hospital\b"
    r"(?:\s+(?:after|because)\b|\s+for\s+"
    r"(?![^.!?,;:]{0,48}\b(?:appointment|visit|check-?up|tests?|testing|work|"
    r"meeting|tour|volunteering|lunch|coffee|directions|parking)\b)\S|"
    r"[,;:]\s+(?!(?:and\s+)?(?:visiting|to\s+visit|working|at\s+work|"
    r"here\s+for|waiting\s+for|volunteering)\b)|"
    r"\s+(?:right\s+now|today|tonight)"
    r"(?:[.!?]\s*)?$|[.!?]\s*$|$)",
    r"\bi\s+(?:just\s+)?(?:got|came)\s+(?:out|home)\s+from\s+"
    r"(?:the\s+)?hospital\b",
)

_DISCLOSURE_RE = re.compile("|".join(_DISCLOSURE_PATTERNS), re.IGNORECASE)

SENSITIVE_DISCLOSURE_RESPONSE = (
    "I'm so sorry you're going through this. You don't have to carry it alone. "
    "Would you like to contact someone you trust, your clinician, or a chaplain "
    "or pastoral care person? I can help you decide whom to reach out to."
)

# Governed, deterministic presentation copy. These values are intentionally
# static: no model, persistence result, or case detail may influence the
# safety fallback or its action descriptor.
SENSITIVE_HUMAN_CONTACT_ACTION = {
    "label": "Contact someone you trust",
    "action_id": "human-contact",
    "fallback_text": (
        "Please contact someone you trust, your clinician, or a chaplain "
        "or pastoral care person."
    ),
}

SENSITIVE_SAFETY_FALLBACK_RESPONSE = (
    "I'm so sorry you're going through this. You don't have to carry it alone. "
    "Please reach out to someone you trust, your clinician, or a chaplain or "
    "pastoral care person."
)


def detect_sensitive_disclosure(text: str) -> bool:
    """Return whether ``text`` contains a supported personal disclosure."""
    return bool(_DISCLOSURE_RE.search(text))
