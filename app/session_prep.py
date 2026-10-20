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
from app.clients.session_prep_facts_contract import SessionPrepFactsResult
from app.clients.provider_evidence import _literal_markdown
from app.schemas import (
    SessionPrepFactsResponse, SessionPrepProjectedFacts, SessionPrepProjectedFact,
    SessionPrepProjectedCorrection,
    SessionPrepProjectedBrief,
    SessionPrepProjectedPatientReport,
    SessionPrepResponse,
)

MAX_SESSION_PREP_CANONICAL_CHARS = 19_000
FACT_LABELS = {
    "planned": "Planned", "documented_taking": "Taking documented in note",
    "documented_continued": "Continued in note", "documented_started": "Started in note",
    "documented_prescribed": "Prescription documented", "documented_listed": "Listed; use not confirmed",
    "documented_not_taking": "Not taking documented", "stopped": "Stopped in note",
    "superseded": "Superseded in note", "ordered": "Ordered", "resulted": "Result documented",
    "cancelled": "Cancelled", "not_ordered": "Not ordered", "order_not_documented": "Order not documented",
    "result_not_documented": "Result not documented", "measured": "Measurement documented",
}

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


def project_session_prep_facts(
    result: SessionPrepFactsResult | SessionPrepContextReadResult, *, client_turn_id: uuid.UUID,
) -> SessionPrepFactsResponse:
    """Project exact observations without exposing any authority/source handles."""
    preparation = result.preparation if isinstance(result, SessionPrepFactsResult) else result
    original = project_session_prep_result(preparation, client_turn_id=client_turn_id)
    source = result.structured_facts if isinstance(result, SessionPrepFactsResult) else None
    facts = None
    if source is not None:
        corrections = {}
        for fact in source.facts:
            for prior in fact.supersedes:
                if prior not in corrections or (corrections[prior].source_at, corrections[prior].start) < (fact.evidence.source_at, fact.evidence.start):
                    corrections[prior] = fact.correction_evidence
        facts = SessionPrepProjectedFacts(
            coverage=source.coverage, source_dates=[item.source_at for item in source.sources],
            selection_limited=source.selection_limited, facts_limited=source.facts_limited,
            facts=[SessionPrepProjectedFact(
                kind=item.kind, subject=item.subject, value=item.value, state=item.state,
                setting=item.setting, note_date=item.evidence.source_at, section=item.evidence.section,
                quote=item.evidence.quote, correction=(SessionPrepProjectedCorrection(
                    note_date=corrections[item.fact_ref].source_at,
                    section=corrections[item.fact_ref].section,
                    quote=corrections[item.fact_ref].quote,
                ) if item.fact_ref in corrections else None),
            ) for item in source.facts],
        )
    response = SessionPrepFactsResponse(
        **original.model_dump(),
        facts_state=result.facts_state if isinstance(result, SessionPrepFactsResult) else "unsupported",
        structured_facts=facts,
    )
    # Never show clinical rows which cannot also fit canonical persistence.
    # Prefer newer observations; the limitation is explicitly displayed.
    while len(render_session_prep_text(response)) > MAX_SESSION_PREP_CANONICAL_CHARS and facts and facts.facts:
        facts.facts.pop(0)
        facts.facts_limited = True
    if len(render_session_prep_text(response)) > MAX_SESSION_PREP_CANONICAL_CHARS:
        response = SessionPrepFactsResponse(
            **_blocked_projection(client_turn_id).model_dump(), facts_state=response.facts_state,
            structured_facts=facts,
        )
    return response


def _render_preparation_text(response: SessionPrepResponse) -> str:
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


def render_session_prep_text(response: SessionPrepResponse) -> str:
    """Persist the deterministic visible brief and exact quoted observations."""
    text = _render_preparation_text(response)
    if not isinstance(response, SessionPrepFactsResponse):
        return text
    facts = response.structured_facts
    if facts is None:
        return text + ("\n\nDated tables aren’t available here yet." if response.facts_state == "unsupported" else "")
    if response.state == "pending":
        text = "The prepared brief is still being generated. Reviewed-source observations are available below."
    elif response.state == "unavailable":
        text = "The prepared brief is unavailable. Reviewed-source observations are available below."
    lines = [text, "", "Reviewed-source observations — bounded explicit mentions",
             "Note dates: " + ", ".join(value.date().isoformat() for value in facts.source_dates),
             "These are dated note observations, not a complete medication list or order ledger."]
    if facts.selection_limited or facts.facts_limited:
        lines.append("Some sources or observations are omitted by the preparation limits; consult reviewed notes.")
    if not facts.facts:
        lines.append("No supported observations are shown; this does not establish absence from the chart.")
    for fact in facts.facts:
        label = FACT_LABELS[fact.state]
        if fact.correction:
            label += f"; superseded by correction dated {fact.correction.note_date.date().isoformat()}"
        lines.extend(["", _literal_markdown(f"{fact.kind.title()} · {fact.subject} {fact.value or ''} · {label}"),
                      _literal_markdown(f"Note {fact.note_date.date().isoformat()} · {fact.section}"),
                      *["> " + _literal_markdown(line) for line in fact.quote.splitlines()]])
        if fact.correction:
            lines.extend([_literal_markdown(f"Correction note {fact.correction.note_date.date().isoformat()} · {fact.correction.section}"),
                          *["> " + _literal_markdown(line) for line in fact.correction.quote.splitlines()]])
    return "\n".join(lines)
