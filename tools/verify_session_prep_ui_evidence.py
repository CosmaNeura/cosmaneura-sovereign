#!/usr/bin/env python3
"""Validate a closed, synthetic-only Session Prep UI/UX evidence receipt.

The verifier reads one local JSON receipt, verifies pinned local Git objects,
and runs two dependency-free Node harnesses against those exact bytes. It
never calls an HTTP service and never prints receipt content, paths,
identifiers, or subprocess output.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn, Sequence


ROOT = Path(__file__).resolve().parents[1]
MAX_RECEIPT_BYTES = 16 * 1024
MAX_JSON_DEPTH = 12
SCHEMA = "cosmaneura.session-prep.synthetic-ui-ux-evidence.v1"
SOVEREIGN_COMMIT = "e0736289f36b200a3daee59db3732482bab6fc5d"
SOVEREIGN_TREE = "3391bc15f8a110ccac55d39668ba9f6c4eb9c175"
SOURCE_COMMIT_TIME = dt.datetime(2026, 9, 3, 20, 3, 13, tzinfo=dt.UTC)
DEMO_ASSET_PATH = "tools/session_prep_demo.html"
DEMO_ASSET_SHA256 = (
    "sha256:f7b0c7f5cda44cb4a8a38e963c767c8d19da8d09aa351b2f54e80535c3a052c7"
)
SOURCE_MANIFEST = {
    "app/panel.js": (
        "sha256:ab64c6d2135d7b217834363791728ab04fb9d86524f1643cb72f88cfeb4e0d5c"
    ),
    "tests/panel_contract_harness.js": (
        "sha256:777c8076b832959e6cb05829d84f7ca2ee462d7d37b98fd149163ec927a6c6fa"
    ),
    "tests/session_prep_demo_ui_harness.js": (
        "sha256:c4d11a39fcc2da5eb639f9c3bb4e6d0210555e565c4646acccd4473d89320fa9"
    ),
    "tools/run_session_prep_demo.py": (
        "sha256:175ccc5e077e2e9a61ee2f4f1675745a58cb7b49fc3fa2b9afa7f82be86f18c5"
    ),
    DEMO_ASSET_PATH: DEMO_ASSET_SHA256,
}
SOURCE_MANIFEST_SHA256 = (
    "sha256:8bcae9ab57f5b2d301e4d8b2e2ffdbfb373d6f7c556a84b76ad2686bd59f8152"
)
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
BROWSER_ENGINES = frozenset({"chromium", "firefox", "webkit"})
SCREEN_READERS = frozenset({"jaws", "nvda", "orca", "talkback", "voiceover"})
PASS_SUMMARY = (
    "[PASS] synthetic-only Session Prep UI/UX evidence verified: "
    "4 states, 2 viewports, accessibility, transitions, and privacy."
)
FAIL_SUMMARY = "[FAIL] synthetic-only Session Prep UI/UX evidence rejected."


def _expected_evidence(
    observed_at_utc: str,
    browser_engine: str,
    browser_major: int,
    screen_reader: str,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "observed_at_utc": observed_at_utc,
        "environment": {
            "browser_engine": browser_engine,
            "browser_major": browser_major,
            "screen_reader": screen_reader,
        },
        "scope": {
            "surface": "bundled-loopback-demo",
            "data_class": "synthetic-only",
            "manual_observation_completed": True,
            "network_scope": "loopback-only",
            "live_service_contacted": False,
        },
        "source": {
            "sovereign_commit": SOVEREIGN_COMMIT,
            "sovereign_tree": SOVEREIGN_TREE,
            "demo_asset_path": DEMO_ASSET_PATH,
            "demo_asset_sha256": DEMO_ASSET_SHA256,
            "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        },
        "manual_observations": {
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
        },
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


class EvidenceError(RuntimeError):
    """The receipt or its immutable source binding failed closed."""


def _fail(message: str) -> NoReturn:
    raise EvidenceError(message)


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
            _fail("The receipt exceeds the maximum JSON depth")
        if isinstance(candidate, dict):
            pending.extend((item, depth + 1) for item in candidate.values())
        elif isinstance(candidate, list):
            pending.extend((item, depth + 1) for item in candidate)


def load_receipt(path: Path) -> dict[str, Any]:
    """Read a bounded UTF-8 JSON object without duplicate keys or NaN values."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow == 0:
        try:
            if path.is_symlink():
                _fail("The receipt must not be a symbolic link")
        except OSError:
            _fail("The receipt could not be inspected")
    try:
        descriptor = os.open(path, flags | no_follow)
    except OSError:
        _fail("The receipt could not be read")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail("The receipt must be a regular file")
        if before.st_size > MAX_RECEIPT_BYTES:
            _fail("The receipt exceeds the maximum size")
        chunks: list[bytes] = []
        remaining = MAX_RECEIPT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if identity_before != identity_after or len(raw) != after.st_size:
            _fail("The receipt changed while it was being read")
    except OSError:
        _fail("The receipt could not be read")
    finally:
        os.close(descriptor)
    if len(raw) > MAX_RECEIPT_BYTES:
        _fail("The receipt exceeds the maximum size")
    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except EvidenceError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        _fail("The receipt is not valid bounded JSON")
    _reject_excessive_depth(parsed)
    if type(parsed) is not dict:
        _fail("The receipt must be one JSON object")
    return parsed


def _assert_exact(actual: Any, expected: Any) -> None:
    """Require exact types, keys, values, and pass-only observation leaves."""
    if type(actual) is not type(expected):
        _fail("The receipt contains an unexpected value type")
    if isinstance(expected, dict):
        if set(actual) != set(expected):
            _fail("The receipt contains missing or unknown fields")
        for key, expected_value in expected.items():
            _assert_exact(actual[key], expected_value)
        return
    if actual != expected:
        _fail("The receipt contains an unapproved value")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def validate_receipt(receipt: dict[str, Any]) -> None:
    """Validate the closed metadata-only v1 receipt schema and its digest."""
    if type(receipt) is not dict:
        _fail("The receipt envelope must be one JSON object")
    if set(receipt) != {"evidence", "evidence_sha256"}:
        _fail("The receipt envelope contains missing or unknown fields")
    document = receipt.get("evidence")
    receipt_digest = receipt.get("evidence_sha256")
    if type(document) is not dict or type(receipt_digest) is not str:
        _fail("The receipt envelope is invalid")
    if SHA256_PATTERN.fullmatch(receipt_digest) is None:
        _fail("The receipt digest is malformed")

    source = document.get("source")
    if type(source) is not dict:
        _fail("The receipt has no valid source binding")
    commit = source.get("sovereign_commit")
    digest = source.get("demo_asset_sha256")
    if type(commit) is not str or COMMIT_PATTERN.fullmatch(commit) is None:
        _fail("The source commit is malformed")
    if type(digest) is not str or SHA256_PATTERN.fullmatch(digest) is None:
        _fail("The demo asset digest is malformed")

    observed_at_utc = document.get("observed_at_utc")
    if (
        type(observed_at_utc) is not str
        or TIMESTAMP_PATTERN.fullmatch(observed_at_utc) is None
    ):
        _fail("The observation timestamp is not canonical UTC seconds")
    try:
        observed_at = dt.datetime.strptime(
            observed_at_utc, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=dt.UTC)
    except ValueError:
        _fail("The observation timestamp is invalid")
    if observed_at < SOURCE_COMMIT_TIME:
        _fail("The observation predates the pinned source")
    if observed_at > _utc_now() + dt.timedelta(minutes=5):
        _fail("The observation timestamp is in the future")

    environment = document.get("environment")
    if type(environment) is not dict:
        _fail("The browser environment is invalid")
    browser_engine = environment.get("browser_engine")
    browser_major = environment.get("browser_major")
    screen_reader = environment.get("screen_reader")
    if type(browser_engine) is not str or browser_engine not in BROWSER_ENGINES:
        _fail("The browser engine is unsupported")
    if type(browser_major) is not int or not 1 <= browser_major <= 999:
        _fail("The browser major version is invalid")
    if type(screen_reader) is not str or screen_reader not in SCREEN_READERS:
        _fail("The screen reader is unsupported")

    expected = _expected_evidence(
        observed_at_utc,
        browser_engine,
        browser_major,
        screen_reader,
    )
    _assert_exact(document, expected)
    if _digest(document) != receipt_digest:
        _fail("The receipt digest does not match the evidence")


