"""
PHI firewall and de-identification.

Role in the architecture: the Sovereign must never let Protected Health
Information reach a component that is not a HIPAA business associate. In this
system the non-covered components are Magisterium, ClinicalTrials.gov, NPPES,
and any web search. Before the orchestrator hands text to any of those, it runs
it through `deidentify()`.

De-identification has two modes (config: DEID_PROVIDER):
  * "regex"  — the built-in baseline below. Catches obvious direct identifiers
    (email, SSN, phone, dates, ...) but NOT free-text names or indirect PHI.
    Fine for a demo / patient (non-PHI) build.
  * "azure"  — Azure Health Data Services de-identification (HIPAA-grade; the
    same service the main CosmaNeura app uses). Catches names, locations, etc.
    Required before real PHI can pass toward any non-BAA component.

The Azure path FAILS CLOSED: if the service is unavailable it raises rather than
silently degrading to the weaker regex, because a de-id gate that quietly weakens
under load is not a gate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.config import Settings, get_settings

# Hard cap on text handed to the scrubber. The de-identifier runs on every
# untrusted input, so it must not be a CPU amplification target. Anything longer
# is truncated before regex evaluation (and the caller-level request limits in
# schemas.py should prevent this being hit in normal operation).
_MAX_SCRUB_CHARS = 50_000

# Order matters: more specific patterns first.
# NB: patterns are written to avoid catastrophic backtracking (no nested
# unbounded quantifiers over overlapping character classes).
_PATTERNS = [
    ("EMAIL", re.compile(r"\b[\w.+-]{1,64}@[\w-]{1,63}\.[\w.-]{1,63}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # Phone: fixed-shape North American formats. Avoids the previous nested
    # optional groups that could backtrack on adversarial input.
    ("PHONE", re.compile(
        r"\b(?:\+1[-.\s])?(?:\(\d{3}\)\s?|\d{3}[-.\s])\d{3}[-.\s]\d{4}\b")),
    ("MRN", re.compile(r"\b(?:MRN|mrn|medical record(?: number)?)[:#\s]{0,3}\w{1,32}\b")),
    ("DATE", re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")),
    ("ZIP", re.compile(r"\b\d{5}(?:-\d{4})?\b")),
    ("URL", re.compile(r"\bhttps?://\S{1,2048}\b")),
]


@dataclass
class DeidResult:
    text: str
    redaction_count: int
    redacted_types: list[str]

    @property
    def found_phi(self) -> bool:
        return self.redaction_count > 0


def _deidentify_regex(text: str) -> DeidResult:
    """Baseline: replace obvious direct identifiers with [REDACTED:TYPE] tokens."""
    redacted_types: list[str] = []
    count = 0
    # Bound the work: truncate pathologically long input before regex eval.
    out = text if len(text) <= _MAX_SCRUB_CHARS else text[:_MAX_SCRUB_CHARS]
    for label, pattern in _PATTERNS:
        def _repl(_m, _label=label):
            nonlocal count
            count += 1
            if _label not in redacted_types:
                redacted_types.append(_label)
            return f"[REDACTED:{_label}]"
        out = pattern.sub(_repl, out)
    return DeidResult(text=out, redaction_count=count, redacted_types=redacted_types)


# Cached client — building the credential + client per call is wasteful and
# refetches tokens. Keyed only by the (single) settings object in practice.
_azure_client = None


def _azure_deid_client(s: Settings):
    global _azure_client
    if _azure_client is None:
        from azure.health.deidentification import DeidentificationClient
        if s.azure_deid_client_secret:
            from azure.identity import ClientSecretCredential
            cred = ClientSecretCredential(
                tenant_id=s.azure_deid_tenant_id, client_id=s.azure_deid_client_id,
                client_secret=s.azure_deid_client_secret,
            )
        else:
            # Uses `az login` locally and the managed identity in Azure.
            from azure.identity import DefaultAzureCredential
            cred = DefaultAzureCredential()
        _azure_client = DeidentificationClient(
            s.azure_deid_endpoint, cred, api_version=s.azure_deid_api_version)
    return _azure_client


def _deidentify_azure(text: str, s: Settings) -> DeidResult:
    from azure.health.deidentification.models import DeidentificationContent
    client = _azure_deid_client(s)
    # "Redact" replaces each detected entity with its [CATEGORY], so the output
    # carries no PHI-looking values (unlike "Surrogate").
    result = client.deidentify_text(
        DeidentificationContent(input_text=text, operation_type="Redact"))
    out = result.output_text or text
    tags = re.findall(r"\[([A-Za-z]+)\]", out)
    types = sorted(set(tags))
    count = len(tags) if tags else (1 if out != text else 0)
    return DeidResult(text=out, redaction_count=count, redacted_types=types)


def deidentify(text: str, settings: Optional[Settings] = None) -> DeidResult:
    """De-identify text using the configured provider (Azure clinical or regex)."""
    s = settings or get_settings()
    capped = text if len(text) <= _MAX_SCRUB_CHARS else text[:_MAX_SCRUB_CHARS]
    if s.deid_provider == "azure" and not s.mock_mode and s.azure_deid_endpoint:
        try:
            return _deidentify_azure(capped, s)
        except PHIBoundaryViolation:
            raise
        except Exception:
            # FAIL CLOSED — never fall back to the weaker regex for real PHI.
            # (Message carries no PHI.)
            raise PHIBoundaryViolation(
                "clinical de-identification service is unavailable; refusing to "
                "proceed rather than risk under-redacting PHI."
            ) from None
    return _deidentify_regex(capped)


def assert_no_phi(text: str) -> None:
    """
    Fast last-line tripwire used by non-BAA clients. Intentionally uses the
    REGEX detector (not the network de-id service): it is a cheap check for
    OBVIOUS identifiers on text the orchestrator has already de-identified, run
    on every outbound call. Comprehensive de-id is `deidentify()`'s job upstream.
    """
    res = _deidentify_regex(text)
    if res.found_phi:
        raise PHIBoundaryViolation(
            f"Refusing to transmit text containing likely identifiers "
            f"({', '.join(res.redacted_types)}) to a non-BAA component."
        )


class PHIBoundaryViolation(RuntimeError):
    pass
