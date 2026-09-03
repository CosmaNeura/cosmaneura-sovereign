"""Fail-closed tests for the synthetic Session Prep UI/UX receipt."""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import verify_session_prep_ui_evidence as evidence


OBSERVED_AT = "2026-09-03T20:05:00Z"
EXERCISE_ID = "uxrun_0123456789abcdef0123456789abcdef"
CHALLENGE = "uxchallenge_fedcba9876543210fedcba9876543210"
RUNBOOK = evidence.ROOT / "docs/flight-plans/session-prep-synthetic-ui-ux-exercise.md"


def _receipt() -> dict[str, Any]:
    document = evidence._expected_evidence(
        OBSERVED_AT,
        EXERCISE_ID,
        CHALLENGE,
        "chromium",
        140,
        "voiceover",
    )
    return {
        "evidence": document,
        "evidence_sha256": evidence._digest(document),
    }


def _reseal(receipt: dict[str, Any]) -> None:
    receipt["evidence_sha256"] = evidence._digest(receipt["evidence"])


def _write_receipt(tmp_path: Path, receipt: object) -> Path:
    path = tmp_path / "synthetic-ui-evidence.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path


def _set_nested(receipt: dict[str, Any], path: tuple[str, ...], value: object) -> None:
    target: dict[str, Any] = receipt
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        evidence,
        "_utc_now",
        lambda: dt.datetime(2026, 9, 3, 20, 6, tzinfo=dt.UTC),
    )


def test_exact_reviewed_commit_tree_and_source_manifest_are_pinned() -> None:
    tree = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(evidence.ROOT),
            "rev-parse",
            "--verify",
            f"{evidence.SOVEREIGN_COMMIT}^{{tree}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert evidence.SOVEREIGN_COMMIT == ("e0736289f36b200a3daee59db3732482bab6fc5d")
    assert tree.stdout.strip() == evidence.SOVEREIGN_TREE
    assert evidence._digest(evidence.SOURCE_MANIFEST) == (
        evidence.SOURCE_MANIFEST_SHA256
    )
    assets = evidence.verify_source_binding(evidence.ROOT)
    assert set(assets) == set(evidence.SOURCE_MANIFEST)
    assert evidence.SOURCE_MANIFEST[evidence.DEMO_ASSET_PATH] == (
        evidence.DEMO_ASSET_SHA256
    )


def test_valid_receipt_is_a_closed_source_bound_operator_attestation(
    tmp_path: Path,
) -> None:
    receipt = _receipt()
    path = _write_receipt(tmp_path, receipt)

    assert evidence.load_receipt(path) == receipt
    evidence.validate_receipt(receipt)
    evidence.verify(path, evidence.ROOT)

    document = receipt["evidence"]
    assert set(document["manual_observations"]["states"]) == {
        "ready",
        "refreshing",
        "pending",
        "unavailable",
    }
    assert {
        viewport["width_css_px"]
        for viewport in document["manual_observations"]["viewports"].values()
    } == {390, 1440}
    assert set(document["manual_observations"]["accessibility"]) == {
        "keyboard",
        "screen_reader",
    }
    assert set(document["automated_support"]["transitions"]) == {
        "record_switch_clear_and_isolation",
        "encounter_switch_clear_and_isolation",
        "late_response_suppression",
        "revocation_and_reset",
    }
    assert set(document["manual_observations"]["host_controls"]) == {
        "viewports",
        "record_switch",
        "revocation",
        "reset",
    }


def test_runbook_json_specimen_is_intentionally_rejected() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    matches = re.findall(r"```json\n(.*?)\n```", runbook, flags=re.DOTALL)
    assert len(matches) == 1
    receipt = json.loads(matches[0])
    with pytest.raises(evidence.EvidenceError):
        evidence.validate_receipt(receipt)


