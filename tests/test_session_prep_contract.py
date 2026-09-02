"""Dormant consumer-contract tests for Session Prep context read v1.0.0."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.clients.session_prep_contract import (
    SessionPrepContextReadRequest,
    SessionPrepPendingResult,
    SessionPrepReadyResult,
    SessionPrepRefreshingResult,
    SessionPrepUnavailableResult,
    parse_session_prep_result,
    validate_session_prep_exchange,
)


ROOT = Path(__file__).parents[1] / "contracts"
FIXTURES = ROOT / "fixtures" / "provider-session-prep-v1"
SCHEMAS = ROOT / "schemas" / "provider-session-prep-v1"
CANONICAL_SHA256 = {
    SCHEMAS / "request.schema.json": "657a75acbe2cf4fb05019c1764f829e88a499b50fae61b6746d19ab5c79ff9f7",
    SCHEMAS / "result.schema.json": "5e109bc901276dfe784ded2212fcc0c877f64ed60e1afd1b351046330138b17b",
    FIXTURES / "request-brief.json": "4edc9bdc767202d19d4f435b4d57e66ecbed4988812ac621c06bef98cc6ed832",
    FIXTURES / "request-supporting-context.json": "b4896560f9c27ed34373fb6b11cf6c68224f5f43c3a871cc10da3ce3431e1475",
    FIXTURES / "result-pending.json": "a3be0bd8ef517e4573479e81c93e67e0f71258b72b50c5a7801f9d09da507756",
    FIXTURES / "result-ready-brief.json": "fc14f4d8a020809aa45db7e4b5bfb00a02c6319babf4003dd4336ec06b79491a",
    FIXTURES / "result-ready-supporting-context.json": "7242063179b40c0c2923fc768ed4dac2337aefa3a0feaa18320365c9fb557a82",
    FIXTURES / "result-refreshing-brief.json": "6f21876d1207872f366e75af27bbaa6823b4f98c480aeaa291111370d6894496",
    FIXTURES / "result-unavailable.json": "cd34858a808f1dd28825a129bb4655e77d10adc14a956a7cad2774bfbf81ff02",
}


def fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_artifacts_match_architecture_commit_25806a2() -> None:
    for path, expected in CANONICAL_SHA256.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


@pytest.mark.parametrize("name", ["request-brief.json", "request-supporting-context.json"])
def test_requests_are_exact_scoped_and_closed(name: str) -> None:
    raw = fixture(name)
    request = SessionPrepContextReadRequest.model_validate(raw)
    assert request.subject_ref is None
    assert request.model_dump(mode="json") == raw
    for key in ("actor_ref", "tenant_ref", "patient_ref", "record_ref", "encounter_ref"):
        invalid = copy.deepcopy(raw)
        invalid["arguments"][key] = "forbidden_selector_001"
        with pytest.raises(ValidationError):
            SessionPrepContextReadRequest.model_validate(invalid)


@pytest.mark.parametrize(
    "name,kind",
    [
        ("result-ready-brief.json", SessionPrepReadyResult),
        ("result-ready-supporting-context.json", SessionPrepReadyResult),
        ("result-refreshing-brief.json", SessionPrepRefreshingResult),
        ("result-pending.json", SessionPrepPendingResult),
        ("result-unavailable.json", SessionPrepUnavailableResult),
    ],
)
def test_result_fixtures_round_trip_as_closed_variants(name: str, kind: type) -> None:
    raw = fixture(name)
    result = parse_session_prep_result(raw)
    assert isinstance(result, kind)
    assert result.model_dump(mode="json") == raw
    with pytest.raises(ValidationError):
        parse_session_prep_result({**raw, "raw_note": "forbidden"})


def test_detail_and_stale_window_are_request_bound() -> None:
    request = SessionPrepContextReadRequest.model_validate(fixture("request-brief.json"))
    supporting = parse_session_prep_result(fixture("result-ready-supporting-context.json"))
    with pytest.raises(ValueError, match="does not match request"):
        validate_session_prep_exchange(
            request, supporting, observed_at=datetime(2026, 9, 2, 12, 6, tzinfo=UTC),
        )

    raw = fixture("result-ready-supporting-context.json")
    raw["detail"] = "brief"
    with pytest.raises(ValidationError, match="detail does not match"):
        parse_session_prep_result(raw)

    refreshing = parse_session_prep_result(fixture("result-refreshing-brief.json"))
    validate_session_prep_exchange(
        request, refreshing, observed_at=datetime(2026, 9, 2, 12, 25, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="stale window has expired"):
        validate_session_prep_exchange(
            request, refreshing,
            observed_at=datetime(2026, 9, 2, 12, 25, 2, tzinfo=UTC),
        )


@pytest.mark.parametrize("artifact", ["snapshot", "brief"])
def test_artifact_versions_require_predecessor_refs(artifact: str) -> None:
    raw = fixture("result-ready-supporting-context.json")
    raw[f"{artifact}_supersedes_ref"] = None
    with pytest.raises(ValidationError, match="predecessor"):
        parse_session_prep_result(raw)


def test_source_count_citations_and_supporting_provenance_are_bound() -> None:
    raw = fixture("result-ready-supporting-context.json")
    mutations = (
        ("source_count", 1, "source_count"),
        ("supporting_version", 2, "provenance does not match"),
        ("unknown_citation", "unknown_source_001", "unincluded source"),
    )
    for kind, value, message in mutations:
        invalid = copy.deepcopy(raw)
        if kind == "source_count":
            invalid["source_count"] = value
        elif kind == "supporting_version":
            invalid["supporting_context"][0]["source_version"] = value
        else:
            invalid["prepared_brief"]["recap"]["source_refs"] = [value]
        with pytest.raises(ValidationError, match=message):
            parse_session_prep_result(invalid)


def test_provenance_disposition_and_class_limits_fail_closed() -> None:
    raw = fixture("result-ready-brief.json")
    invalid = copy.deepcopy(raw)
    invalid["provenance"][1]["source_ref"] = "visitprep_forbidden_001"
    with pytest.raises(ValidationError, match="cannot carry source lineage"):
        parse_session_prep_result(invalid)

    too_many = copy.deepcopy(raw)
    note = too_many["provenance"][0]
    too_many["provenance"][1:1] = [
        {**copy.deepcopy(note), "source_ref": f"note_synthetic_00{i}"}
        for i in range(2, 7)
    ]
    too_many["source_count"] = 6
    with pytest.raises(ValidationError, match="class bounds"):
        parse_session_prep_result(too_many)


def test_empty_states_cannot_carry_lineage_or_content() -> None:
    raw = fixture("result-pending.json")
    raw["snapshot_ref"] = "snapshot_forbidden_001"
    with pytest.raises(ValidationError):
        parse_session_prep_result(raw)
