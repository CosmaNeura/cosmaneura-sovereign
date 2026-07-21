"""Shared base for the three specialist agents."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.clients.llm import LLMClient
from app.config import ComplianceProfile, Settings, get_settings
from app.schemas import AgentResult, ChatRequest


class BaseAgent(ABC):
    name: str = "base"

    def __init__(self, settings: Optional[Settings] = None, llm: Optional[LLMClient] = None):
        self.s = settings or get_settings()
        self.llm = llm or LLMClient(self.s)

    def require_baa_covered_llm(self, profile: ComplianceProfile) -> None:
        """
        Tripwire for PHI-bearing agents: in a real provider deployment, refuse
        to process PHI unless the inference path is attested as BAA-covered.
        Merely pointing at a BAA-capable provider is not enough — BAA_ATTESTED
        must be set only after the agreement (with ZDR, or the hyperscaler
        equivalent) is actually executed.
        """
        if profile.require_baa_covered_llm and self.s.mock_mode is False \
                and (self.s.llm_provider not in ("anthropic", "azure_openai")
                     or not self.s.baa_attested):
            raise RuntimeError(
                "Provider build requires an attested BAA-covered LLM path "
                "(set BAA_ATTESTED=true only after the BAA + ZDR are executed)."
            )

    @abstractmethod
    async def run(self, request: ChatRequest, profile: ComplianceProfile) -> AgentResult:
        ...