@pytest.mark.parametrize(
    ("container_path", "field"),
    [
        ((), "notes"),
        (("evidence",), "operator_name"),
        (("evidence", "scope"), "provider_identifier"),
        (("evidence", "source"), "record_id"),
        (("evidence", "manual_observations"), "clinical_summary"),
        (
            ("evidence", "manual_observations", "states", "ready"),
            "patient_name",
        ),
        (("evidence", "automated_support", "privacy"), "raw_note"),
        (("evidence", "readiness_claims"), "free_form_comment"),
    ],
)
def test_unknown_or_free_form_fields_are_rejected(
    container_path: tuple[str, ...], field: str
) -> None:
    receipt = _receipt()
    container: dict[str, Any] = receipt
    for key in container_path:
        container = container[key]
    container[field] = "SYNTHETIC-SENTINEL clinical free text"
    _reseal(receipt)

    with pytest.raises(evidence.EvidenceError):
        evidence.validate_receipt(receipt)


@pytest.mark.parametrize(
    "path",
    [
        (
            "evidence",
            "manual_observations",
            "states",
            "ready",
            "heading_and_status",
        ),
        (
            "evidence",
            "manual_observations",
            "states",
            "refreshing",
            "returned_brief_only",
        ),
        (
            "evidence",
            "manual_observations",
            "states",
            "pending",
            "prior_brief_cleared",
        ),
        (
            "evidence",
            "manual_observations",
            "states",
            "unavailable",
            "prior_brief_cleared",
        ),
        (
            "evidence",
            "manual_observations",
            "viewports",
            "narrow",
            "layout_readable",
        ),
        (
            "evidence",
            "manual_observations",
            "viewports",
            "wide",
            "controls_visible_and_operable",
        ),
        (
            "evidence",
            "manual_observations",
            "accessibility",
            "keyboard",
            "visible_focus",
        ),
        (
            "evidence",
            "manual_observations",
            "accessibility",
            "screen_reader",
            "state_changes_announced",
        ),
        (
            "evidence",
            "manual_observations",
            "host_controls",
            "record_switch",
            "prior_trace_cleared",
        ),
        (
            "evidence",
            "manual_observations",
            "host_controls",
            "revocation",
            "content_and_trace_cleared",
        ),
        (
            "evidence",
            "manual_observations",
            "host_controls",
            "reset",
            "typed_and_switch_controls_restored",
        ),
        (
            "evidence",
            "automated_support",
            "transitions",
            "record_switch_clear_and_isolation",
        ),
        (
            "evidence",
            "automated_support",
            "transitions",
            "encounter_switch_clear_and_isolation",
        ),
        (
            "evidence",
            "automated_support",
            "transitions",
            "late_response_suppression",
        ),
        (
            "evidence",
            "automated_support",
            "transitions",
            "revocation_and_reset",
        ),
        (
            "evidence",
            "automated_support",
            "privacy",
            "local_storage_unused",
        ),
    ],
)
@pytest.mark.parametrize("non_pass", ["fail", "not-run", "partial", True])
def test_every_required_observation_is_pass_only(
    path: tuple[str, ...], non_pass: object
) -> None:
    receipt = _receipt()
    _set_nested(receipt, path, non_pass)
    _reseal(receipt)

    with pytest.raises(evidence.EvidenceError):
        evidence.validate_receipt(receipt)


@pytest.mark.parametrize(
    "claim",
    [
        "staging_ready",
        "auth_iam_ready",
        "durable_operational_controls_ready",
        "real_provider_ready",
        "production_ready",
        "phi_ready",
    ],
)
def test_readiness_claims_cannot_be_promoted(claim: str) -> None:
    receipt = _receipt()
    receipt["evidence"]["readiness_claims"][claim] = True
    _reseal(receipt)

    with pytest.raises(evidence.EvidenceError):
        evidence.validate_receipt(receipt)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("evidence", "scope", "live_service_contacted"), True),
        (("evidence", "scope", "network_scope"), "staging"),
        (("evidence", "scope", "data_class"), "phi"),
    ],
)
def test_scope_cannot_claim_live_or_real_data(
    path: tuple[str, ...], value: object
) -> None:
    receipt = _receipt()
    _set_nested(receipt, path, value)
    _reseal(receipt)

    with pytest.raises(evidence.EvidenceError):
        evidence.validate_receipt(receipt)


