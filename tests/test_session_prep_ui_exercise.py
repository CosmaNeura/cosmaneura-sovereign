"""Fail-closed tests for the synthetic Session Prep UI exercise worksheet."""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from tools import prepare_session_prep_ui_exercise as exercise
from tools import verify_session_prep_ui_evidence as evidence


FIXED_NOW = dt.datetime(2026, 9, 3, 21, 5, tzinfo=dt.UTC)
EXERCISE_HEX = "0123456789abcdef0123456789abcdef"
CHALLENGE_HEX = "fedcba9876543210fedcba9876543210"
REAL_VERIFY_DEMO_CHECKOUT = exercise._verify_demo_checkout
RUNBOOK = exercise.ROOT / "docs/flight-plans/session-prep-synthetic-ui-ux-exercise.md"


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(exercise, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(evidence, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(
        exercise, "_verify_demo_checkout", lambda repo: Path(repo).resolve()
    )


def _initialize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    values = iter((EXERCISE_HEX, CHALLENGE_HEX))
    monkeypatch.setattr(exercise.secrets, "token_hex", lambda _size: next(values))
    path = tmp_path / "session-prep-ui-worksheet.json"
    exercise.initialize(path, "chromium", 140, "voiceover", exercise.ROOT)
    return path


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    path.chmod(0o600)


def _revision(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _complete(path: Path) -> dict[str, Any]:
    worksheet = _load(path)
    worksheet["manual_checks"] = dict.fromkeys(exercise.MANUAL_CHECK_IDS, "pass")
    _write(path, worksheet)
    return worksheet


def _detached_demo_repo(tmp_path: Path) -> Path:
    destination = tmp_path / "pinned-demo-repo"
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--shared",
            "--no-checkout",
            str(exercise.ROOT),
            str(destination),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "checkout",
            "--quiet",
            "--detach",
            exercise.UI_SOURCE_COMMIT,
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    return destination


def _set_nested(value: dict[str, Any], path: tuple[str, ...], replacement: Any) -> None:
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement


def _manual_pass_paths(
    value: Any, prefix: tuple[str, ...] = ()
) -> set[tuple[str, ...]]:
    if isinstance(value, dict):
        result: set[tuple[str, ...]] = set()
        for key, child in value.items():
            result.update(_manual_pass_paths(child, (*prefix, key)))
        return result
    return {prefix} if value == "pass" else set()


def test_manual_check_set_exactly_covers_all_44_receipt_observations() -> None:
    assert len(exercise.MANUAL_CHECK_PATHS) == 44
    assert len(set(exercise.MANUAL_CHECK_PATHS)) == 44
    assert len(set(exercise.MANUAL_CHECK_IDS)) == 44
    assert _manual_pass_paths(exercise._manual_observations()) == set(
        exercise.MANUAL_CHECK_PATHS
    )


def test_runbook_uses_separate_pinned_demo_checkout_without_bulk_pass() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "git worktree add --detach" in runbook
    assert runbook.count("--demo-repo /absolute/path/to/session-prep-ui-source") == 4
    assert "44 sequential single-check" in runbook
    assert "Never edit" in runbook
    assert "the worksheet JSON directly" in runbook
    assert "--expected-worksheet-sha256" in runbook
    assert "must not be copied or edited into one" in runbook


def test_init_creates_private_closed_all_not_run_worksheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _initialize(tmp_path, monkeypatch)
    worksheet = _load(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.stat().st_size <= exercise.MAX_WORKSHEET_BYTES
    assert worksheet == exercise._expected_worksheet(
        "2026-09-03T21:05:00Z",
        f"uxrun_{EXERCISE_HEX}",
        f"uxchallenge_{CHALLENGE_HEX}",
        "chromium",
        140,
        "voiceover",
        "not-run",
    )
    assert set(worksheet["manual_checks"].values()) == {"not-run"}
    assert len(worksheet["manual_checks"]) == 44
    assert worksheet["scope"]["live_service_contacted"] is False
    assert worksheet["scope"]["real_provider_data_used"] is False
    assert worksheet["scope"]["phi_used"] is False
    assert worksheet["observed_source"] == {
        "sovereign_commit": exercise.UI_SOURCE_COMMIT,
        "sovereign_tree": exercise.UI_SOURCE_TREE,
        "checkout_state": "detached-clean",
    }
    with pytest.raises(evidence.EvidenceError):
        evidence.validate_receipt(worksheet)


def test_init_uses_two_independent_fresh_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def token_hex(size: int) -> str:
        calls.append(size)
        return ("1" if len(calls) == 1 else "2") * 32

    monkeypatch.setattr(exercise.secrets, "token_hex", token_hex)
    path = tmp_path / "worksheet.json"
    exercise.initialize(path, "firefox", 141, "nvda", exercise.ROOT)
    worksheet = _load(path)

    assert calls == [16, 16]
    assert worksheet["exercise_id"] == "uxrun_" + "1" * 32
    assert worksheet["challenge"] == "uxchallenge_" + "2" * 32


def test_status_reports_only_bounded_progress_next_check_and_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _initialize(tmp_path, monkeypatch)
    before = path.read_bytes()

    summary = exercise.worksheet_status(path, exercise.ROOT)

    assert summary == (
        f"{exercise.STATUS_PASS_PREFIX} passed=0/44; remaining=44; "
        f"next={exercise.MANUAL_CHECK_IDS[0]}; revision={_revision(path)}"
    )
    assert path.read_bytes() == before
    assert str(path) not in summary
    assert EXERCISE_HEX not in summary
    assert CHALLENGE_HEX not in summary
    assert "chromium" not in summary
    assert len(summary) < 256


def test_mark_changes_exactly_one_check_and_advances_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _initialize(tmp_path, monkeypatch)
    before = _load(path)
    old_revision = _revision(path)

    summary = exercise.mark_check(
        path,
        exercise.MANUAL_CHECK_IDS[0],
        old_revision,
        exercise.ROOT,
    )

    after = _load(path)
    expected = dict(before)
    expected_checks = dict(before["manual_checks"])
    expected_checks[exercise.MANUAL_CHECK_IDS[0]] = "pass"
    expected["manual_checks"] = expected_checks
    assert after == expected
    assert _revision(path) != old_revision
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert summary == (
        f"{exercise.MARK_PASS_PREFIX} {exercise.MANUAL_CHECK_IDS[0]}; progress "
        f"passed=1/44; remaining=43; next={exercise.MANUAL_CHECK_IDS[1]}; "
        f"revision={_revision(path)}"
    )


def test_mark_rejects_stale_repeat_unknown_and_malformed_revision_without_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _initialize(tmp_path, monkeypatch)
    stale_revision = _revision(path)
    exercise.mark_check(
        path,
        exercise.MANUAL_CHECK_IDS[0],
        stale_revision,
        exercise.ROOT,
    )
    current = path.read_bytes()
    current_revision = _revision(path)

    attempts = (
        (exercise.MANUAL_CHECK_IDS[1], stale_revision),
        (exercise.MANUAL_CHECK_IDS[0], current_revision),
        (exercise.MANUAL_CHECK_IDS[-1], current_revision),
        ("states.ready.unknown", current_revision),
        (exercise.MANUAL_CHECK_IDS[1], "sha256:not-a-digest"),
    )
    for check_id, revision in attempts:
        with pytest.raises(exercise.ExerciseError):
            exercise.mark_check(path, check_id, revision, exercise.ROOT)
        assert path.read_bytes() == current


def test_status_and_mark_reject_noncontiguous_pass_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _initialize(tmp_path, monkeypatch)
    worksheet = _load(path)
    worksheet["manual_checks"][exercise.MANUAL_CHECK_IDS[1]] = "pass"
    _write(path, worksheet)
    before = path.read_bytes()

    with pytest.raises(exercise.ExerciseError):
        exercise.worksheet_status(path, exercise.ROOT)
    with pytest.raises(exercise.ExerciseError):
        exercise.mark_check(
            path,
            exercise.MANUAL_CHECK_IDS[0],
            _revision(path),
            exercise.ROOT,
        )
    assert path.read_bytes() == before


def test_mark_replace_failure_preserves_original_and_cleans_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _initialize(tmp_path, monkeypatch)
    before = path.read_bytes()

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(exercise.os, "replace", fail_replace)
    with pytest.raises(exercise.ExerciseError):
        exercise.mark_check(
            path,
            exercise.MANUAL_CHECK_IDS[0],
            _revision(path),
            exercise.ROOT,
        )

    assert path.read_bytes() == before
    assert list(tmp_path.glob(".session-prep-ui-update-*.tmp")) == []


def test_mark_detects_external_byte_change_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _initialize(tmp_path, monkeypatch)
    revision = _revision(path)
    calls = 0

    def verify(repo: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(b"concurrent change")
            path.chmod(0o600)
        return Path(repo).resolve()

    monkeypatch.setattr(exercise, "_verify_demo_checkout", verify)
    with pytest.raises(exercise.ExerciseError):
        exercise.mark_check(
            path,
            exercise.MANUAL_CHECK_IDS[0],
            revision,
            exercise.ROOT,
        )

    assert path.read_bytes() == b"concurrent change"
    assert list(tmp_path.glob(".session-prep-ui-update-*.tmp")) == []


def test_status_and_mark_reject_nonprivate_or_multiply_linked_worksheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _initialize(tmp_path, monkeypatch)
    path.chmod(0o640)
    with pytest.raises(exercise.ExerciseError):
        exercise.worksheet_status(path, exercise.ROOT)
    with pytest.raises(exercise.ExerciseError):
        exercise.mark_check(
            path,
            exercise.MANUAL_CHECK_IDS[0],
            _revision(path),
            exercise.ROOT,
        )

    path.chmod(0o600)
    linked = tmp_path / "worksheet-hardlink.json"
    os.link(path, linked)
    with pytest.raises(exercise.ExerciseError):
        exercise.worksheet_status(path, exercise.ROOT)
    with pytest.raises(exercise.ExerciseError):
        exercise.mark_check(
            path,
            exercise.MANUAL_CHECK_IDS[0],
            _revision(path),
            exercise.ROOT,
        )


def test_44_sequential_marks_still_seal_exact_v1_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _initialize(tmp_path, monkeypatch)
    for check_id in exercise.MANUAL_CHECK_IDS:
        exercise.mark_check(path, check_id, _revision(path), exercise.ROOT)
    assert "passed=44/44; remaining=0; next=complete" in (
        exercise.worksheet_status(path, exercise.ROOT)
    )

    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(
        exercise,
        "_validate_with_pinned_verifier",
        lambda _verifier_repo, _demo_repo, raw: evidence.validate_receipt(
            json.loads(raw)
        ),
    )
    exercise.seal(path, receipt, exercise.ROOT)
    accepted = evidence.load_receipt(receipt)
    assert all(
        value is False for value in accepted["evidence"]["readiness_claims"].values()
    )


def test_init_refuses_to_overwrite_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "worksheet.json"
    path.write_bytes(b"existing")
    original = path.read_bytes()

    with pytest.raises(exercise.ExerciseError):
        exercise.initialize(path, "chromium", 140, "voiceover", exercise.ROOT)

    assert path.read_bytes() == original


def test_evidence_paths_must_remain_outside_demo_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo_repo = tmp_path / "demo"
    demo_repo.mkdir()
    inside_worksheet = demo_repo / "worksheet.json"

    with pytest.raises(exercise.ExerciseError):
        exercise.initialize(
            inside_worksheet,
            "chromium",
            140,
            "voiceover",
            demo_repo,
        )
    assert not inside_worksheet.exists()

    outside_worksheet = _initialize(tmp_path, monkeypatch)
    _complete(outside_worksheet)
    inside_receipt = demo_repo / "receipt.json"
    monkeypatch.setattr(exercise, "_validate_with_pinned_verifier", lambda *_args: None)
    with pytest.raises(exercise.ExerciseError):
        exercise.seal(outside_worksheet, inside_receipt, demo_repo)
    assert not inside_receipt.exists()


def test_uncompleted_init_worksheet_cannot_be_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worksheet = _initialize(tmp_path, monkeypatch)
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(
        exercise,
        "_validate_with_pinned_verifier",
        lambda *_args: pytest.fail("an incomplete worksheet reached the verifier"),
    )

    with pytest.raises(exercise.ExerciseError):
        exercise.seal(worksheet, receipt, exercise.ROOT)

    assert not receipt.exists()


def test_seal_builds_exact_v1_receipt_and_forces_all_readiness_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worksheet_path = _initialize(tmp_path, monkeypatch)
    worksheet = _complete(worksheet_path)
    receipt_path = tmp_path / "receipt.json"
    captured: list[dict[str, Any]] = []

    def validate(_verifier_repo: Path, _demo_repo: Path, raw: bytes) -> None:
        receipt = json.loads(raw)
        evidence.validate_receipt(receipt)
        captured.append(receipt)

    monkeypatch.setattr(exercise, "_validate_with_pinned_verifier", validate)
    exercise.seal(worksheet_path, receipt_path, exercise.ROOT)
    receipt = evidence.load_receipt(receipt_path)
    expected = evidence._expected_evidence(
        "2026-09-03T21:05:00Z",
        f"uxrun_{EXERCISE_HEX}",
        f"uxchallenge_{CHALLENGE_HEX}",
        "chromium",
        140,
        "voiceover",
    )

    assert captured == [receipt]
    assert receipt["evidence"] == expected
    assert receipt["evidence_sha256"] == evidence._digest(expected)
    assert receipt["evidence_sha256"] == exercise._digest(expected)
    assert worksheet["started_at_utc"] == "2026-09-03T21:05:00Z"
    assert all(value is False for value in expected["readiness_claims"].values())
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
    assert receipt_path.stat().st_size <= exercise.MAX_RECEIPT_BYTES


def test_observed_timestamp_is_set_at_seal_not_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worksheet_path = _initialize(tmp_path, monkeypatch)
    _complete(worksheet_path)
    later = FIXED_NOW + dt.timedelta(minutes=17)
    monkeypatch.setattr(exercise, "_utc_now", lambda: later)
    monkeypatch.setattr(evidence, "_utc_now", lambda: later)
    monkeypatch.setattr(
        exercise,
        "_validate_with_pinned_verifier",
        lambda _verifier_repo, _demo_repo, raw: evidence.validate_receipt(
            json.loads(raw)
        ),
    )
    receipt_path = tmp_path / "receipt.json"

    exercise.seal(worksheet_path, receipt_path, exercise.ROOT)

    assert _load(receipt_path)["evidence"]["observed_at_utc"] == (
        "2026-09-03T21:22:00Z"
    )


@pytest.mark.parametrize("status", ["not-run", "partial", "fail", True, 1, None])
def test_every_check_must_be_explicit_pass(
    status: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _initialize(tmp_path, monkeypatch)
    worksheet = _complete(path)
    worksheet["manual_checks"][exercise.MANUAL_CHECK_IDS[-1]] = status
    _write(path, worksheet)

    with pytest.raises(exercise.ExerciseError):
        exercise.load_completed_worksheet(path, FIXED_NOW)


Mutation = Callable[[dict[str, Any]], None]


def _add_unknown(worksheet: dict[str, Any]) -> None:
    worksheet["operator_name"] = "free form"


def _add_unknown_check(worksheet: dict[str, Any]) -> None:
    worksheet["manual_checks"]["states.ready.patient_name"] = "pass"


def _remove_check(worksheet: dict[str, Any]) -> None:
    worksheet["manual_checks"].pop(exercise.MANUAL_CHECK_IDS[0])


@pytest.mark.parametrize(
    "mutation",
    [
        _add_unknown,
        _add_unknown_check,
        _remove_check,
        lambda value: _set_nested(value, ("environment", "browser_major"), True),
        lambda value: _set_nested(value, ("scope", "live_service_contacted"), True),
        lambda value: _set_nested(value, ("scope", "network_scope"), "staging"),
        lambda value: _set_nested(value, ("scope", "data_class"), "phi"),
        lambda value: _set_nested(value, ("scope", "real_provider_data_used"), True),
        lambda value: _set_nested(value, ("scope", "phi_used"), True),
        lambda value: _set_nested(value, ("scope", "phi_used"), 0),
        lambda value: _set_nested(
            value, ("observed_source", "sovereign_commit"), "f" * 40
        ),
        lambda value: _set_nested(
            value, ("observed_source", "checkout_state"), "mutable"
        ),
        lambda value: _set_nested(
            value,
            ("manual_checks", exercise.MANUAL_CHECK_IDS[0]),
            "patient Jane Doe passed",
        ),
    ],
)
def test_unknown_free_form_type_coerced_live_and_phi_shapes_are_rejected(
    mutation: Mutation, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _initialize(tmp_path, monkeypatch)
    worksheet = _complete(path)
    mutation(worksheet)
    _write(path, worksheet)

    with pytest.raises(exercise.ExerciseError):
        exercise.load_completed_worksheet(path, FIXED_NOW)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("exercise_id", "uxrun_" + "A" * 32),
        ("exercise_id", "record-provider-123"),
        ("challenge", "uxchallenge_" + "1" * 31),
        ("challenge", "real patient"),
    ],
)
def test_run_tokens_are_closed_lowercase_hex(
    field: str, value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _initialize(tmp_path, monkeypatch)
    worksheet = _complete(path)
    worksheet[field] = value
    _write(path, worksheet)

    with pytest.raises(exercise.ExerciseError):
        exercise.load_completed_worksheet(path, FIXED_NOW)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("environment", "browser_engine"), "chrome"),
        (("environment", "browser_engine"), "Chromium"),
        (("environment", "browser_major"), 0),
        (("environment", "browser_major"), 1000),
        (("environment", "screen_reader"), "none"),
        (("environment", "screen_reader"), "VoiceOver"),
    ],
)
def test_environment_is_closed_and_bounded(
    path: tuple[str, ...],
    value: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worksheet_path = _initialize(tmp_path, monkeypatch)
    worksheet = _complete(worksheet_path)
    _set_nested(worksheet, path, value)
    _write(worksheet_path, worksheet)

    with pytest.raises(exercise.ExerciseError):
        exercise.load_completed_worksheet(worksheet_path, FIXED_NOW)


@pytest.mark.parametrize(
    "started_at",
    [
        "2026-09-03T21:05:01Z",
        "2026-09-02T21:04:59Z",
        "2026-09-03T20:03:13Z",
        "2026-09-03T21:05:00.000Z",
        "2026-09-03T17:05:00-04:00",
        "2026-09-03 21:05:00Z",
    ],
)
def test_future_stale_pre_source_and_noncanonical_worksheets_are_rejected(
    started_at: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _initialize(tmp_path, monkeypatch)
    worksheet = _complete(path)
    worksheet["started_at_utc"] = started_at
    _write(path, worksheet)

    with pytest.raises(exercise.ExerciseError):
        exercise.load_completed_worksheet(path, FIXED_NOW)


def test_exact_24_hour_age_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _initialize(tmp_path, monkeypatch)
    worksheet = _complete(path)
    worksheet["started_at_utc"] = "2026-09-03T21:05:00Z"
    _write(path, worksheet)

    accepted = exercise.load_completed_worksheet(
        path, dt.datetime(2026, 9, 4, 21, 5, tzinfo=dt.UTC)
    )

    assert accepted == worksheet


def test_duplicate_keys_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _initialize(tmp_path, monkeypatch)
    _complete(path)
    raw = path.read_text(encoding="utf-8")
    raw = raw.replace(
        '"schema": "cosmaneura.session-prep.synthetic-ui-ux-worksheet.v1",',
        '"schema": "duplicate",\n  "schema": '
        '"cosmaneura.session-prep.synthetic-ui-ux-worksheet.v1",',
        1,
    )
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(exercise.ExerciseError):
        exercise.load_completed_worksheet(path, FIXED_NOW)


@pytest.mark.parametrize("raw", [b"[]", b"{not-json}", b"\xff\xfe{}", b'{"x":NaN}'])
def test_ambiguous_or_non_object_json_is_rejected(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "worksheet.json"
    path.write_bytes(raw)
    path.chmod(0o600)

    with pytest.raises(exercise.ExerciseError):
        exercise.load_completed_worksheet(path, FIXED_NOW)


def test_oversized_input_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "worksheet.json"
    path.write_bytes(b" " * (exercise.MAX_WORKSHEET_BYTES + 1))
    path.chmod(0o600)

    with pytest.raises(exercise.ExerciseError):
        exercise.load_completed_worksheet(path, FIXED_NOW)


def test_symlink_and_directory_inputs_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _initialize(tmp_path, monkeypatch)
    symlink = tmp_path / "worksheet-link.json"
    symlink.symlink_to(target)

    for path in (symlink, tmp_path):
        with pytest.raises(exercise.ExerciseError):
            exercise.load_completed_worksheet(path, FIXED_NOW)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unavailable")
def test_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "worksheet.fifo"
    os.mkfifo(fifo)

    with pytest.raises(exercise.ExerciseError):
        exercise.load_completed_worksheet(fifo, FIXED_NOW)


def test_verifier_failure_leaves_no_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worksheet = _initialize(tmp_path, monkeypatch)
    _complete(worksheet)
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(
        exercise,
        "_validate_with_pinned_verifier",
        lambda *_args: exercise._fail("synthetic verifier failure"),
    )

    with pytest.raises(exercise.ExerciseError):
        exercise.seal(worksheet, receipt, exercise.ROOT)

    assert not receipt.exists()


def test_seal_refuses_to_overwrite_existing_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worksheet = _initialize(tmp_path, monkeypatch)
    _complete(worksheet)
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(b"existing receipt")
    original = receipt.read_bytes()
    monkeypatch.setattr(exercise, "_validate_with_pinned_verifier", lambda *_args: None)

    with pytest.raises(exercise.ExerciseError):
        exercise.seal(worksheet, receipt, exercise.ROOT)

    assert receipt.read_bytes() == original


def test_exact_unchanged_committed_verifier_is_loaded_by_sha() -> None:
    verifier = exercise._pinned_verifier_bytes(exercise.ROOT)
    working_tree_verifier = exercise.ROOT / exercise.UI_VERIFIER_PATH

    assert hashlib.sha256(verifier).hexdigest() == exercise.UI_VERIFIER_SHA256
    assert verifier == working_tree_verifier.read_bytes()
    assert exercise.UI_VERIFIER_COMMIT == ("13ad964cb7d40b75c1209be431dcde3698ad6021")


def test_demo_preflight_requires_exact_detached_clean_checkout(tmp_path: Path) -> None:
    demo_repo = _detached_demo_repo(tmp_path)
    assert REAL_VERIFY_DEMO_CHECKOUT(demo_repo) == demo_repo.resolve()

    with pytest.raises(exercise.ExerciseError):
        REAL_VERIFY_DEMO_CHECKOUT(exercise.ROOT)
    with pytest.raises(exercise.ExerciseError):
        REAL_VERIFY_DEMO_CHECKOUT(demo_repo / "tools")

    ignored = demo_repo / ".env"
    ignored.write_text("MOCK_MODE=false\n", encoding="utf-8")
    with pytest.raises(exercise.ExerciseError):
        REAL_VERIFY_DEMO_CHECKOUT(demo_repo)
    ignored.unlink()

    (demo_repo / "unexpected-untracked.txt").write_text("drift", encoding="utf-8")
    with pytest.raises(exercise.ExerciseError):
        REAL_VERIFY_DEMO_CHECKOUT(demo_repo)


def test_status_and_mark_recheck_exact_demo_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worksheet = _initialize(tmp_path, monkeypatch)
    revision = _revision(worksheet)
    demo_repo = _detached_demo_repo(tmp_path)
    monkeypatch.setattr(exercise, "_verify_demo_checkout", REAL_VERIFY_DEMO_CHECKOUT)

    assert "passed=0/44" in exercise.worksheet_status(worksheet, demo_repo)
    (demo_repo / "unexpected-untracked.txt").write_text("drift", encoding="utf-8")

    with pytest.raises(exercise.ExerciseError):
        exercise.worksheet_status(worksheet, demo_repo)
    with pytest.raises(exercise.ExerciseError):
        exercise.mark_check(
            worksheet,
            exercise.MANUAL_CHECK_IDS[0],
            revision,
            demo_repo,
        )
    assert _revision(worksheet) == revision


def test_private_read_rejects_parent_swap_during_directory_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested_parent = tmp_path / "requested"
    alternate_parent = tmp_path / "alternate"
    held_parent = tmp_path / "held"
    requested_parent.mkdir()
    alternate_parent.mkdir()
    requested = requested_parent / "worksheet.json"
    alternate = alternate_parent / "worksheet.json"
    source = _initialize(tmp_path, monkeypatch)
    requested.write_bytes(source.read_bytes())
    alternate.write_bytes(source.read_bytes())
    requested.chmod(0o600)
    alternate.chmod(0o600)
    expected_parent = os.fspath(requested_parent.resolve())
    original_open = exercise.os.open
    swapped = False

    def swap_parent_on_open(
        target: object, flags: int, *args: object, **kwargs: object
    ) -> int:
        nonlocal swapped
        if (
            not swapped
            and os.fspath(target) == expected_parent
            and "dir_fd" not in kwargs
        ):
            swapped = True
            requested_parent.rename(held_parent)
            alternate_parent.rename(requested_parent)
            try:
                return original_open(target, flags, *args, **kwargs)
            finally:
                requested_parent.rename(alternate_parent)
                held_parent.rename(requested_parent)
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(exercise.os, "open", swap_parent_on_open)

    with pytest.raises(exercise.ExerciseError):
        exercise.worksheet_status(requested, exercise.ROOT)
    assert swapped is True
    assert _load(requested)["manual_checks"][exercise.MANUAL_CHECK_IDS[0]] == (
        "not-run"
    )


@pytest.mark.parametrize("index_flag", ["--skip-worktree", "--assume-unchanged"])
def test_demo_preflight_rejects_hidden_index_flags(
    tmp_path: Path, index_flag: str
) -> None:
    demo_repo = _detached_demo_repo(tmp_path)
    relative = "tools/session_prep_demo.html"
    subprocess.run(
        ["git", "-C", str(demo_repo), "update-index", index_flag, relative],
        check=True,
        capture_output=True,
        timeout=10,
    )
    status = subprocess.run(
        ["git", "-C", str(demo_repo), "status", "--porcelain=v1"],
        check=True,
        capture_output=True,
        timeout=10,
    )

    assert status.stdout == b""
    with pytest.raises(exercise.ExerciseError):
        REAL_VERIFY_DEMO_CHECKOUT(demo_repo)


def test_demo_preflight_checks_executable_bit_even_when_git_ignores_it(
    tmp_path: Path,
) -> None:
    demo_repo = _detached_demo_repo(tmp_path)
    relative = "tools/session_prep_demo.html"
    target = demo_repo / relative
    subprocess.run(
        ["git", "-C", str(demo_repo), "config", "core.fileMode", "false"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    status_result = subprocess.run(
        ["git", "-C", str(demo_repo), "status", "--porcelain=v1"],
        check=True,
        capture_output=True,
        timeout=10,
    )

    assert status_result.stdout == b""
    with pytest.raises(exercise.ExerciseError):
        REAL_VERIFY_DEMO_CHECKOUT(demo_repo)


def test_demo_preflight_disables_repository_fsmonitor_command(tmp_path: Path) -> None:
    demo_repo = _detached_demo_repo(tmp_path)
    marker = tmp_path / "fsmonitor-ran"
    command = tmp_path / "fsmonitor-hook"
    command.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    command.chmod(0o700)
    subprocess.run(
        ["git", "-C", str(demo_repo), "config", "core.fsmonitor", str(command)],
        check=True,
        capture_output=True,
        timeout=10,
    )

    assert REAL_VERIFY_DEMO_CHECKOUT(demo_repo) == demo_repo.resolve()
    assert not marker.exists()


def test_demo_preflight_byte_compares_when_status_and_index_tag_are_blind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo_repo = _detached_demo_repo(tmp_path)
    relative = "tools/session_prep_demo.html"
    subprocess.run(
        [
            "git",
            "-C",
            str(demo_repo),
            "update-index",
            "--assume-unchanged",
            relative,
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    (demo_repo / relative).write_text("concealed drift\n", encoding="utf-8")
    original_run_git = exercise._run_git

    def conceal_index_tag(repo: Path, arguments: list[str], maximum: int) -> bytes:
        raw = original_run_git(repo, arguments, maximum)
        if arguments == ["ls-files", "--stage", "-v", "-z"]:
            return b"\0".join(
                (b"H" + record[1:] if record.startswith(b"h ") else record)
                for record in raw.split(b"\0")
            )
        return raw

    monkeypatch.setattr(exercise, "_run_git", conceal_index_tag)

    with pytest.raises(exercise.ExerciseError):
        REAL_VERIFY_DEMO_CHECKOUT(demo_repo)


def test_demo_subdirectory_cannot_bypass_evidence_path_containment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo_repo = _detached_demo_repo(tmp_path)
    output = demo_repo / "worksheet.json"
    monkeypatch.setattr(exercise, "_verify_demo_checkout", REAL_VERIFY_DEMO_CHECKOUT)

    with pytest.raises(exercise.ExerciseError):
        exercise.initialize(
            output,
            "chromium",
            140,
            "voiceover",
            demo_repo / "tools",
        )

    assert not output.exists()


def test_documented_demo_self_test_preserves_exact_checkout(tmp_path: Path) -> None:
    demo_repo = _detached_demo_repo(tmp_path)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = "."

    completed = subprocess.run(
        [sys.executable, "tools/run_session_prep_demo.py", "--self-test"],
        check=False,
        capture_output=True,
        cwd=demo_repo,
        env=environment,
        timeout=20,
    )

    assert completed.returncode == 0
    assert REAL_VERIFY_DEMO_CHECKOUT(demo_repo) == demo_repo.resolve()


def test_valid_init_cli_creates_only_a_not_run_worksheet(tmp_path: Path) -> None:
    output = tmp_path / "worksheet.json"
    demo_repo = _detached_demo_repo(tmp_path)
    tool = exercise.ROOT / "tools/prepare_session_prep_ui_exercise.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(tool),
            "init",
            str(output),
            "--browser-engine",
            "chromium",
            "--browser-major",
            "140",
            "--screen-reader",
            "voiceover",
            "--demo-repo",
            str(demo_repo),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert completed.stdout.strip() == exercise.INIT_PASS_SUMMARY
    assert str(output) not in completed.stdout
    assert set(_load(output)["manual_checks"].values()) == {"not-run"}
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_status_and_mark_cli_are_revision_bound_and_sanitized(tmp_path: Path) -> None:
    worksheet = tmp_path / "worksheet.json"
    demo_repo = _detached_demo_repo(tmp_path)
    tool = exercise.ROOT / "tools/prepare_session_prep_ui_exercise.py"
    initialized = subprocess.run(
        [
            sys.executable,
            str(tool),
            "init",
            str(worksheet),
            "--browser-engine",
            "chromium",
            "--browser-major",
            "140",
            "--screen-reader",
            "voiceover",
            "--demo-repo",
            str(demo_repo),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert initialized.returncode == 0
    sensitive = _load(worksheet)

    status_result = subprocess.run(
        [
            sys.executable,
            str(tool),
            "status",
            str(worksheet),
            "--demo-repo",
            str(demo_repo),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert status_result.returncode == 0
    assert status_result.stderr == ""
    assert "passed=0/44; remaining=44" in status_result.stdout
    revision = re.search(r"revision=(sha256:[0-9a-f]{64})", status_result.stdout)
    assert revision is not None
    for hidden in (
        str(worksheet),
        sensitive["exercise_id"],
        sensitive["challenge"],
        "chromium",
        "voiceover",
    ):
        assert hidden not in status_result.stdout

    marked = subprocess.run(
        [
            sys.executable,
            str(tool),
            "mark",
            str(worksheet),
            exercise.MANUAL_CHECK_IDS[0],
            "--expected-worksheet-sha256",
            revision.group(1),
            "--demo-repo",
            str(demo_repo),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert marked.returncode == 0
    assert marked.stderr == ""
    assert "passed=1/44; remaining=43" in marked.stdout
    assert _load(worksheet)["manual_checks"][exercise.MANUAL_CHECK_IDS[0]] == "pass"

    stale = subprocess.run(
        [
            sys.executable,
            str(tool),
            "mark",
            str(worksheet),
            exercise.MANUAL_CHECK_IDS[1],
            "--expected-worksheet-sha256",
            revision.group(1),
            "--demo-repo",
            str(demo_repo),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert stale.returncode == 1
    assert stale.stdout == ""
    assert stale.stderr.strip() == exercise.FAIL_SUMMARY
    assert str(worksheet) not in stale.stderr


def test_concurrent_mark_cli_allows_only_one_writer_for_one_revision(
    tmp_path: Path,
) -> None:
    worksheet = tmp_path / "worksheet.json"
    demo_repo = _detached_demo_repo(tmp_path)
    tool = exercise.ROOT / "tools/prepare_session_prep_ui_exercise.py"
    initialized = subprocess.run(
        [
            sys.executable,
            str(tool),
            "init",
            str(worksheet),
            "--browser-engine",
            "chromium",
            "--browser-major",
            "140",
            "--screen-reader",
            "voiceover",
            "--demo-repo",
            str(demo_repo),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert initialized.returncode == 0
    revision = _revision(worksheet)

    processes = [
        subprocess.Popen(
            [
                sys.executable,
                str(tool),
                "mark",
                str(worksheet),
                check_id,
                "--expected-worksheet-sha256",
                revision,
                "--demo-repo",
                str(demo_repo),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for check_id in (exercise.MANUAL_CHECK_IDS[0],) * 2
    ]
    results = [process.communicate(timeout=30) for process in processes]

    assert sorted(process.returncode for process in processes) == [0, 1]
    worksheet_value = _load(worksheet)
    assert (
        sum(value == "pass" for value in worksheet_value["manual_checks"].values()) == 1
    )
    for process, (stdout, stderr) in zip(processes, results, strict=True):
        if process.returncode == 0:
            assert stderr == ""
            assert "passed=1/44; remaining=43" in stdout
        else:
            assert stdout == ""
            assert stderr.strip() == exercise.FAIL_SUMMARY


def test_real_seal_runs_pinned_verifier_and_both_offline_harnesses(
    tmp_path: Path,
) -> None:
    demo_repo = _detached_demo_repo(tmp_path)
    worksheet = tmp_path / "worksheet.json"
    receipt = tmp_path / "receipt.json"
    tool = exercise.ROOT / "tools/prepare_session_prep_ui_exercise.py"
    initialized = subprocess.run(
        [
            sys.executable,
            str(tool),
            "init",
            str(worksheet),
            "--browser-engine",
            "chromium",
            "--browser-major",
            "140",
            "--screen-reader",
            "voiceover",
            "--demo-repo",
            str(demo_repo),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert initialized.returncode == 0
    _complete(worksheet)

    completed = subprocess.run(
        [
            sys.executable,
            str(tool),
            "seal",
            str(worksheet),
            str(receipt),
            "--demo-repo",
            str(demo_repo),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert completed.stdout.strip() == exercise.SEAL_PASS_SUMMARY
    assert str(worksheet) not in completed.stdout
    assert str(receipt) not in completed.stdout
    accepted = evidence.load_receipt(receipt)
    assert accepted["evidence"]["scope"]["data_class"] == "synthetic-only"
    assert all(
        value is False for value in accepted["evidence"]["readiness_claims"].values()
    )


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["init"],
        ["seal", "SENSITIVE_MISSING"],
        ["status", "SENSITIVE_MISSING"],
        ["mark", "SENSITIVE_PATH", "SENSITIVE_CHECK"],
        [
            "mark",
            "SENSITIVE_PATH",
            "states.ready.heading_and_status",
            "states.ready.bounded_brief_readable",
            "--expected-worksheet-sha256",
            "sha256:" + "0" * 64,
            "--demo-repo",
            "SENSITIVE_REPO",
        ],
        ["--SENSITIVE_UNKNOWN_FLAG"],
        [
            "init",
            "SENSITIVE_OUTPUT",
            "--browser-engine",
            "chromium",
            "--browser-major",
            "140",
            "--screen-reader",
            "voiceover",
            "--all-pass",
        ],
    ],
)
def test_invalid_cli_invocation_is_sanitized_and_has_no_all_pass_shortcut(
    arguments: list[str],
) -> None:
    tool = exercise.ROOT / "tools/prepare_session_prep_ui_exercise.py"
    completed = subprocess.run(
        [sys.executable, str(tool), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr.strip() == exercise.FAIL_SUMMARY
    assert "SENSITIVE" not in completed.stderr
    assert "all-pass" not in completed.stderr
    assert len(completed.stderr) < 120


def test_cli_failure_does_not_echo_input_path_or_content(tmp_path: Path) -> None:
    sentinel = "SENSITIVE_PROVIDER_SENTINEL"
    path = tmp_path / f"{sentinel}.json"
    path.write_text(json.dumps({"note": sentinel}), encoding="utf-8")
    receipt = tmp_path / f"{sentinel}-receipt.json"
    tool = exercise.ROOT / "tools/prepare_session_prep_ui_exercise.py"

    completed = subprocess.run(
        [sys.executable, str(tool), "seal", str(path), str(receipt)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr.strip() == exercise.FAIL_SUMMARY
    assert sentinel not in completed.stderr
    assert str(path) not in completed.stderr
    assert not receipt.exists()


def test_tool_has_no_network_or_browser_automation_imports() -> None:
    source = Path(exercise.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported_roots.isdisjoint(
        {
            "aiohttp",
            "httpx",
            "playwright",
            "requests",
            "selenium",
            "socket",
            "urllib",
        }
    )


def test_prohibited_files_remain_at_exact_merged_hashes() -> None:
    expected = {
        "tools/verify_session_prep_ui_evidence.py": (
            "ce4088c9917ffae9a12f347bb8e81902a694c3040ae836f8d49894e69fd69d46"
        ),
        "tools/reconcile_session_prep_source_candidate.py": (
            "19d280e59f7c32fab1b4a9a0a7f9e5193d0e5dbf05171dbd07cc28b198a448a8"
        ),
        "tests/test_session_prep_source_candidate.py": (
            "20a0abb0353a695f706ea265a6da0ee8f46b7048e52a0cc054da0dbb9bb9a4d7"
        ),
        "docs/flight-plans/session-prep-staging-source-candidate.md": (
            "70e0b828e237c6fd2538907363fb25d16f71368ca0fb7a98449361f1531e37d1"
        ),
    }
    for relative, digest in expected.items():
        raw = (exercise.ROOT / relative).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest
