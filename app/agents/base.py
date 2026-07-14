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

    @abstractmethod
    async def run(self, request: ChatRequest, profile: ComplianceProfile) -> AgentResult:
        ...