def test_canonical_digest_detects_permitted_metadata_mutation() -> None:
    receipt = _receipt()
    receipt["evidence"]["environment"]["browser_major"] = 141

    with pytest.raises(evidence.EvidenceError):
        evidence.validate_receipt(receipt)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("exercise_id", "uxrun_" + "A" * 32),
        ("exercise_id", "uxrun_" + "1" * 31),
        ("exercise_id", "record_synthetic_patient"),
        ("challenge", "uxchallenge_" + "A" * 32),
        ("challenge", "uxchallenge_" + "1" * 33),
        ("challenge", "free-form challenge"),
    ],
)
def test_run_identity_and_challenge_are_closed_bounded_hex(
    field: str, value: str
) -> None:
    receipt = _receipt()
    receipt["evidence"][field] = value
    _reseal(receipt)

    with pytest.raises(evidence.EvidenceError):
        evidence.validate_receipt(receipt)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("evidence", "source", "sovereign_commit"), "A" * 40),
        (("evidence", "source", "sovereign_commit"), "1" * 39),
        (("evidence", "source", "sovereign_commit"), "main"),
        (("evidence", "source", "demo_asset_sha256"), "d1c34827"),
        (
            ("evidence", "source", "demo_asset_sha256"),
            "sha256:" + "A" * 64,
        ),
        (
            ("evidence", "source", "source_manifest_sha256"),
            "sha256:" + "1" * 63,
        ),
        (("evidence_sha256",), "sha256:" + "A" * 64),
    ],
)
def test_malformed_or_wrong_sha_values_are_rejected(
    path: tuple[str, ...], value: str
) -> None:
    receipt = _receipt()
    _set_nested(receipt, path, value)

    with pytest.raises(evidence.EvidenceError):
        evidence.validate_receipt(receipt)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("evidence", "scope", "manual_observation_completed"), 1),
        (("evidence", "scope", "live_service_contacted"), 0),
        (
            (
                "evidence",
                "manual_observations",
                "viewports",
                "narrow",
                "width_css_px",
            ),
            390.0,
        ),
        (("evidence", "environment", "browser_major"), True),
        (("evidence", "readiness_claims", "phi_ready"), 0),
    ],
)
def test_json_scalar_types_cannot_smuggle_equivalent_values(
    path: tuple[str, ...], value: object
) -> None:
    receipt = _receipt()
    _set_nested(receipt, path, value)
    _reseal(receipt)

    with pytest.raises(evidence.EvidenceError):
        evidence.validate_receipt(receipt)


