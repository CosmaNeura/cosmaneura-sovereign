"""Request-local closure evidence; never authority or shared mutable turn state."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field

from app.clients.platform_audit import AuditTurn
from app.clients.platform_conversation import ConversationReceipt, PlatformTurn
from app.config import ComplianceProfile

PROVIDER_WORK_SECONDS = 165.0
PROVIDER_TOTAL_SECONDS = 180.0


@dataclass
class ProviderTurnClosure:
    trace_id: str = ""
    profile: ComplianceProfile | None = None
    turn: PlatformTurn | None = field(default=None, repr=False)
    user_receipt: ConversationReceipt | None = None
    audit_turn: AuditTurn | None = field(default=None, repr=False)
    history_context_ref: str | None = None
    provider_context_ref: str | None = None
    dependency_pending: bool = False
    final_arguments: dict[str, object] | None = field(default=None, repr=False)
    final_committed: bool = False
    work_deadline: float | None = None


ACTIVE_PROVIDER_TURN: ContextVar[ProviderTurnClosure | None] = ContextVar(
    "bounded_provider_turn", default=None,
)
