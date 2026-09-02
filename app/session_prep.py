"""Direct, browser-minimized projection of Core-owned Session Prep results."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.clients.session_prep_contract import (
    SessionPrepContextReadResult,
    SessionPrepPendingResult,
    SessionPrepReadyResult,
    SessionPrepRefreshingResult,
)
from app.schemas import (
    SessionPrepProjectedBrief,
    SessionPrepProjectedPatientReport,
    SessionPrepResponse,
)

MAX_SESSION_PREP_CANONICAL_CHARS = 19_000

@dataclass(frozen=True)
class SessionPrepOutcome:
    """A browser response plus server-only transport correlation."""

    response: SessionPrepResponse
    trace_id: str

_PROHIBITED_EGRESS = tuple(re.compile(pattern, re.I) for pattern in (
    r"\b(?:diagnosis\s+is|diagnos(?:e|ed)\s+(?:as|with)|(?:new|confirmed|definitive)\s+diagnosis|this\s+confirms?\s+(?:a\s+|the\s+)?diagnosis)\b",
    r"(?:^|[.!?]\s*)(?:please\s+)?(?:start|stop|increase|decrease)\b(?!\s+(?:with|by|the\s+(?:visit|session|discussion|review)))",
    r"\b(?:start|stop|increase|decrease)\b[^.!?\n]{0,80}\b(?:taking|using|dose|dosage|medication|medicine|drug|prescription|mg|mcg|tablet|capsule)s?\b",
    r"\b(?:avoid|delay|defer|postpone|skip|do\s+not\s+seek|don't\s+seek)\b[^.!?\n]{0,80}\b(?:urgent[- ]care|emergency\s+(?:care|department)|ER|911)\b",
))

def session_prep_egress_is_safe(result: SessionPrepContextReadResult) -> bool:
    """Independently reject prohibited clinical claims before projection."""
    if not isinstance(result, (SessionPrepReadyResult, SessionPrepRefreshingResult)):
        return True
    brief = result.prepared_brief
    text = [brief.recap.text]
    for section in (
        brief.trajectory_and_material_changes, brief.talking_points,
        brief.open_threads, brief.cautions, brief.suggested_outline,
    ):
        text.extend(item.text for item in section)
    if brief.patient_reported is not None:
        text.append(brief.patient_reported.summary)
    return not any(pattern.search(value) for value in text for pattern in _PROHIBITED_EGRESS)


def _blocked_projection(client_turn_id: uuid.UUID) -> SessionPrepResponse:
    return SessionPrepResponse(
        client_turn_id=client_turn_id, state="unavailable", freshness="none",
        current_through=None, source_count=0, prepared_brief=None,
        retry_after_seconds=None, reason_code="context_not_available",
    )


def project_session_prep_result(
    result: SessionPrepContextReadResult,
    *,
    client_turn_id: uuid.UUID,
) -> SessionPrepResponse:
    """Copy exact text while dropping server-only lineage and unsafe content."""
    if isinstance(result, (SessionPrepReadyResult, SessionPrepRefreshingResult)):
        if not session_prep_egress_is_safe(result):
            return _blocked_projection(client_turn_id)
        brief = result.prepared_brief
        projected = SessionPrepProjectedBrief(
            recap=brief.recap.text,
            trajectory_and_material_changes=[
                item.text for item in brief.trajectory_and_material_changes
            ],
            talking_points=[item.text for item in brief.talking_points],
            open_threads=[item.text for item in brief.open_threads],
            cautions=[item.text for item in brief.cautions],
            suggested_outline=[item.text for item in brief.suggested_outline],
            patient_reported=(
                SessionPrepProjectedPatientReport(
                    label=brief.patient_reported.label,
                    summary=brief.patient_reported.summary,
                )
                if brief.patient_reported is not None
                else None
            ),
        )
        response = SessionPrepResponse(
            client_turn_id=client_turn_id,
            state=result.state,
            freshness=result.freshness,
            current_through=result.current_through,
            source_count=result.source_count,
            prepared_brief=projected,
            retry_after_seconds=result.retry_after_seconds,
            reason_code=result.reason_code,
        )
        return (
            response if len(render_session_prep_text(response))
            <= MAX_SESSION_PREP_CANONICAL_CHARS
            else _blocked_projection(client_turn_id)
        )

    return SessionPrepResponse(
        client_turn_id=client_turn_id,
        state=result.state,
        freshness=result.freshness,
        current_through=None,
        source_count=0,
        prepared_brief=None,
        retry_after_seconds=(
            result.retry_after_seconds
            if isinstance(result, SessionPrepPendingResult)
            else None
        ),
        reason_code=result.reason_code,
    )


def render_session_prep_text(response: SessionPrepResponse) -> str:
    """Render the exact deterministic text stored in canonical history."""
    if response.state == "pending":
        return (
            "Session Prep is still being prepared. No clinical content is "
            "available yet."
        )
    if response.state == "unavailable":
        if response.reason_code == "no_eligible_sources":
            return "Session Prep is unavailable because no eligible finalized notes were found."
        return "Session Prep is unavailable for the current encounter."

    brief = response.prepared_brief
    if brief is None or response.current_through is None:
        raise ValueError("content-bearing Session Prep projection is incomplete")
    lines = ["Session Prep"]
    if response.state == "refreshing":
        lines.extend([
            "",
            "Refreshing — this still-authorized brief is stale while Core updates it.",
        ])
    count_label = "source" if response.source_count == 1 else "sources"
    lines.extend([
        "",
        f"Current through {response.current_through.isoformat()} · "
        f"{response.source_count} {count_label}",
        "",
        "Recap",
        brief.recap,
    ])
    for title, values in (
        ("Trajectory and material changes", brief.trajectory_and_material_changes),
        ("Talking points", brief.talking_points),
        ("Open threads", brief.open_threads),
        ("Cautions", brief.cautions),
        ("Suggested outline", brief.suggested_outline),
    ):
        if values:
            lines.extend(["", title, *[f"- {value}" for value in values]])
    if brief.patient_reported is not None:
        lines.extend([
            "",
            brief.patient_reported.label,
            brief.patient_reported.summary,
        ])
    return "\n".join(lines)
