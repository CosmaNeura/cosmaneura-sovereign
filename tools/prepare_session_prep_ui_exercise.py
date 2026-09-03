#!/usr/bin/env python3
"""Prepare and seal a closed synthetic Session Prep UI exercise worksheet.

The two-stage workflow deliberately starts every manual observation at
``not-run``.  Sealing succeeds only after every closed observation is
explicitly ``pass`` and the unchanged pinned UI evidence verifier accepts the
resulting receipt and its exact offline harnesses.

This tool does not open a browser, contact a service, accept free-form text,
or prove that a human performed the observations.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn, Sequence


ROOT = Path(__file__).resolve().parents[1]
WORKSHEET_SCHEMA = "cosmaneura.session-prep.synthetic-ui-ux-worksheet.v1"
RECEIPT_SCHEMA = "cosmaneura.session-prep.synthetic-ui-ux-evidence.v1"
MAX_WORKSHEET_BYTES = 16 * 1024
MAX_RECEIPT_BYTES = 16 * 1024
MAX_VERIFIER_BYTES = 64 * 1024
MAX_GIT_TREE_BYTES = 256 * 1024
MAX_TRACKED_FILE_BYTES = 1024 * 1024
MAX_TRACKED_TREE_BYTES = 8 * 1024 * 1024
MAX_TRACKED_PATHS = 4096
MAX_JSON_DEPTH = 8
MAX_WORKSHEET_AGE = dt.timedelta(hours=24)
SOURCE_COMMIT_TIME = dt.datetime(2026, 9, 3, 20, 3, 13, tzinfo=dt.UTC)

UI_SOURCE_COMMIT = "e0736289f36b200a3daee59db3732482bab6fc5d"
UI_SOURCE_TREE = "3391bc15f8a110ccac55d39668ba9f6c4eb9c175"
UI_VERIFIER_COMMIT = "13ad964cb7d40b75c1209be431dcde3698ad6021"
UI_VERIFIER_TREE = "852a7fbb0a998035485c8b480de9c2a703796512"
UI_VERIFIER_PATH = "tools/verify_session_prep_ui_evidence.py"
UI_VERIFIER_SHA256 = "ce4088c9917ffae9a12f347bb8e81902a694c3040ae836f8d49894e69fd69d46"
UI_VERIFIER_PASS_OUTPUT = (
    b"[PASS] receipt structure and pinned automated support validated; "
    b"the synthetic UI operator attestation remains unproven.\n"
)

DEMO_ASSET_PATH = "tools/session_prep_demo.html"
DEMO_ASSET_SHA256 = (
    "sha256:f7b0c7f5cda44cb4a8a38e963c767c8d19da8d09aa351b2f54e80535c3a052c7"
)
SOURCE_MANIFEST_SHA256 = (
    "sha256:8bcae9ab57f5b2d301e4d8b2e2ffdbfb373d6f7c556a84b76ad2686bd59f8152"
)

TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
EXERCISE_ID_PATTERN = re.compile(r"uxrun_[0-9a-f]{32}\Z")
CHALLENGE_PATTERN = re.compile(r"uxchallenge_[0-9a-f]{32}\Z")
BROWSER_ENGINES = frozenset({"chromium", "firefox", "webkit"})
SCREEN_READERS = frozenset({"jaws", "nvda", "orca", "talkback", "voiceover"})

MANUAL_CHECK_PATHS: tuple[tuple[str, ...], ...] = (
    ("states", "ready", "heading_and_status"),
    ("states", "ready", "bounded_brief_readable"),
    ("states", "ready", "source_count_understandable"),
    ("states", "refreshing", "heading_and_status"),
    ("states", "refreshing", "stale_label_unambiguous"),
    ("states", "refreshing", "returned_brief_only"),
    ("states", "refreshing", "refresh_does_not_claim_freshness"),
    ("states", "pending", "heading_and_status"),
    ("states", "pending", "prior_brief_cleared"),
    ("states", "pending", "waiting_state_understandable"),
    ("states", "unavailable", "heading_and_status"),
    ("states", "unavailable", "prior_brief_cleared"),
    ("states", "unavailable", "distinct_from_error_or_empty_ready"),
    ("viewports", "narrow", "layout_readable"),
    ("viewports", "narrow", "controls_visible_and_operable"),
    ("viewports", "narrow", "no_horizontal_overflow"),
    ("viewports", "wide", "layout_readable"),
    ("viewports", "wide", "controls_visible_and_operable"),
    ("viewports", "wide", "no_horizontal_overflow"),
    ("accessibility", "keyboard", "logical_focus_order"),
    ("accessibility", "keyboard", "visible_focus"),
    ("accessibility", "keyboard", "all_actions_operable"),
    ("accessibility", "keyboard", "focus_preserved_or_rehomed"),
    ("accessibility", "screen_reader", "headings_and_controls_named"),
    ("accessibility", "screen_reader", "state_changes_announced"),
    ("accessibility", "screen_reader", "busy_state_announced"),
    ("visual_privacy", "bounded_typed_projection_only"),
    ("visual_privacy", "raw_source_and_internal_refs_absent"),
    ("host_controls", "record_switch", "prior_content_cleared"),
    ("host_controls", "record_switch", "prior_trace_cleared"),
    ("host_controls", "record_switch", "transition_status_announced"),
    ("host_controls", "record_switch", "controls_disabled_while_switching"),
    ("host_controls", "record_switch", "focus_preserved_or_rehomed"),
    ("host_controls", "record_switch", "new_record_state_only"),
    ("host_controls", "revocation", "content_and_trace_cleared"),
    ("host_controls", "revocation", "closed_status_announced"),
    ("host_controls", "revocation", "typed_and_switch_controls_disabled"),
    ("host_controls", "revocation", "reset_control_enabled"),
    ("host_controls", "revocation", "focus_preserved_or_rehomed"),
    ("host_controls", "reset", "local_only_status_announced"),
    ("host_controls", "reset", "content_and_trace_remain_clear"),
    ("host_controls", "reset", "typed_and_switch_controls_restored"),
    ("host_controls", "reset", "reset_control_disabled"),
    ("host_controls", "reset", "focus_preserved_or_rehomed"),
)
MANUAL_CHECK_IDS = tuple(".".join(path) for path in MANUAL_CHECK_PATHS)

INIT_PASS_SUMMARY = (
    "[PASS] synthetic-only Session Prep UI worksheet initialized; "
    "all 44 manual checks remain not-run."
)
SEAL_PASS_SUMMARY = (
    "[PASS] synthetic-only Session Prep UI receipt sealed; pinned automated "
    "support validated and the operator attestation remains unproven."
)
FAIL_SUMMARY = "[FAIL] synthetic-only Session Prep UI worksheet rejected."


class ExerciseError(RuntimeError):
    """The closed worksheet, receipt, or pinned verifier failed closed."""


def _fail(message: str) -> NoReturn:
    raise ExerciseError(message)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _canonical_timestamp(value: dt.datetime) -> str:
    return (
        value.astimezone(dt.UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _parse_timestamp(value: Any) -> dt.datetime:
    if type(value) is not str or TIMESTAMP_PATTERN.fullmatch(value) is None:
        _fail("The worksheet timestamp is not canonical UTC seconds")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
    except ValueError:
        _fail("The worksheet timestamp is invalid")


def _reject_json_constant(_value: str) -> NoReturn:
    _fail("Non-finite JSON numbers are not allowed")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("Duplicate JSON object keys are not allowed")
        result[key] = value
    return result


def _reject_excessive_depth(value: Any) -> None:
    pending: list[tuple[Any, int]] = [(value, 1)]
    while pending:
        candidate, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            _fail("The worksheet exceeds the maximum JSON depth")
        if isinstance(candidate, dict):
            pending.extend((item, depth + 1) for item in candidate.values())
        elif isinstance(candidate, list):
            pending.extend((item, depth + 1) for item in candidate)


def _read_bounded_regular(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow == 0:
        try:
            if path.is_symlink():
                _fail("The input must not be a symbolic link")
        except OSError:
            _fail("The input could not be inspected")
    try:
        descriptor = os.open(path, flags | no_follow)
    except OSError:
        _fail("The input could not be read")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail("The input must be a regular file")
        if before.st_size > maximum:
            _fail("The input exceeds the maximum size")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_identity != after_identity or len(raw) != after.st_size:
            _fail("The input changed while it was being read")
    except OSError:
        _fail("The input could not be read")
    finally:
        os.close(descriptor)
    if len(raw) > maximum:
        _fail("The input exceeds the maximum size")
    return raw


def _parse_closed_json(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except ExerciseError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        _fail("The worksheet is not valid bounded JSON")
    _reject_excessive_depth(parsed)
    if type(parsed) is not dict:
        _fail("The worksheet must be one JSON object")
    return parsed


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _write_new_private(path: Path, raw: bytes, maximum: int) -> None:
    if len(raw) > maximum:
        _fail("The output exceeds the maximum size")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        opened = os.fstat(descriptor)
        identity = (opened.st_dev, opened.st_ino)
        if not stat.S_ISREG(opened.st_mode):
            _fail("The output is not a regular file")
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                _fail("The output could not be written")
            offset += written
        os.fsync(descriptor)
        completed = os.fstat(descriptor)
        if completed.st_size != len(raw) or stat.S_IMODE(completed.st_mode) != 0o600:
            _fail("The output could not be finalized privately")
    except ExerciseError:
        raise
    except OSError:
        _fail("The output could not be created")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if sys.exc_info()[0] is not None and identity is not None:
            try:
                current = os.lstat(path)
                if (current.st_dev, current.st_ino) == identity:
                    os.unlink(path)
            except OSError:
                pass


def _reject_path_within_demo_repo(path: Path, demo_repo: Path) -> None:
    try:
        resolved_repo = demo_repo.resolve(strict=True)
        resolved_path = path.resolve(strict=False)
        resolved_path.relative_to(resolved_repo)
    except ValueError:
        return
    except OSError:
        _fail("The local paths could not be resolved")
    _fail("Evidence files must remain outside the demo checkout")


def _expected_worksheet(
    started_at_utc: str,
    exercise_id: str,
    challenge: str,
    browser_engine: str,
    browser_major: int,
    screen_reader: str,
    status: str,
) -> dict[str, Any]:
    return {
        "schema": WORKSHEET_SCHEMA,
        "started_at_utc": started_at_utc,
        "exercise_id": exercise_id,
        "challenge": challenge,
        "environment": {
            "browser_engine": browser_engine,
            "browser_major": browser_major,
            "screen_reader": screen_reader,
        },
        "scope": {
            "surface": "bundled-loopback-demo",
            "data_class": "synthetic-only",
            "network_scope": "loopback-only",
            "live_service_contacted": False,
            "real_provider_data_used": False,
            "phi_used": False,
        },
        "observed_source": {
            "sovereign_commit": UI_SOURCE_COMMIT,
            "sovereign_tree": UI_SOURCE_TREE,
            "checkout_state": "detached-clean",
        },
        "manual_checks": dict.fromkeys(MANUAL_CHECK_IDS, status),
    }


def _assert_exact(actual: Any, expected: Any) -> None:
    if type(actual) is not type(expected):
        _fail("The worksheet contains an unexpected value type")
    if isinstance(expected, dict):
        if set(actual) != set(expected):
            _fail("The worksheet contains missing or unknown fields")
        for key, expected_value in expected.items():
            _assert_exact(actual[key], expected_value)
        return
    if actual != expected:
        _fail("The worksheet contains an unapproved value")


def _validate_environment(
    browser_engine: Any,
    browser_major: Any,
    screen_reader: Any,
) -> tuple[str, int, str]:
    if type(browser_engine) is not str or browser_engine not in BROWSER_ENGINES:
        _fail("The browser engine is unsupported")
    if type(browser_major) is not int or not 1 <= browser_major <= 999:
        _fail("The browser major version is invalid")
    if type(screen_reader) is not str or screen_reader not in SCREEN_READERS:
        _fail("The screen reader is unsupported")
    return browser_engine, browser_major, screen_reader


def _validate_run_tokens(exercise_id: Any, challenge: Any) -> tuple[str, str]:
    if (
        type(exercise_id) is not str
        or EXERCISE_ID_PATTERN.fullmatch(exercise_id) is None
    ):
        _fail("The exercise identifier is invalid")
    if type(challenge) is not str or CHALLENGE_PATTERN.fullmatch(challenge) is None:
        _fail("The exercise challenge is invalid")
    return exercise_id, challenge


def _validate_started_at(started_at_utc: Any, now: dt.datetime) -> str:
    started_at = _parse_timestamp(started_at_utc)
    if started_at <= SOURCE_COMMIT_TIME:
        _fail("The worksheet does not follow the pinned source")
    if started_at > now:
        _fail("The worksheet timestamp is in the future")
    if now - started_at > MAX_WORKSHEET_AGE:
        _fail("The worksheet is stale")
    return started_at_utc


def load_completed_worksheet(path: Path, now: dt.datetime) -> dict[str, Any]:
    raw = _read_bounded_regular(path, MAX_WORKSHEET_BYTES)
    worksheet = _parse_closed_json(raw)
    environment = worksheet.get("environment")
    if type(environment) is not dict:
        _fail("The worksheet browser environment is invalid")
    browser_engine, browser_major, screen_reader = _validate_environment(
        environment.get("browser_engine"),
        environment.get("browser_major"),
        environment.get("screen_reader"),
    )
    exercise_id, challenge = _validate_run_tokens(
        worksheet.get("exercise_id"), worksheet.get("challenge")
    )
    started_at_utc = _validate_started_at(worksheet.get("started_at_utc"), now)
    expected = _expected_worksheet(
        started_at_utc,
        exercise_id,
        challenge,
        browser_engine,
        browser_major,
        screen_reader,
        "pass",
    )
    _assert_exact(worksheet, expected)
    return worksheet


def _manual_observations() -> dict[str, Any]:
    return {
        "states": {
            "ready": {
                "response_state": "ready",
                "heading_and_status": "pass",
                "bounded_brief_readable": "pass",
                "source_count_understandable": "pass",
            },
            "refreshing": {
                "response_state": "refreshing",
                "heading_and_status": "pass",
                "stale_label_unambiguous": "pass",
                "returned_brief_only": "pass",
                "refresh_does_not_claim_freshness": "pass",
            },
            "pending": {
                "response_state": "pending",
                "heading_and_status": "pass",
                "prior_brief_cleared": "pass",
                "waiting_state_understandable": "pass",
            },
            "unavailable": {
                "response_state": "unavailable",
                "heading_and_status": "pass",
                "prior_brief_cleared": "pass",
                "distinct_from_error_or_empty_ready": "pass",
            },
        },
        "viewports": {
            "narrow": {
                "width_css_px": 390,
                "height_css_px": 844,
                "zoom_percent": 100,
                "layout_readable": "pass",
                "controls_visible_and_operable": "pass",
                "no_horizontal_overflow": "pass",
            },
            "wide": {
                "width_css_px": 1440,
                "height_css_px": 900,
                "zoom_percent": 100,
                "layout_readable": "pass",
                "controls_visible_and_operable": "pass",
                "no_horizontal_overflow": "pass",
            },
        },
        "accessibility": {
            "keyboard": {
                "logical_focus_order": "pass",
                "visible_focus": "pass",
                "all_actions_operable": "pass",
                "focus_preserved_or_rehomed": "pass",
            },
            "screen_reader": {
                "headings_and_controls_named": "pass",
                "state_changes_announced": "pass",
                "busy_state_announced": "pass",
            },
        },
        "visual_privacy": {
            "bounded_typed_projection_only": "pass",
            "raw_source_and_internal_refs_absent": "pass",
        },
        "host_controls": {
            "viewports": "narrow-and-wide",
            "record_switch": {
                "prior_content_cleared": "pass",
                "prior_trace_cleared": "pass",
                "transition_status_announced": "pass",
                "controls_disabled_while_switching": "pass",
                "focus_preserved_or_rehomed": "pass",
                "new_record_state_only": "pass",
            },
            "revocation": {
                "content_and_trace_cleared": "pass",
                "closed_status_announced": "pass",
                "typed_and_switch_controls_disabled": "pass",
                "reset_control_enabled": "pass",
                "focus_preserved_or_rehomed": "pass",
            },
            "reset": {
                "local_only_status_announced": "pass",
                "content_and_trace_remain_clear": "pass",
                "typed_and_switch_controls_restored": "pass",
                "reset_control_disabled": "pass",
                "focus_preserved_or_rehomed": "pass",
            },
        },
    }


def _build_receipt(worksheet: dict[str, Any], observed_at_utc: str) -> dict[str, Any]:
    environment = worksheet["environment"]
    evidence = {
        "schema": RECEIPT_SCHEMA,
        "observed_at_utc": observed_at_utc,
        "exercise_id": worksheet["exercise_id"],
        "challenge": worksheet["challenge"],
        "environment": dict(environment),
        "scope": {
            "surface": "bundled-loopback-demo",
            "data_class": "synthetic-only",
            "manual_observation_completed": True,
            "network_scope": "loopback-only",
            "live_service_contacted": False,
        },
        "source": {
            "sovereign_commit": UI_SOURCE_COMMIT,
            "sovereign_tree": UI_SOURCE_TREE,
            "demo_asset_path": DEMO_ASSET_PATH,
            "demo_asset_sha256": DEMO_ASSET_SHA256,
            "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        },
        "manual_observations": _manual_observations(),
        "automated_support": {
            "harnesses": {
                "demo_controller": "pass",
                "panel_contract": "pass",
            },
            "transitions": {
                "record_switch_clear_and_isolation": "pass",
                "encounter_switch_clear_and_isolation": "pass",
                "late_response_suppression": "pass",
                "revocation_and_reset": "pass",
            },
            "privacy": {
                "session_prep_body_client_turn_only": "pass",
                "session_prep_handle_absent_from_url_and_generic_chat": "pass",
                "dom_has_no_handle_raw_source_or_internal_ref": "pass",
                "local_storage_unused": "pass",
                "session_storage_unused": "pass",
                "indexed_db_unused": "pass",
                "cache_storage_unused": "pass",
                "demo_requests_loopback_only": "pass",
            },
        },
        "readiness_claims": {
            "staging_ready": False,
            "auth_iam_ready": False,
            "durable_operational_controls_ready": False,
            "real_provider_ready": False,
            "production_ready": False,
            "phi_ready": False,
        },
    }
    return {"evidence": evidence, "evidence_sha256": _digest(evidence)}


def _git_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _run_git(repo: Path, arguments: Sequence[str], maximum: int) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "--no-replace-objects", "-C", os.fspath(repo), *arguments],
            check=False,
            capture_output=True,
            timeout=15,
            env=_git_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        _fail("The pinned verifier Git object could not be read")
    if completed.returncode != 0 or len(completed.stdout) > maximum:
        _fail("The pinned verifier Git object could not be read")
    return completed.stdout


def _nul_records(raw: bytes) -> list[bytes]:
    if not raw.endswith(b"\0"):
        _fail("The demo checkout index is invalid")
    records = raw[:-1].split(b"\0")
    if (
        not records
        or len(records) > MAX_TRACKED_PATHS
        or any(not item for item in records)
    ):
        _fail("The demo checkout index is invalid")
    return records


def _git_blob_oid(raw: bytes) -> bytes:
    header = f"blob {len(raw)}\0".encode("ascii")
    return hashlib.sha1(header + raw, usedforsecurity=False).hexdigest().encode("ascii")


def _verify_tracked_worktree(top_level: Path) -> None:
    object_format = _run_git(top_level, ["rev-parse", "--show-object-format"], 32)
    if object_format != b"sha1\n":
        _fail("The demo checkout object format is unsupported")

    index_raw = _run_git(
        top_level,
        ["ls-files", "--stage", "-v", "-z"],
        MAX_GIT_TREE_BYTES,
    )
    tree_raw = _run_git(
        top_level,
        ["ls-tree", "-r", "-z", "--full-tree", UI_SOURCE_COMMIT],
        MAX_GIT_TREE_BYTES,
    )

    index: dict[bytes, tuple[bytes, bytes]] = {}
    for record in _nul_records(index_raw):
        try:
            tag, remainder = record[:1], record[2:]
            metadata, relative_raw = remainder.split(b"\t", 1)
            mode, oid, stage = metadata.split(b" ")
        except ValueError:
            _fail("The demo checkout index is invalid")
        if (
            record[1:2] != b" "
            or tag != b"H"
            or stage != b"0"
            or not relative_raw
            or relative_raw in index
        ):
            _fail("The demo checkout has hidden or unsupported index state")
        index[relative_raw] = (mode, oid)

    expected: dict[bytes, tuple[bytes, bytes]] = {}
    for record in _nul_records(tree_raw):
        try:
            metadata, relative_raw = record.split(b"\t", 1)
            mode, object_type, oid = metadata.split(b" ")
        except ValueError:
            _fail("The pinned demo tree is invalid")
        if object_type != b"blob" or not relative_raw or relative_raw in expected:
            _fail("The pinned demo tree contains unsupported entries")
        expected[relative_raw] = (mode, oid)

    if index != expected:
        _fail("The demo checkout index does not match the pinned tree")

    total_bytes = 0
    for relative_raw, (mode, oid) in expected.items():
        relative = Path(os.fsdecode(relative_raw))
        if relative.is_absolute() or ".." in relative.parts:
            _fail("The pinned demo tree path is invalid")
        candidate = top_level / relative
        if mode in {b"100644", b"100755"}:
            try:
                if candidate.resolve(strict=True) != candidate.absolute():
                    _fail("The demo checkout path is not canonical")
            except OSError:
                _fail("The demo checkout path could not be inspected")
            raw = _read_bounded_regular(candidate, MAX_TRACKED_FILE_BYTES)
        elif mode == b"120000":
            try:
                if not candidate.is_symlink():
                    _fail("The demo checkout link does not match the pinned tree")
                raw = os.fsencode(os.readlink(candidate))
            except OSError:
                _fail("The demo checkout link could not be inspected")
            if len(raw) > MAX_TRACKED_FILE_BYTES:
                _fail("The demo checkout link exceeds the maximum size")
        else:
            _fail("The pinned demo tree mode is unsupported")
        total_bytes += len(raw)
        if total_bytes > MAX_TRACKED_TREE_BYTES or _git_blob_oid(raw) != oid:
            _fail("The demo checkout bytes do not match the pinned tree")


def _verify_demo_checkout(repo: Path) -> Path:
    top_level_raw = _run_git(repo, ["rev-parse", "--show-toplevel"], 4 * 1024)
    try:
        if not top_level_raw.endswith(b"\n") or b"\n" in top_level_raw[:-1]:
            _fail("The demo checkout root is invalid")
        top_level = Path(top_level_raw[:-1].decode("utf-8")).resolve(strict=True)
        requested = repo.resolve(strict=True)
    except (OSError, UnicodeDecodeError):
        _fail("The demo checkout root is invalid")
    if requested != top_level:
        _fail("The demo checkout path is not its canonical worktree root")

    head = _run_git(top_level, ["rev-parse", "--verify", "HEAD^{commit}"], 128)
    tree = _run_git(top_level, ["rev-parse", "--verify", "HEAD^{tree}"], 128)
    branch = _run_git(top_level, ["rev-parse", "--abbrev-ref", "HEAD"], 128)
    status_output = _run_git(
        top_level,
        [
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignored=matching",
        ],
        4 * 1024,
    )
    if head != f"{UI_SOURCE_COMMIT}\n".encode("ascii"):
        _fail("The demo checkout is not the exact pinned commit")
    if tree != f"{UI_SOURCE_TREE}\n".encode("ascii"):
        _fail("The demo checkout is not the exact pinned tree")
    if branch != b"HEAD\n":
        _fail("The demo checkout is not detached")
    if status_output != b"":
        _fail("The demo checkout is not clean")
    _verify_tracked_worktree(top_level)
    return top_level


def _pinned_verifier_bytes(repo: Path) -> bytes:
    commit = _run_git(
        repo,
        ["rev-parse", "--verify", f"{UI_VERIFIER_COMMIT}^{{commit}}"],
        128,
    )
    tree = _run_git(
        repo,
        ["rev-parse", "--verify", f"{UI_VERIFIER_COMMIT}^{{tree}}"],
        128,
    )
    if commit != f"{UI_VERIFIER_COMMIT}\n".encode("ascii"):
        _fail("The pinned verifier commit is unavailable")
    if tree != f"{UI_VERIFIER_TREE}\n".encode("ascii"):
        _fail("The pinned verifier tree is unavailable")
    verifier = _run_git(
        repo,
        ["show", f"{UI_VERIFIER_COMMIT}:{UI_VERIFIER_PATH}"],
        MAX_VERIFIER_BYTES,
    )
    if hashlib.sha256(verifier).hexdigest() != UI_VERIFIER_SHA256:
        _fail("The pinned verifier bytes do not match")
    return verifier


def _write_private_temp(path: Path, raw: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                _fail("A private verifier input could not be written")
            offset += written
    finally:
        os.close(descriptor)


def _validate_with_pinned_verifier(
    verifier_repo: Path, demo_repo: Path, receipt: bytes
) -> None:
    verifier = _pinned_verifier_bytes(verifier_repo)
    with tempfile.TemporaryDirectory(prefix="session-prep-ui-worksheet-") as raw:
        directory = Path(raw)
        verifier_path = directory / "verify_session_prep_ui_evidence.py"
        receipt_path = directory / "synthetic-ui-evidence.json"
        _write_private_temp(verifier_path, verifier)
        _write_private_temp(receipt_path, receipt)
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("PYTHON") and not key.startswith("GIT_")
        }
        environment.update(
            {
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    os.fspath(verifier_path),
                    os.fspath(receipt_path),
                    "--sovereign-repo",
                    os.fspath(demo_repo),
                ],
                check=False,
                capture_output=True,
                timeout=55,
                cwd=demo_repo,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError):
            _fail("The pinned UI evidence verifier did not complete")
        if (
            completed.returncode != 0
            or completed.stdout != UI_VERIFIER_PASS_OUTPUT
            or completed.stderr != b""
        ):
            _fail("The pinned UI evidence verifier rejected the receipt")


def initialize(
    output: Path,
    browser_engine: str,
    browser_major: int,
    screen_reader: str,
    demo_repo: Path,
) -> None:
    browser_engine, browser_major, screen_reader = _validate_environment(
        browser_engine, browser_major, screen_reader
    )
    demo_root = _verify_demo_checkout(demo_repo)
    _reject_path_within_demo_repo(output, demo_root)
    now = _utc_now()
    started_at_utc = _canonical_timestamp(now)
    _validate_started_at(started_at_utc, now)
    worksheet = _expected_worksheet(
        started_at_utc,
        f"uxrun_{secrets.token_hex(16)}",
        f"uxchallenge_{secrets.token_hex(16)}",
        browser_engine,
        browser_major,
        screen_reader,
        "not-run",
    )
    _write_new_private(output, _json_bytes(worksheet), MAX_WORKSHEET_BYTES)


def seal(
    worksheet_path: Path,
    receipt_path: Path,
    demo_repo: Path,
    verifier_repo: Path = ROOT,
) -> None:
    demo_root = _verify_demo_checkout(demo_repo)
    _reject_path_within_demo_repo(worksheet_path, demo_root)
    _reject_path_within_demo_repo(receipt_path, demo_root)
    now = _utc_now()
    worksheet = load_completed_worksheet(worksheet_path, now)
    receipt = _build_receipt(worksheet, _canonical_timestamp(now))
    raw = _json_bytes(receipt)
    if len(raw) > MAX_RECEIPT_BYTES:
        _fail("The receipt exceeds the maximum size")
    _validate_with_pinned_verifier(verifier_repo, demo_root, raw)
    _write_new_private(receipt_path, raw, MAX_RECEIPT_BYTES)


class _SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        _fail("The command invocation is invalid")


def _parser() -> argparse.ArgumentParser:
    parser = _SanitizedArgumentParser(
        description="Prepare closed synthetic-only Session Prep UI evidence."
    )
    commands = parser.add_subparsers(
        dest="command", required=True, parser_class=_SanitizedArgumentParser
    )
    initialize_parser = commands.add_parser("init")
    initialize_parser.add_argument("output", type=Path)
    initialize_parser.add_argument(
        "--browser-engine", required=True, choices=sorted(BROWSER_ENGINES)
    )
    initialize_parser.add_argument("--browser-major", required=True, type=int)
    initialize_parser.add_argument(
        "--screen-reader", required=True, choices=sorted(SCREEN_READERS)
    )
    initialize_parser.add_argument("--demo-repo", required=True, type=Path)
    seal_parser = commands.add_parser("seal")
    seal_parser.add_argument("worksheet", type=Path)
    seal_parser.add_argument("receipt", type=Path)
    seal_parser.add_argument("--demo-repo", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "init":
            initialize(
                arguments.output,
                arguments.browser_engine,
                arguments.browser_major,
                arguments.screen_reader,
                arguments.demo_repo,
            )
            summary = INIT_PASS_SUMMARY
        else:
            seal(arguments.worksheet, arguments.receipt, arguments.demo_repo)
            summary = SEAL_PASS_SUMMARY
    except Exception:
        print(FAIL_SUMMARY, file=sys.stderr)
        return 1
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