@pytest.mark.parametrize(
    ("observed_at", "now"),
    [
        ("2026-09-03T20:05:00.000Z", "2026-09-03T20:06:00Z"),
        ("2026-09-03T16:05:00-04:00", "2026-09-03T20:06:00Z"),
        ("2026-09-03 20:05:00Z", "2026-09-03T20:06:00Z"),
        ("2026-09-03T20:03:12Z", "2026-09-03T20:06:00Z"),
        ("2026-09-03T20:06:01Z", "2026-09-03T20:06:00Z"),
        ("2026-09-03T20:05:00Z", "2026-09-04T20:05:01Z"),
    ],
)
def test_timestamp_must_be_canonical_post_source_fresh_and_not_future(
    observed_at: str,
    now: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _receipt()
    receipt["evidence"]["observed_at_utc"] = observed_at
    _reseal(receipt)
    parsed_now = dt.datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
    monkeypatch.setattr(evidence, "_utc_now", lambda: parsed_now)

    with pytest.raises(evidence.EvidenceError):
        evidence.validate_receipt(receipt)


def test_timestamp_accepts_exact_now_and_exact_max_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for now in (
        dt.datetime(2026, 9, 3, 20, 5, tzinfo=dt.UTC),
        dt.datetime(2026, 9, 4, 20, 5, tzinfo=dt.UTC),
    ):
        receipt = _receipt()
        monkeypatch.setattr(evidence, "_utc_now", lambda now=now: now)
        evidence.validate_receipt(receipt)


@pytest.mark.parametrize("engine", ["chrome", "Chromium", "safari", "synthetic"])
def test_browser_engine_is_a_closed_enum(engine: str) -> None:
    receipt = _receipt()
    receipt["evidence"]["environment"]["browser_engine"] = engine
    _reseal(receipt)

    with pytest.raises(evidence.EvidenceError):
        evidence.validate_receipt(receipt)


@pytest.mark.parametrize("screen_reader", ["none", "VoiceOver", "custom", ""])
def test_screen_reader_is_a_closed_enum(screen_reader: str) -> None:
    receipt = _receipt()
    receipt["evidence"]["environment"]["screen_reader"] = screen_reader
    _reseal(receipt)

    with pytest.raises(evidence.EvidenceError):
        evidence.validate_receipt(receipt)


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b"{not-json}",
        b'\xff\xfe{"schema":"bad"}',
        b"\xef\xbb\xbf{}",
        b'{"schema":NaN}',
        b'{"schema":Infinity}',
        b'{"schema":"one","schema":"two"}',
        b"{} trailing",
        (b'{"a":' * 1200) + b"null" + (b"}" * 1200),
    ],
)
def test_parser_rejects_ambiguous_or_non_object_json(
    tmp_path: Path, raw: bytes
) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(raw)

    with pytest.raises(evidence.EvidenceError):
        evidence.load_receipt(path)


def test_parser_reads_at_most_the_bounded_receipt_size(tmp_path: Path) -> None:
    path = tmp_path / "oversized.json"
    path.write_bytes(b" " * (evidence.MAX_RECEIPT_BYTES + 1))

    with pytest.raises(evidence.EvidenceError):
        evidence.load_receipt(path)


def test_parser_rejects_symlink(tmp_path: Path) -> None:
    target = _write_receipt(tmp_path, _receipt())
    symlink = tmp_path / "receipt-link.json"
    symlink.symlink_to(target)

    with pytest.raises(evidence.EvidenceError):
        evidence.load_receipt(symlink)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unavailable")
def test_parser_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "receipt.fifo"
    os.mkfifo(fifo)

    with pytest.raises(evidence.EvidenceError):
        evidence.load_receipt(fifo)


def test_source_binding_uses_local_non_lazy_exact_git_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    asset = b"exact synthetic demo bytes"
    manifest = {evidence.DEMO_ASSET_PATH: "sha256:" + hashlib.sha256(asset).hexdigest()}
    monkeypatch.setattr(evidence, "SOURCE_MANIFEST", manifest)
    monkeypatch.setattr(evidence, "SOURCE_MANIFEST_SHA256", evidence._digest(manifest))

    def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((command, kwargs["env"]))
        operation = command[-3] if command[-3] == "rev-parse" else command[-2]
        if operation == "rev-parse" and command[-1].endswith("^{commit}"):
            stdout = f"{evidence.SOVEREIGN_COMMIT}\n".encode("ascii")
        elif operation == "rev-parse":
            stdout = f"{evidence.SOVEREIGN_TREE}\n".encode("ascii")
        else:
            stdout = asset
        return SimpleNamespace(returncode=0, stdout=stdout)

    monkeypatch.setattr(evidence.subprocess, "run", fake_run)
    assets = evidence.verify_source_binding(Path("/synthetic/local/repo"))

    assert assets == {evidence.DEMO_ASSET_PATH: asset}
    assert len(calls) == 3
    assert calls[0][0][:2] == ["git", "--no-replace-objects"]
    assert calls[0][0][-3:] == [
        "rev-parse",
        "--verify",
        f"{evidence.SOVEREIGN_COMMIT}^{{commit}}",
    ]
    assert calls[1][0][-3:] == [
        "rev-parse",
        "--verify",
        f"{evidence.SOVEREIGN_COMMIT}^{{tree}}",
    ]
    assert calls[2][0][-2:] == [
        "show",
        f"{evidence.SOVEREIGN_COMMIT}:{evidence.DEMO_ASSET_PATH}",
    ]
    for _command, environment in calls:
        assert environment["GIT_NO_LAZY_FETCH"] == "1"
        assert environment["GIT_OPTIONAL_LOCKS"] == "0"
        assert environment["GIT_TERMINAL_PROMPT"] == "0"


def test_harnesses_receive_only_verified_temporary_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], Path]] = []
    assets = {path: path.encode("ascii") for path in evidence.SOURCE_MANIFEST}
    monkeypatch.setattr(evidence.shutil, "which", lambda _name: "/usr/bin/node")

    def fake_harness(command: list[str], cwd: Path) -> None:
        calls.append((command, cwd))
        assert all(Path(item).is_file() for item in command[1:])

    monkeypatch.setattr(evidence, "_run_harness", fake_harness)
    evidence.verify_automated_support(assets)

    assert len(calls) == 2
    assert all(command[0] == "/usr/bin/node" for command, _cwd in calls)
    assert calls[0][0][1].endswith("session_prep_demo_ui_harness.js")
    assert calls[1][0][1].endswith("panel_contract_harness.js")


def test_invalid_receipt_stops_before_git_and_output_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = "SYNTHETIC-SENSITIVE-SENTINEL"
    receipt = _receipt()
    receipt["evidence"]["clinical_note"] = sentinel
    _reseal(receipt)
    path = _write_receipt(tmp_path, receipt)
    monkeypatch.setattr(
        evidence,
        "verify_source_binding",
        lambda _repo: pytest.fail("invalid evidence reached Git verification"),
    )

    assert evidence.main([str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == evidence.FAIL_SUMMARY
    assert sentinel not in captured.err
    assert str(path) not in captured.err
    assert len(captured.err) < 160


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["receipt.json", "SENSITIVE_EXTRA_ARGUMENT"],
        ["--SENSITIVE_UNKNOWN_FLAG", "receipt.json"],
    ],
)
def test_invalid_cli_invocation_has_one_sanitized_bounded_failure(
    arguments: list[str],
) -> None:
    tool = evidence.ROOT / "tools/verify_session_prep_ui_evidence.py"
    completed = subprocess.run(
        [sys.executable, str(tool), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr.strip() == evidence.FAIL_SUMMARY
    assert "SENSITIVE" not in completed.stderr
    assert len(completed.stderr) < 160


def test_invalid_input_subprocess_never_echoes_content_or_path(tmp_path: Path) -> None:
    sentinel = "SENSITIVE_INPUT_SENTINEL"
    path = tmp_path / f"{sentinel}.json"
    path.write_text(json.dumps({"clinical_note": sentinel}), encoding="utf-8")
    tool = evidence.ROOT / "tools/verify_session_prep_ui_evidence.py"

    completed = subprocess.run(
        [sys.executable, str(tool), str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr.strip() == evidence.FAIL_SUMMARY
    assert sentinel not in completed.stderr
    assert str(path) not in completed.stderr
    assert len(completed.stderr) < 160


def test_success_output_is_one_bounded_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write_receipt(tmp_path, _receipt())

    assert evidence.main([str(path), "--sovereign-repo", str(evidence.ROOT)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.strip() == evidence.PASS_SUMMARY
    assert len(captured.out) < 180


def test_verifier_has_no_network_client_imports() -> None:
    source = Path(evidence.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported_roots.isdisjoint(
        {"aiohttp", "httpx", "requests", "socket", "urllib"}
    )
