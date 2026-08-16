"""Shared base for the three specialist agents."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.clients.llm import LLMClient
from app.compliance.baa import require_baa_covered_llm
from app.config import ComplianceProfile, Settings, get_settings
from app.schemas import AgentResult, ChatRequest


class BaseAgent(ABC):
    name: str = "base"

    def __init__(self, settings: Optional[Settings] = None, llm: Optional[LLMClient] = None):
        self.s = settings or get_settings()
        self.llm = llm or LLMClient(self.s)

    # Output flexibility: the same synthesis can land as a chat reply, as text
    # ready to paste into a note/EHR field, or as a standalone document. The
    # calling UI selects via metadata.output_format; unknown values fall back
    # to chat. (Direct user need: one clinician copy-pastes every note into
    # Athena by hand — "note" output exists to make that paste clean.)
    _FORMAT_RULES = {
        "chat": "Write for a chat panel: conversational and direct.",
        "note": ("Write clean clinical text ready to paste into a note or EHR "
                 "field: no greeting, no questions back, no markdown symbols; "
                 "short labeled sections and plain sentences."),
        "document": ("Write a standalone document in markdown: a title line, "
                     "clear section headings, complete prose — suitable to "
                     "save, print, or hand to the patient."),
    }

    def resolve_output_format(self, request: ChatRequest) -> tuple[str, str]:
        """Return (format_name, formatting instruction) for this request."""
        fmt = str(request.metadata.get("output_format") or "chat").lower()
        if fmt not in self._FORMAT_RULES:
            fmt = "chat"
        return fmt, self._FORMAT_RULES[fmt]

    def require_baa_covered_llm(self, profile: ComplianceProfile) -> None:
        """PHI tripwire — see app/compliance/baa.py (shared with /digest)."""
        require_baa_covered_llm(self.s, profile)

    @abstractmethod
    async def run(self, request: ChatRequest, profile: ComplianceProfile) -> AgentResult:
        ...