def _run_git(repo: Path, arguments: Sequence[str]) -> bytes:
    environment = os.environ.copy()
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
                "git",
                "--no-replace-objects",
                "-C",
                os.fspath(repo),
                *arguments,
            ],
            check=False,
            capture_output=True,
            timeout=15,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("Local Git object verification could not complete")
    if completed.returncode != 0:
        _fail("A required local Git object is unavailable")
    return completed.stdout


def verify_source_binding(repo: Path) -> dict[str, bytes]:
    """Return verified reviewed assets from local Git objects only."""
    resolved = _run_git(
        repo,
        ["rev-parse", "--verify", f"{SOVEREIGN_COMMIT}^{{commit}}"],
    )
    try:
        resolved_commit = resolved.decode("ascii").strip()
    except UnicodeDecodeError:
        _fail("The local Git commit identity is invalid")
    if resolved_commit != SOVEREIGN_COMMIT:
        _fail("The reviewed source commit did not resolve exactly")

    resolved_tree = _run_git(
        repo,
        ["rev-parse", "--verify", f"{SOVEREIGN_COMMIT}^{{tree}}"],
    )
    try:
        tree = resolved_tree.decode("ascii").strip()
    except UnicodeDecodeError:
        _fail("The local Git tree identity is invalid")
    if tree != SOVEREIGN_TREE:
        _fail("The reviewed source tree did not resolve exactly")

    if _digest(SOURCE_MANIFEST) != SOURCE_MANIFEST_SHA256:
        _fail("The source manifest digest is invalid")
    assets: dict[str, bytes] = {}
    for path, expected_digest in SOURCE_MANIFEST.items():
        asset = _run_git(repo, ["show", f"{SOVEREIGN_COMMIT}:{path}"])
        if "sha256:" + hashlib.sha256(asset).hexdigest() != expected_digest:
            _fail("A reviewed source asset digest does not match")
        assets[path] = asset
    return assets


def _run_harness(command: Sequence[str], cwd: Path) -> None:
    environment = {
        "LC_ALL": "C",
        "NODE_OPTIONS": "",
        "NODE_PATH": "",
        "PATH": os.environ.get("PATH", ""),
    }
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("An exact local UI harness could not complete")
    if result.returncode != 0:
        _fail("An exact local UI harness failed")


def verify_automated_support(assets: dict[str, bytes]) -> None:
    """Run exact offline harness bytes supporting non-visual observations."""
    node = shutil.which("node")
    if node is None:
        _fail("The local Node.js runtime is unavailable")
    required = {
        "app/panel.js",
        "tests/panel_contract_harness.js",
        "tests/session_prep_demo_ui_harness.js",
        DEMO_ASSET_PATH,
    }
    if not required.issubset(assets):
        _fail("A required exact UI harness asset is unavailable")
    with tempfile.TemporaryDirectory(prefix="session-prep-ui-evidence-") as raw:
        destination = Path(raw)
        panel = destination / "panel.js"
        panel_harness = destination / "panel_contract_harness.js"
        demo = destination / "session_prep_demo.html"
        demo_harness = destination / "session_prep_demo_ui_harness.js"
        panel.write_bytes(assets["app/panel.js"])
        panel_harness.write_bytes(assets["tests/panel_contract_harness.js"])
        demo.write_bytes(assets[DEMO_ASSET_PATH])
        demo_harness.write_bytes(assets["tests/session_prep_demo_ui_harness.js"])
        _run_harness([node, os.fspath(demo_harness), os.fspath(demo)], destination)
        _run_harness([node, os.fspath(panel_harness), os.fspath(panel)], destination)


def verify(receipt_path: Path, sovereign_repo: Path) -> None:
    """Validate the receipt and its immutable local source binding."""
    receipt = load_receipt(receipt_path)
    validate_receipt(receipt)
    assets = verify_source_binding(sovereign_repo)
    verify_automated_support(assets)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate metadata-only synthetic Session Prep UI/UX evidence."
    )
    parser.add_argument("receipt", type=Path, help="path to the local JSON receipt")
    parser.add_argument(
        "--sovereign-repo",
        type=Path,
        default=ROOT,
        help="local Sovereign Git repository containing the reviewed commit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        verify(args.receipt, args.sovereign_repo)
    except Exception:
        print(FAIL_SUMMARY, file=sys.stderr)
        return 1
    print(PASS_SUMMARY)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
