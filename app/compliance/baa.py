"""
Shared BAA-attestation tripwire.

One function, used by every PHI-bearing path (agents 3/4/5/6 and the journey
digest endpoint): in a real provider deployment, refuse to process PHI unless
the inference path is attested as BAA-covered. Merely pointing at a
BAA-capable provider is not enough — BAA_ATTESTED must be set only after the
agreement (with ZDR, or the hyperscaler equivalent) is actually executed.
"""
from __future__ import annotations

from app.config import ComplianceProfile, Settings


def require_baa_covered_llm(settings: Settings, profile: ComplianceProfile) -> None:
    if profile.require_baa_covered_llm and settings.mock_mode is False \
            and (settings.llm_provider not in ("anthropic", "azure_openai")
                 or not settings.baa_attested):
        raise RuntimeError(
            "Provider build requires an attested BAA-covered LLM path "
            "(set BAA_ATTESTED=true only after the BAA + ZDR are executed)."
        )
