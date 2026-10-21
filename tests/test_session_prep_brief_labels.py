"""Display-only dated citations for synthetic generated Session Prep briefs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.clients.session_prep_contract import parse_session_prep_result
from app.clients.session_prep_facts_contract import SessionPrepFactsReadResponse
from app.session_prep import (
    MAX_SESSION_PREP_CANONICAL_CHARS, project_session_prep_facts,
    project_session_prep_result, render_session_prep_text,
)
from tests.test_provider_evidence import panel_render_data
from tests.test_session_prep_facts import _facts_envelope, _VisibleHistory
from tests.test_session_prep_runtime import TURN_ID

ROOT = Path(__file__).parents[1]
A = "visit_note_01m5dea032f1kayt4n0q1r5twh"
B = "visit_note_01m5dec8eke2y95h4nk78mn9vq"
D = "visit_note_01m5derh64eezsbz34jmm6ahcb"
DATES = {
    A: "2026-09-30T00:00:00Z",
    B: "2026-10-07T00:00:00Z",
    D: "2026-10-08T00:00:00Z",
}


def _body(text, refs=None):
    body = json.loads((ROOT / "contracts/fixtures/provider-session-prep-v1/result-ready-brief.json").read_text())
    body.update(
        current_through="2026-10-08T00:00:00Z",
        snapshot_generated_at="2026-10-20T12:00:00Z",
        brief_generated_at="2026-10-20T12:00:01Z",
        source_count=3,
    )
    prototype = body["provenance"][0]
    body["provenance"] = [
        dict(prototype, source_ref=ref, source_at=date)
        for ref, date in reversed(list(DATES.items()))
    ] + body["provenance"][1:]
    brief = body["prepared_brief"]
    brief["recap"] = {"text": text, "source_refs": refs or [A]}
    for key in ("talking_points", "suggested_outline"):
        for item in brief[key]:
            item["source_refs"] = [B]
    return body


def _project(body):
    return project_session_prep_result(parse_session_prep_result(body), client_turn_id=TURN_ID)


@pytest.mark.parametrize("state", ["ready", "refreshing"])
def test_observed_synthetic_recap_uses_each_verified_source_date(state):
    text = (
        f"On 09/30/2026 ({A}), office blood pressure was documented. "
        f"On 10/07/2026 ({B}), a prior plan was documented. "
        f"On 10/08/2026 ({D}), a corrective plan was documented."
    )
    body = _body(text, [A, B, D])
    if state == "refreshing":
        body.update(state=state, freshness="stale", retry_after_seconds=5, reason_code="refresh_in_progress")
    original = json.dumps(body, sort_keys=True)
    result = _project(body)
    expected = text
    for ref, date in DATES.items():
        expected = expected.replace(f"({ref})", f"(Reviewed note · {date[:10]})")
    assert result.state == state and result.prepared_brief.recap == expected
    assert json.dumps(body, sort_keys=True) == original
    assert expected in render_session_prep_text(result)
    live = panel_render_data(render_session_prep_text(result), hydrated=False,
                             preparation=result.model_dump(mode="json"))
    history = panel_render_data(render_session_prep_text(result), hydrated=True)
    assert expected in live["literalTexts"]
    visible = _VisibleHistory()
    visible.feed(history["html"])
    assert expected in "".join(visible.fragments)
    assert "<a " not in history["html"]
    assert all(ref not in render_session_prep_text(result) for ref in DATES)


@pytest.mark.parametrize("token", [
    A, f"( {A} )", f"(({A}))", f"prefix({A})", f"({A})suffix",
    f"\\({A})", f"`({A})`", f"({A}, {B})",
    f"https://invalid.example/({A})", f"[note](({A}))",
    f'<a href="({A})">note</a>', f"({A}).example",
    "(visit_note_foreign_00001)", f"({A}_suffix)",
])
def test_unrecognized_or_unverified_tokens_remain_exact(token):
    text = "Literal content: " + token + " end."
    result = _project(_body(text))
    assert result.prepared_brief.recap == text


def test_included_source_cited_only_by_another_item_is_not_labeled():
    body = _body(f"Recap ({A}). Another item cites ({B}).")
    body["prepared_brief"]["talking_points"][0]["text"] = f"Review the prior note ({B})."
    result = _project(body).prepared_brief
    assert result.recap == f"Recap (Reviewed note · 2026-09-30). Another item cites ({B})."
    assert result.talking_points == ["Review the prior note (Reviewed note · 2026-10-07)."]


def test_patient_reported_ref_with_note_prefix_never_becomes_clinician_label():
    body = _body(f"Patient-reported reference ({D}).", [D])
    source = next(item for item in body["provenance"] if item["source_ref"] == D)
    source.update(source_class="patient_reported_visit_prep", author_class="patient",
                  reason_code="eligible_patient_visit_prep")
    body["provenance"] = [item for item in body["provenance"] if item["included"]]
    body["prepared_brief"]["patient_reported"] = {
        "label": "Patient-reported Visit Prep", "summary": f"Patient content ({D}).",
        "source_refs": [D],
    }
    brief = _project(body).prepared_brief
    assert brief.recap == body["prepared_brief"]["recap"]["text"]
    assert brief.patient_reported.summary == f"Patient content ({D})."


def test_each_generated_section_uses_its_own_refs_and_preserves_literal_content():
    text = f'Literal <img src=x> &amp; **markup**; note ({A}).'
    body = _body(text)
    for key in ("trajectory_and_material_changes", "talking_points", "open_threads", "cautions", "suggested_outline"):
        body["prepared_brief"][key] = [{"text": text, "source_refs": [A]}]
    result = _project(body)
    expected = text.replace(f"({A})", "(Reviewed note · 2026-09-30)")
    assert result.prepared_brief.recap == expected
    for key in ("trajectory_and_material_changes", "talking_points", "open_threads", "cautions", "suggested_outline"):
        assert getattr(result.prepared_brief, key) == [expected]
    live = panel_render_data(render_session_prep_text(result), hydrated=False,
                             preparation=result.model_dump(mode="json"))
    assert expected in live["literalTexts"]
    history = panel_render_data(render_session_prep_text(result), hydrated=True)
    assert "<img " not in history["html"]


def test_source_date_is_not_the_generated_date_or_local_timezone_conversion():
    body = _body(f"Model says 2026-01-01 ({A}). Twice ({A}).")
    source = next(item for item in body["provenance"] if item["source_ref"] == A)
    source["source_at"] = "2026-09-30T23:30:00-07:00"
    expected = "Model says 2026-01-01 (Reviewed note · 2026-09-30). Twice (Reviewed note · 2026-09-30)."
    result = _project(body)
    assert result.prepared_brief.recap == expected
    body["prepared_brief"]["recap"]["text"] = expected
    assert _project(body).prepared_brief.recap == expected


def test_same_date_notes_are_not_dropped_or_combined():
    body = _body(f"First ({A}). Second ({B}).", [A, B])
    next(item for item in body["provenance"] if item["source_ref"] == B)["source_at"] = DATES[A]
    assert _project(body).prepared_brief.recap == (
        "First (Reviewed note · 2026-09-30). Second (Reviewed note · 2026-09-30)."
    )


def test_original_prohibited_clinical_instruction_is_still_rejected():
    result = _project(_body(f"Increase your dose ({A}) to 50 mg."))
    assert result.state == "unavailable" and result.prepared_brief is None


def test_label_expansion_cannot_exceed_an_item_bound():
    body = _body("Safe recap.")
    ref = "visit_note_x"
    body["provenance"][0]["source_ref"] = ref
    text = "x" * (1000 - len(f" ({ref})")) + f" ({ref})"
    body["prepared_brief"]["talking_points"] = [{"text": text, "source_refs": [ref]}]
    assert len(text) == 1000
    result = _project(body)
    assert result.state == "unavailable" and result.prepared_brief is None


def test_label_expansion_cannot_exceed_canonical_persistence_bound():
    body = _body("x" * 3900)
    ref = "visit_note_x"
    body["provenance"][0]["source_ref"] = ref
    for key in ("talking_points", "suggested_outline"):
        body["prepared_brief"][key] = [{"text": "x" * 900, "source_refs": [ref]} for _ in range(8)]
    body["prepared_brief"]["open_threads"] = [{"text": "x", "source_refs": [ref]}]
    before = _project(body)
    assert before.state == "ready"
    padding = MAX_SESSION_PREP_CANONICAL_CHARS - 1 - len(render_session_prep_text(before))
    assert 0 < padding < 999
    body["prepared_brief"]["open_threads"][0]["text"] += "x" * padding
    assert len(render_session_prep_text(_project(body))) == MAX_SESSION_PREP_CANONICAL_CHARS - 1
    item = body["prepared_brief"]["talking_points"][0]
    item["text"] = "x" * (900 - len(f" ({ref})")) + f" ({ref})"
    result = _project(body)
    assert result.state == "unavailable" and result.prepared_brief is None


def test_dated_brief_projection_does_not_rewrite_literal_fact_or_correction_quotes():
    body = _facts_envelope("ready")
    facts = body["result"]["structured_facts"]
    prior_ref = facts["sources"][0]["source_ref"]

    def rename(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "source_ref" and child == prior_ref:
                    value[key] = A
                elif key == "source_refs":
                    value[key] = [A if ref == prior_ref else ref for ref in child]
                else:
                    rename(child)
        elif isinstance(value, list):
            for child in value:
                rename(child)

    rename(body)
    body["result"]["preparation"]["prepared_brief"]["recap"]["text"] = f"Prior note ({A})."
    for fact in facts["facts"]:
        for evidence in (fact["evidence"], fact["correction_evidence"]):
            if evidence:
                evidence["quote"] += f" Literal citation ({A})."
                evidence["end"] = evidence["start"] + len(evidence["quote"])
    original = json.dumps(body, sort_keys=True)
    parsed = SessionPrepFactsReadResponse.model_validate(body)
    result = project_session_prep_facts(parsed.result, client_turn_id=TURN_ID)
    assert result.prepared_brief.recap == "Prior note (Reviewed note · 2026-08-12)."
    assert json.dumps(body, sort_keys=True) == original
    for projected, source in zip(result.structured_facts.facts, facts["facts"]):
        assert projected.quote == source["evidence"]["quote"] and f"({A})" in projected.quote
    canonical = render_session_prep_text(result)
    live = panel_render_data(canonical, hydrated=False, preparation=result.model_dump(mode="json"))
    history = panel_render_data(canonical, hydrated=True)
    visible = _VisibleHistory()
    visible.feed(history["html"])
    for fact in result.structured_facts.facts:
        assert fact.quote in live["literalTexts"] and fact.quote in "".join(visible.fragments)
        if fact.correction:
            assert f"({A})" in fact.correction.quote
            assert fact.correction.quote in live["literalTexts"]
            assert fact.correction.quote in "".join(visible.fragments)

