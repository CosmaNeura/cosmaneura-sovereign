"""Fail-closed tests for the synthetic Session Prep UI exercise worksheet."""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import os
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
    assert runbook.count("--demo-repo /absolute/path/to/session-prep-ui-source") == 2
    assert "Do not bulk-promote checks" in runbook
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
