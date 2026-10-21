"""Direct, browser-minimized projection of Core-owned Session Prep results."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from pydantic import ValidationError

from app.clients.session_prep_contract import (
    SessionPrepBriefText,
    SessionPrepContextReadResult,
    SessionPrepPendingResult,
    SessionPrepReadyResult,
    SessionPrepRecap,
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
# Only the observed standalone citation syntax is a display token. Bare IDs,
# nested/escaped parentheses, URL destinations and attributes remain literal.
_VISIT_NOTE_CITATION = re.compile(
    r"(?<!\S)\((visit_note_[A-Za-z0-9._~-]+)\)(?=$|\s|[.,;:!?](?=\s|$))"
)
_HTML_TAG_NAME = re.compile(r"</?([A-Za-z][A-Za-z0-9:-]*)")
_VOID_HTML_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})
# Dynamic observation values escape these brackets/angles, so only the
# appended server delimiter remains raw. It grants formatting, not authority.
OBSERVATIONS_LITERAL_MARKER = "<!-- [reviewed-source-observations:v1] -->"
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

def session_prep_egress_is_safe(
    result: SessionPrepContextReadResult | SessionPrepResponse,
) -> bool:
    """Reject prohibited clinical claims in source or projected brief text."""
    if isinstance(result, SessionPrepResponse):
        if result.state not in {"ready", "refreshing"}:
            return True
        brief = result.prepared_brief
        if brief is None:
            return False
        text = [brief.recap, *brief.trajectory_and_material_changes,
                *brief.talking_points, *brief.open_threads, *brief.cautions,
                *brief.suggested_outline]
        if brief.patient_reported is not None:
            text.append(brief.patient_reported.summary)
        return not any(pattern.search(value) for value in text for pattern in _PROHIBITED_EGRESS)
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


def _citation_is_wrapped(text: str, start: int) -> bool:
    """Conservatively retain tokens inside literal or ambiguous wrappers.

    This is only a display exclusion, not a Markdown/HTML renderer or source
    authority. Unclosed wrappers remain literal through the end of the item.
    """
    parentheses = 0
    code_width = 0
    tags: list[str] = []
    tag_start: int | None = None
    quote: str | None = None
    cursor = 0
    while cursor < start:
        character = text[cursor]
        if code_width:
            if character == "`":
                end = cursor + 1
                while end < len(text) and text[end] == "`":
                    end += 1
                if end - cursor == code_width:
                    code_width = 0
                cursor = end
            else:
                cursor += 1
            continue
        if quote:
            if character == "\\":
                cursor += 2
                continue
            if character == quote:
                quote = None
            cursor += 1
            continue
        if tag_start is not None:
            if character in {'"', "'"}:
                quote = character
            elif character == ">":
                value = text[tag_start:cursor + 1]
                tag = _HTML_TAG_NAME.match(value)
                if tag is not None:
                    name = tag[1].lower()
                    if value.startswith("</"):
                        if name in tags:
                            del tags[len(tags) - 1 - tags[::-1].index(name):]
                    elif name not in _VOID_HTML_TAGS and not value.endswith("/>"):
                        tags.append(name)
                tag_start = None
            cursor += 1
            continue
        if text.startswith("<!--", cursor):
            end = text.find("-->", cursor + 4)
            if end == -1 or end + 3 > start:
                return True
            cursor = end + 3
            continue
        if character == "<" and _HTML_TAG_NAME.match(text, cursor):
            tag_start = cursor
            cursor += 1
            continue
        if tags:
            cursor += 1
            continue
        if character == "\\":
            cursor += 2
            continue
        if character == "`":
            end = cursor + 1
            while end < len(text) and text[end] == "`":
                end += 1
            code_width = end - cursor
            cursor = end
            continue
        if character == '"' or (character == "'" and (cursor == 0 or not text[cursor - 1].isalnum())):
            quote = character
        elif character == "(":
            parentheses += 1
        elif character == ")":
            parentheses = max(0, parentheses - 1)
        cursor += 1
    return bool(parentheses or code_width or tags or tag_start is not None or quote)


def _dated_brief_citations(
    item: SessionPrepRecap | SessionPrepBriefText, note_labels: dict[str, str],
) -> str:
    """Label only this item's verified note citations; retain all other text.

    The labels describe supporting-note dates, never clinical event dates.
    This operates on generated brief items, not source excerpts or canonical
    history text, and does not change the Core-owned artifact or its lineage.
    """
    refs = set(item.source_refs)

    def label(match: re.Match[str]) -> str:
        ref = match.group(1)
        if ref in refs and ref in note_labels and not _citation_is_wrapped(item.text, match.start()):
            return f"({note_labels[ref]})"
        return match[0]

    return _VISIT_NOTE_CITATION.sub(label, item.text)


def project_session_prep_result(
    result: SessionPrepContextReadResult,
    *,
    client_turn_id: uuid.UUID,
) -> SessionPrepResponse:
    """Project safe brief text with dated labels for verified note citations."""
    if isinstance(result, (SessionPrepReadyResult, SessionPrepRefreshingResult)):
        if not session_prep_egress_is_safe(result):
            return _blocked_projection(client_turn_id)
        brief = result.prepared_brief
        note_labels = {
            source.source_ref: f"Reviewed note · {source.source_at.date().isoformat()}"
            for source in result.provenance
            if source.included and source.source_class == "clinician_finalized_note"
            and source.source_ref is not None and source.source_at is not None
        }
        try:
            projected = SessionPrepProjectedBrief(
                recap=_dated_brief_citations(brief.recap, note_labels),
                trajectory_and_material_changes=[
                    _dated_brief_citations(item, note_labels)
                    for item in brief.trajectory_and_material_changes
                ],
                talking_points=[_dated_brief_citations(item, note_labels) for item in brief.talking_points],
                open_threads=[_dated_brief_citations(item, note_labels) for item in brief.open_threads],
                cautions=[_dated_brief_citations(item, note_labels) for item in brief.cautions],
                suggested_outline=[_dated_brief_citations(item, note_labels) for item in brief.suggested_outline],
                patient_reported=(
                    SessionPrepProjectedPatientReport(
                        label=brief.patient_reported.label,
                        summary=brief.patient_reported.summary,
                    )
                    if brief.patient_reported is not None
                    else None
                ),
            )
        except ValidationError:
            # A short opaque ref can expand beyond an item's projection bound.
            # Keep the same fail-closed outcome as the canonical size limit.
            return _blocked_projection(client_turn_id)
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
        # Formatting can shorten a long ref into the existing regex window.
        # Independently gate the exact visible/canonical projection as well.
        if not session_prep_egress_is_safe(response):
            return _blocked_projection(client_turn_id)
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
    lines = [text, "", OBSERVATIONS_LITERAL_MARKER,
             "Reviewed-source observations — bounded explicit mentions",
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
