"""Request/response contracts for the Sovereign pipeline and HTTP API."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.config import Persona


class Intent(str, Enum):
    """What the Sovereign decides a request needs."""
    REFERRAL = "referral"            # -> Agent 1
    CONCIERGE = "concierge"          # -> Agent 2
    ENGAGEMENT = "engagement"        # -> Agent 3
    ETHICS_CONSULT = "ethics_consult"  # -> Agent 4 (always fully arbitrated)
    VISIT_PREP = "visit_prep"        # -> Agent 5 (clinician session prep)
    PLAN_WORKBENCH = "plan_workbench"  # -> Agent 6 (post-visit plan deep-dive)
    LAB_REVIEW = "lab_review"        # -> Agent 7 (EMR lab results)
    DOCTRINAL = "doctrinal"          # answered directly by Magisterium
    SMALL_TALK = "small_talk"        # handled by the Sovereign, no agent
    EMERGENCY = "emergency"          # short-circuit to safety guidance


class DoctrinalSensitivity(str, Enum):
    """How hard the doctrinal gate should work (tiered to save latency/cost)."""
    NONE = "none"        # pure logistics; lightweight conformity check only
    LOW = "low"
    HIGH = "high"        # touches faith/morals/medical ethics -> full arbitration


class ChatMessage(BaseModel):
    role: str            # "user" | "assistant"
    content: str = Field(max_length=10_000)


class ChatRequest(BaseModel):
    persona: Persona
    message: str = Field(min_length=1, max_length=10_000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=50)
    # Free-form, persona-appropriate context. For PATIENT this is volunteered
    # info; for PROVIDER it may contain PHI and is treated accordingly.
    # Bounded to keep request size (and downstream token spend) in check.
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=50)
    session_id: Optional[str] = Field(
        default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$",
    )


class Citation(BaseModel):
    source: str
    title: Optional[str] = None
    url: Optional[str] = None


class AgentResult(BaseModel):
    agent: str
    content: str
    citations: list[Citation] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)  # structured payload (e.g. matches)


class DoctrinalVerdict(BaseModel):
    conforms: bool
    rationale: str
    revised_content: Optional[str] = None
    citations: list[Citation] = Field(default_factory=list)


class ChatResponse(BaseModel):
    answer: str
    intent: Intent
    persona: Persona
    citations: list[Citation] = Field(default_factory=list)
    doctrinal_review: Optional[DoctrinalVerdict] = None
    structured: dict[str, Any] = Field(default_factory=dict)
    safety_notice: Optional[str] = None
    trace_id: Optional[str] = None


class DigestRequest(BaseModel):
    """
    Journey-digest maintenance (POST /digest): merge one or more session
    notes/transcripts into a compact rolling per-patient digest. Called by the
    host app once per session at WRITE time (when the note lands), not on the
    chat hot path — so multi-visit context arrives at /chat as one small
    field (metadata.journey_digest) instead of N transcripts.

    PHI: sessions may carry PHI; this runs on the BAA path only. The overall
    request is additionally bounded by the HTTP body cap in main.py.
    """
    persona: Persona
    sessions: list[str] = Field(min_length=1, max_length=12)
    existing_digest: str = Field(default="", max_length=8_000)
    terminology: str = Field(default="patient/clinician", max_length=40)


class DigestResponse(BaseModel):
    digest: str
    trace_id: str
