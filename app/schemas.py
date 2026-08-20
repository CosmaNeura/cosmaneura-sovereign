"""Request/response contracts for the Sovereign pipeline and HTTP API."""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import (BaseModel, BeforeValidator, ConfigDict, Field,
                      field_validator, model_validator)

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
    SENSITIVE_DISCLOSURE = "sensitive_disclosure"  # static compassionate response
    EMERGENCY = "emergency"          # short-circuit to safety guidance


class DoctrinalSensitivity(str, Enum):
    """How hard the doctrinal gate should work (tiered to save latency/cost)."""
    NONE = "none"        # pure logistics; lightweight conformity check only
    LOW = "low"
    HIGH = "high"        # touches faith/morals/medical ethics -> full arbitration


class ClosedWireModel(BaseModel):
    """Base for v1 embed payloads: unknown fields always fail closed."""

    model_config = ConfigDict(extra="forbid")


class ChatMessage(ClosedWireModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=10_000)


def _canonical_client_turn_id(value: object) -> object:
    """Accept only the canonical lowercase RFC 4122 UUIDv4 wire form."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        if value.version != 4 or value.variant != uuid.RFC_4122:
            raise ValueError("invalid client turn identifier")
        return value
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("invalid client turn identifier")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise ValueError("invalid client turn identifier") from None
    if (
        parsed.version != 4
        or parsed.variant != uuid.RFC_4122
        or str(parsed) != value
    ):
        raise ValueError("invalid client turn identifier")
    return parsed


CanonicalClientTurnId = Annotated[
    uuid.UUID,
    BeforeValidator(_canonical_client_turn_id),
]


class ChatRequest(ClosedWireModel):
    # Required by the ratified embed contract when the platform agent API is
    # enabled. It remains optional only for the pinned legacy migration path
    # and for the pre-context deterministic emergency short-circuit.
    client_turn_id: Optional[CanonicalClientTurnId] = None
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


class HostContextHints(ClosedWireModel):
    """Initial-v1 browser hints; raw clinical migration bridges are disabled."""

    clientId: Optional[str] = Field(default=None, min_length=1, max_length=128)
    thread_ref: Optional[str] = Field(
        default=None, pattern=r"^[A-Za-z0-9._~-]{8,160}$", max_length=160,
    )
    context_snapshot_ref: Optional[str] = Field(
        default=None, pattern=r"^[A-Za-z0-9._~-]{8,160}$", max_length=160,
    )
    digest_ref: Optional[str] = Field(
        default=None, pattern=r"^[A-Za-z0-9._~-]{8,160}$", max_length=160,
    )
    output_format: Optional[Literal["chat", "note", "document"]] = None
    terminology: Optional[str] = Field(default=None, max_length=40)
    care_model: Optional[str] = None


class EmbedChatRequest(ClosedWireModel):
    """Ratified v1 HTTP request with all migration-only raw ingress off."""

    client_turn_id: CanonicalClientTurnId
    persona: Optional[Persona] = None
    message: str = Field(min_length=1, max_length=10_000)
    metadata: HostContextHints = Field(default_factory=HostContextHints)
    session_id: Optional[str] = Field(
        default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$",
    )


class Citation(ClosedWireModel):
    source: str
    title: Optional[str] = None
    url: Optional[str] = None


class AgentResult(BaseModel):
    agent: str
    content: str
    citations: list[Citation] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)  # structured payload (e.g. matches)


class DoctrinalVerdict(ClosedWireModel):
    conforms: bool
    rationale: str
    revised_content: Optional[str] = None
    citations: list[Citation] = Field(default_factory=list)


SuppressionName = Literal[
    "disclaimer",
    "intent_badge",
    "doctrinal_badge",
    "citations",
    "structured_cards",
    "feature_actions",
    "guided_paths",
]
ALL_PRESENTATION_SUPPRESSIONS: tuple[SuppressionName, ...] = (
    "disclaimer",
    "intent_badge",
    "doctrinal_badge",
    "citations",
    "structured_cards",
    "feature_actions",
    "guided_paths",
)


class HumanContactAction(ClosedWireModel):
    label: str = Field(min_length=1, max_length=120)
    action_id: str = Field(pattern=r"^[A-Za-z0-9._~-]{8,128}$")
    fallback_text: str = Field(min_length=1, max_length=240)


class StandardPresentation(ClosedWireModel):
    mode: Literal["standard"]
    suppress: list[SuppressionName] = Field(
        json_schema_extra={"uniqueItems": True},
    )
    human_contact: Optional[HumanContactAction]

    @field_validator("suppress")
    @classmethod
    def _unique_suppressions(
        cls, value: list[SuppressionName],
    ) -> list[SuppressionName]:
        if len(value) != len(set(value)):
            raise ValueError("presentation suppressions must be unique")
        return value


class EmergencyPresentation(ClosedWireModel):
    mode: Literal["emergency"]
    suppress: list[SuppressionName] = Field(
        json_schema_extra={"uniqueItems": True},
    )
    human_contact: Optional[HumanContactAction]

    @field_validator("suppress")
    @classmethod
    def _unique_suppressions(
        cls, value: list[SuppressionName],
    ) -> list[SuppressionName]:
        if len(value) != len(set(value)):
            raise ValueError("presentation suppressions must be unique")
        return value


class SensitiveDisclosurePresentation(ClosedWireModel):
    mode: Literal["sensitive_disclosure"]
    suppress: list[SuppressionName] = Field(
        min_length=7,
        max_length=7,
        json_schema_extra={"uniqueItems": True},
    )
    human_contact: HumanContactAction

    @field_validator("suppress")
    @classmethod
    def _all_suppressions(
        cls, value: list[SuppressionName],
    ) -> list[SuppressionName]:
        if len(value) != 7 or set(value) != set(ALL_PRESENTATION_SUPPRESSIONS):
            raise ValueError("sensitive presentation requires every suppression")
        return value


ChatPresentation = Annotated[
    Union[
        StandardPresentation,
        EmergencyPresentation,
        SensitiveDisclosurePresentation,
    ],
    Field(discriminator="mode"),
]


class ChatResponse(ClosedWireModel):
    answer: str
    intent: Intent
    client_turn_id: Optional[uuid.UUID] = None
    persona: Optional[Persona] = None
    citations: list[Citation]
    doctrinal_review: Optional[DoctrinalVerdict]
    structured: dict[str, Any]
    safety_notice: Optional[str]
    trace_id: Optional[str] = None
    presentation: ChatPresentation

    @model_validator(mode="after")
    def _sensitive_payload_is_minimized(self) -> "ChatResponse":
        if isinstance(self.presentation, SensitiveDisclosurePresentation):
            if (self.citations or self.doctrinal_review is not None
                    or self.structured or self.safety_notice is not None):
                raise ValueError("sensitive response contains suppressed fields")
        return self


class EmbedChatResponse(ChatResponse):
    """Successful v1 HTTP response; correlation is never optional on wire."""

    client_turn_id: uuid.UUID


class CompassionateSafetyFallback(ClosedWireModel):
    failure_stage: Literal["user_accepted", "final_response"]
    answer: str = Field(min_length=1, max_length=1_000)
    presentation: SensitiveDisclosurePresentation


class SovereignProblemDetails(ClosedWireModel):
    """Closed, value-free RFC 9457 embed error boundary."""

    type: str
    title: str = Field(max_length=160)
    status: int = Field(ge=400, le=599)
    code: Literal[
        "CONTEXT_REQUIRED",
        "CONTEXT_INVALID",
        "CONTEXT_EXPIRED",
        "CAPABILITY_DENIED",
        "RECORD_CONTEXT_MISMATCH",
        "EMBED_VERSION_UNSUPPORTED",
        "VALIDATION_FAILED",
        "RATE_LIMITED",
        "SERVICE_UNAVAILABLE",
        "INTERNAL_ERROR",
    ]
    detail: str = Field(max_length=500)
    trace_id: str
    retryable: bool
    retry_after_seconds: Optional[int] = Field(default=None, ge=1)
    safety_fallback: Optional[CompassionateSafetyFallback] = None

    @model_validator(mode="after")
    def _fallback_is_only_nonretryable_unavailable(self) -> "SovereignProblemDetails":
        if self.safety_fallback is not None and (
            self.status != 503
            or self.code != "SERVICE_UNAVAILABLE"
            or self.retryable
        ):
            raise ValueError("invalid safety fallback problem")
        return self


class DigestRequest(ClosedWireModel):
    """
    Journey-digest maintenance (POST /digest): merge one or more session
    notes/transcripts into a compact rolling per-patient digest. Called by the
    host app once per session at WRITE time (when the note lands), not on the
    chat hot path — so multi-visit context arrives at /chat as one small
    field (metadata.journey_digest) instead of N transcripts.

    PHI: sessions may carry PHI; this runs on the BAA path only. The overall
    request is additionally bounded by the HTTP body cap in main.py.
    """
    client_turn_id: Optional[CanonicalClientTurnId] = None
    persona: Persona
    sessions: list[str] = Field(min_length=1, max_length=12)
    existing_digest: str = Field(default="", max_length=8_000)
    terminology: str = Field(default="patient/clinician", max_length=40)


class DigestResponse(ClosedWireModel):
    client_turn_id: Optional[uuid.UUID] = None
    digest: str
    trace_id: str
