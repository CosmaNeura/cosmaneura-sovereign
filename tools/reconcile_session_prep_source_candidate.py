#!/usr/bin/env python3
"""Reconcile one exact, synthetic-only Session Prep source candidate pair.

The reconciler reads source and control bytes only from explicit Git objects.
It validates a local UI operator-attestation receipt with the exact existing
receipt verifier committed in the Sovereign candidate.  It does not contact a
service, build an image, deploy, or promote any readiness gate.
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn, Sequence


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "cosmaneura.session-prep.staging-source-candidate.v1"
MAX_RECEIPT_BYTES = 16 * 1024
MAX_OUTPUT_BYTES = 8 * 1024
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")

# The reviewed v1 acceptance pair.  These are lineage anchors, not claims
# about a deployment or the current state of either default branch.
ACCEPTED_CORE_COMMIT = "3d56d68e285ad0a136904f0632874ac67797db5e"
ACCEPTED_CORE_TREE = "eefd766abbe20c51669705c5c6b238ddbd3a5cd4"
ACCEPTED_SOVEREIGN_COMMIT = "b4da6defb2c31166fc2902c320cc1aa028e3cc3f"
ACCEPTED_SOVEREIGN_TREE = "66b1484a96bd8a5d14fe9bb27ab19d0088bcef03"

# The one source pair this version is allowed to reconcile.
CANDIDATE_CORE_COMMIT = "fc4fbc299cd1edcf0b5e35c4e95a7ee62ed5907b"
CANDIDATE_CORE_TREE = "b29429957d7b0c2840924ec60bb18efdf68c568d"
CANDIDATE_SOVEREIGN_COMMIT = "01be35ab851c4d2cd25a98020437b9ab7ba49665"
CANDIDATE_SOVEREIGN_TREE = "852a7fbb0a998035485c8b480de9c2a703796512"
CANDIDATE_PAIR = (
    (CANDIDATE_CORE_COMMIT, CANDIDATE_CORE_TREE),
    (CANDIDATE_SOVEREIGN_COMMIT, CANDIDATE_SOVEREIGN_TREE),
)

CONTRACT_DIRECTORY = "provider-session-prep-v1"
CONTRACT_PATHS = frozenset(
    {
        f"contracts/fixtures/{CONTRACT_DIRECTORY}/request-brief.json",
        f"contracts/fixtures/{CONTRACT_DIRECTORY}/request-supporting-context.json",
        f"contracts/fixtures/{CONTRACT_DIRECTORY}/result-pending.json",
        f"contracts/fixtures/{CONTRACT_DIRECTORY}/result-ready-brief.json",
        (
            f"contracts/fixtures/{CONTRACT_DIRECTORY}/"
            "result-ready-supporting-context.json"
        ),
        f"contracts/fixtures/{CONTRACT_DIRECTORY}/result-refreshing-brief.json",
        f"contracts/fixtures/{CONTRACT_DIRECTORY}/result-unavailable.json",
        f"contracts/schemas/{CONTRACT_DIRECTORY}/request.schema.json",
        f"contracts/schemas/{CONTRACT_DIRECTORY}/result.schema.json",
    }
)

CORE_PANEL_CONSTANTS_PATH = "web/app/sovereign/SovereignPanelAsset.ts"
CORE_PANEL_ASSET_SHA256 = (
    "ab64c6d2135d7b217834363791728ab04fb9d86524f1643cb72f88cfeb4e0d5c"
)
CORE_PANEL_ASSET_PATH = (
    f"web/public/vendor/cosmaneura-panel.v1.{CORE_PANEL_ASSET_SHA256}.js"
)
CORE_RUNTIME_CONFIG_PATH = "app/configuration/session_prep.py"
CORE_STAGING_DEPLOY_PATH = "infra-staging/deploy.sh"
CORE_STAGING_WORKFLOW_PATH = ".github/workflows/deploy_staging_candidate.yml"
SOVEREIGN_CONFIG_PATH = "app/config.py"
SOVEREIGN_PANEL_PATH = "app/panel.js"
PANEL_SOURCE_COMMIT = "024655ca3a542caf02e46d1b9b209d3c5b4d96d0"
PANEL_SOURCE_TREE = "4e9f0da567811941291cf0b95c1a10758d46b1b4"
PANEL_SHA256 = CORE_PANEL_ASSET_SHA256
PANEL_SRI = "sha256-q2TG0hNdeyF4NDY3kXKKsE+52GUk8WQ8ty+Iz+tODVw="

# Exact bytes already accepted for the controls and panel pin.  Candidate
# bytes must also equal the corresponding accepted-base objects.
CONTROL_SHA256 = {
    ("core", CORE_RUNTIME_CONFIG_PATH): (
        "0815946f39fd5ac68a3a67424976ad6b781c41eb45ed0bc498c69df42b7d28d0"
    ),
    ("core", CORE_STAGING_DEPLOY_PATH): (
        "4a00301db74368b7377c74869786a83be8ca45308fcfd71013835c22d6fc34dc"
    ),
    ("core", CORE_STAGING_WORKFLOW_PATH): (
        "5c5ee07d2748ea41c4983df0a29146955994d968d58b93751d7da84dcfb82aa8"
    ),
    ("sovereign", SOVEREIGN_CONFIG_PATH): (
        "af7dee5c32443947cf514aaef61701a9545b3a71f1f59ab3fa112dd2b618a376"
    ),
}
CORE_PANEL_CONSTANTS_SHA256 = (
    "4abceffafbee0e2460ac9297078cbb088d8ea0c5ca9c045c8c5c2affbe40e92b"
)
CORE_RUNTIME_PATHS = frozenset(
    {
        CORE_RUNTIME_CONFIG_PATH,
        "app/routes/agent.py",
        "app/services/authority.py",
        "app/services/session_prep.py",
        "app/services/session_prep_scope.py",
        "app/services/staging_synthetic_appointment.py",
        CORE_PANEL_CONSTANTS_PATH,
        "web/app/sovereign/SovereignPanelHost.tsx",
        CORE_STAGING_DEPLOY_PATH,
        CORE_STAGING_WORKFLOW_PATH,
    }
)
SOVEREIGN_RUNTIME_PATHS = frozenset(
    {
        "app/clients/session_prep.py",
        "app/clients/session_prep_contract.py",
        SOVEREIGN_CONFIG_PATH,
        "app/main.py",
        SOVEREIGN_PANEL_PATH,
        "app/schemas.py",
        "app/session_prep.py",
        "app/sovereign/orchestrator.py",
    }
)

# The existing UI receipt verifier is itself pinned to committed bytes.  Its
# source-bound receipt remains an unauthenticated operator attestation.
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

PASS_PREFIX = "{"
FAIL_SUMMARY = "[FAIL] Session Prep source candidate reconciliation rejected."


class ReconciliationError(RuntimeError):
    """An exact source or receipt check failed closed."""


def _fail(message: str) -> NoReturn:
    raise ReconciliationError(message)


def _validate_static_pins() -> None:
    commits_and_trees = (
        ACCEPTED_CORE_COMMIT,
        ACCEPTED_CORE_TREE,
        ACCEPTED_SOVEREIGN_COMMIT,
        ACCEPTED_SOVEREIGN_TREE,
        CANDIDATE_CORE_COMMIT,
        CANDIDATE_CORE_TREE,
        CANDIDATE_SOVEREIGN_COMMIT,
        CANDIDATE_SOVEREIGN_TREE,
        PANEL_SOURCE_COMMIT,
        PANEL_SOURCE_TREE,
        UI_SOURCE_COMMIT,
        UI_SOURCE_TREE,
        UI_VERIFIER_COMMIT,
        UI_VERIFIER_TREE,
    )
    digests = (
        CORE_PANEL_ASSET_SHA256,
        PANEL_SHA256,
        CORE_PANEL_CONSTANTS_SHA256,
        UI_VERIFIER_SHA256,
        *CONTROL_SHA256.values(),
    )
    if any(COMMIT_PATTERN.fullmatch(value) is None for value in commits_and_trees):
        _fail("The embedded source policy contains a malformed Git object ID")
    if any(SHA256_PATTERN.fullmatch(value) is None for value in digests):
        _fail("The embedded source policy contains a malformed SHA-256 digest")
    if CANDIDATE_PAIR != (
        (CANDIDATE_CORE_COMMIT, CANDIDATE_CORE_TREE),
        (CANDIDATE_SOVEREIGN_COMMIT, CANDIDATE_SOVEREIGN_TREE),
    ):
        _fail("The embedded exact source pair policy is inconsistent")


def _git_environment() -> dict[str, str]:
    """Return a closed environment that prevents prompting and lazy fetches."""
    return {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": os.environ.get("PATH", ""),
    }


def _run_git(repo: Path, arguments: Sequence[str]) -> bytes:
    try:
        result = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                os.fspath(repo),
                *arguments,
            ],
            check=False,
            capture_output=True,
            timeout=20,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("Local Git object verification could not complete")
    if result.returncode != 0:
        _fail("A required local Git object is unavailable")
    return result.stdout


def _ascii(value: bytes, failure: str) -> str:
    try:
        return value.decode("ascii").strip()
    except UnicodeDecodeError:
        _fail(failure)


def _resolve_exact_commit(
    repo: Path,
    supplied: str,
    expected_commit: str,
    expected_tree: str,
    label: str,
) -> tuple[str, str]:
    if COMMIT_PATTERN.fullmatch(supplied) is None:
        _fail(f"{label} must be an exact lowercase 40-character commit ID")
    if supplied != expected_commit:
        _fail(f"{label} is not the reviewed exact source candidate")
    resolved = _ascii(
        _run_git(repo, ["rev-parse", "--verify", f"{supplied}^{{commit}}"]),
        f"{label} identity is invalid",
    )
    if resolved != supplied:
        _fail(f"{label} did not resolve exactly")
    tree = _ascii(
        _run_git(repo, ["rev-parse", "--verify", f"{supplied}^{{tree}}"]),
        f"{label} tree identity is invalid",
    )
    if tree != expected_tree or COMMIT_PATTERN.fullmatch(tree) is None:
        _fail(f"{label} tree is not the reviewed exact tree")
    return resolved, tree


def _resolve_anchor(repo: Path, commit: str, tree: str, label: str) -> None:
    resolved = _ascii(
        _run_git(repo, ["rev-parse", "--verify", f"{commit}^{{commit}}"]),
        f"{label} identity is invalid",
    )
    resolved_tree = _ascii(
        _run_git(repo, ["rev-parse", "--verify", f"{commit}^{{tree}}"]),
        f"{label} tree identity is invalid",
    )
    if resolved != commit or resolved_tree != tree:
        _fail(f"{label} did not resolve to the accepted Git object")


def _require_ancestor(repo: Path, ancestor: str, descendant: str) -> None:
    try:
        result = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                os.fspath(repo),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            check=False,
            capture_output=True,
            timeout=20,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("Local Git lineage verification could not complete")
    if result.returncode != 0:
        _fail("The source candidate is not a descendant of its accepted base")


def _git_blob(repo: Path, commit: str, path: str) -> tuple[str, str, bytes]:
    entry = _run_git(repo, ["ls-tree", "-z", commit, "--", path])
    try:
        metadata, entry_path = entry.rstrip(b"\0").split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split(" ")
        decoded_path = entry_path.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        _fail("A required Git tree entry is invalid")
    if (
        decoded_path != path
        or object_type != "blob"
        or mode not in {"100644", "100755"}
        or COMMIT_PATTERN.fullmatch(object_id) is None
    ):
        _fail("A required source path is not one regular Git blob")
    return mode, object_id, _run_git(repo, ["show", f"{commit}:{path}"])


def _git_bytes(repo: Path, commit: str, path: str) -> bytes:
    return _git_blob(repo, commit, path)[2]


def _git_text(repo: Path, commit: str, path: str) -> str:
    try:
        return _git_bytes(repo, commit, path).decode("utf-8")
    except UnicodeDecodeError:
        _fail("A required source object is not UTF-8 text")


def _contract_paths(repo: Path, commit: str) -> set[str]:
    raw = _run_git(
        repo,
        [
            "ls-tree",
            "-r",
            "--name-only",
            "-z",
            commit,
            "--",
            f"contracts/fixtures/{CONTRACT_DIRECTORY}",
            f"contracts/schemas/{CONTRACT_DIRECTORY}",
        ],
    )
    try:
        return {item.decode("utf-8") for item in raw.split(b"\0") if item}
    except UnicodeDecodeError:
        _fail("A contract path is not UTF-8 text")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _manifest_digest(values: dict[str, str]) -> str:
    return "sha256:" + _sha256(_canonical_json(values).encode("ascii"))


def _verify_contracts(
    core_repo: Path,
    core_commit: str,
    sovereign_repo: Path,
    sovereign_commit: str,
) -> dict[str, Any]:
    revisions = (
        (core_repo, ACCEPTED_CORE_COMMIT),
        (core_repo, core_commit),
        (sovereign_repo, ACCEPTED_SOVEREIGN_COMMIT),
        (sovereign_repo, sovereign_commit),
    )
    if any(
        _contract_paths(repo, commit) != CONTRACT_PATHS for repo, commit in revisions
    ):
        _fail("The exact Session Prep v1 contract set drifted")

    manifest: dict[str, str] = {}
    for path in sorted(CONTRACT_PATHS):
        versions = [_git_blob(repo, commit, path) for repo, commit in revisions]
        if any(value != versions[0] for value in versions[1:]):
            _fail("A Session Prep v1 contract object drifted")
        manifest[path] = "sha256:" + _sha256(versions[0][2])
    return {
        "byte_compatible": True,
        "file_count": len(manifest),
        "manifest_sha256": _manifest_digest(manifest),
    }


def _verify_panel(
    core_repo: Path,
    core_commit: str,
    sovereign_repo: Path,
    sovereign_commit: str,
) -> dict[str, Any]:
    core_constants_object = _git_blob(core_repo, core_commit, CORE_PANEL_CONSTANTS_PATH)
    base_constants_object = _git_blob(
        core_repo, ACCEPTED_CORE_COMMIT, CORE_PANEL_CONSTANTS_PATH
    )
    core_constants = core_constants_object[2]
    if (
        core_constants_object != base_constants_object
        or _sha256(core_constants) != CORE_PANEL_CONSTANTS_SHA256
    ):
        _fail("The Core panel provenance control bytes drifted")

    core_asset_object = _git_blob(core_repo, core_commit, CORE_PANEL_ASSET_PATH)
    base_asset_object = _git_blob(
        core_repo, ACCEPTED_CORE_COMMIT, CORE_PANEL_ASSET_PATH
    )
    accepted_panel_object = _git_blob(
        sovereign_repo, ACCEPTED_SOVEREIGN_COMMIT, SOVEREIGN_PANEL_PATH
    )
    candidate_panel_object = _git_blob(
        sovereign_repo, sovereign_commit, SOVEREIGN_PANEL_PATH
    )
    panel_source_object = _git_blob(
        sovereign_repo, PANEL_SOURCE_COMMIT, SOVEREIGN_PANEL_PATH
    )
    if not (
        core_asset_object
        == base_asset_object
        == accepted_panel_object
        == candidate_panel_object
        == panel_source_object
    ):
        _fail("The exact Session Prep panel bytes are not compatible")
    core_asset = core_asset_object[2]
    digest = _sha256(core_asset)
    sri = "sha256-" + base64.b64encode(hashlib.sha256(core_asset).digest()).decode(
        "ascii"
    )
    if digest != PANEL_SHA256 or sri != PANEL_SRI:
        _fail("The Session Prep panel digest or SRI pin drifted")
    return {
        "byte_compatible": True,
        "sha256": "sha256:" + digest,
        "sri": sri,
    }


def _sovereign_runtime_default_off(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    settings = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Settings"
    ]
    if len(settings) != 1:
        return False
    declarations = [
        node
        for node in settings[0].body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "feature_session_prep"
    ]
    if not (
        len(declarations) == 1
        and isinstance(declarations[0].value, ast.Constant)
        and declarations[0].value.value is False
    ):
        return False

    declaration = declarations[0]

    def writes_feature(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id == "feature_session_prep"
        if isinstance(node, ast.Attribute):
            return node.attr == "feature_session_prep"
        if isinstance(node, (ast.List, ast.Tuple)):
            return any(writes_feature(item) for item in node.elts)
        if isinstance(node, ast.Starred):
            return writes_feature(node.value)
        return False

    for node in ast.walk(tree):
        if node is declaration:
            continue
        if isinstance(node, ast.AnnAssign) and writes_feature(node.target):
            return False
        if isinstance(node, ast.Assign) and any(
            writes_feature(target) for target in node.targets
        ):
            return False
        if isinstance(node, (ast.AugAssign, ast.NamedExpr)) and writes_feature(
            node.target
        ):
            return False
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "feature_session_prep"
        ):
            return False
    return True


def _core_runtime_default_off(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    matches = 0
    valid_matches = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "config" or not node.args:
            continue
        if not isinstance(node.args[0], ast.Constant):
            continue
        if node.args[0].value != "SESSION_PREP_SYNTHETIC_ENABLED":
            continue
        matches += 1
        keywords = {item.arg: item.value for item in node.keywords if item.arg}
        default = keywords.get("default")
        cast = keywords.get("cast")
        if (
            isinstance(default, ast.Constant)
            and default.value is False
            and isinstance(cast, ast.Name)
            and cast.id == "bool"
        ):
            valid_matches += 1
    return matches == 1 and valid_matches == 1


def _verify_controls(
    core_repo: Path,
    core_commit: str,
    sovereign_repo: Path,
    sovereign_commit: str,
) -> dict[str, Any]:
    values: dict[tuple[str, str], bytes] = {}
    for repository, label, candidate, accepted, paths in (
        (
            core_repo,
            "core",
            core_commit,
            ACCEPTED_CORE_COMMIT,
            CORE_RUNTIME_PATHS,
        ),
        (
            sovereign_repo,
            "sovereign",
            sovereign_commit,
            ACCEPTED_SOVEREIGN_COMMIT,
            SOVEREIGN_RUNTIME_PATHS,
        ),
    ):
        for path in sorted(paths):
            candidate_mode, candidate_blob, candidate_bytes = _git_blob(
                repository, candidate, path
            )
            accepted_mode, accepted_blob, accepted_bytes = _git_blob(
                repository, accepted, path
            )
            if (
                candidate_mode != accepted_mode
                or candidate_blob != accepted_blob
                or candidate_bytes != accepted_bytes
            ):
                _fail("An accepted Session Prep runtime or control object drifted")
            values[(label, path)] = candidate_bytes

    for key, expected in CONTROL_SHA256.items():
        if _sha256(values[key]) != expected:
            _fail("An accepted Session Prep feature-control digest drifted")

    core_runtime = values[("core", CORE_RUNTIME_CONFIG_PATH)].decode("utf-8")
    sovereign_runtime = values[("sovereign", SOVEREIGN_CONFIG_PATH)].decode("utf-8")
    if not _core_runtime_default_off(core_runtime):
        _fail("The Core Session Prep runtime gate is not default-off")
    if not _sovereign_runtime_default_off(sovereign_runtime):
        _fail("The Sovereign Session Prep runtime gate is not default-off")

    # These exact staging objects are the previously reviewed forced-false
    # controls.  Equality plus their accepted SHA-256 pins is the proof; this
    # tool does not heuristically interpret shell or workflow source.
    manifest = {
        f"{label}:{path}": "sha256:" + _sha256(value)
        for (label, path), value in sorted(values.items())
    }
    return {
        "byte_compatible": True,
        "core_runtime_default_off": True,
        "file_count": len(manifest),
        "sovereign_runtime_default_off": True,
        "staging_feature_forced_off": True,
        "manifest_sha256": _manifest_digest(manifest),
    }


def _verify_ui_verifier_provenance(
    sovereign_repo: Path, sovereign_commit: str
) -> bytes:
    _resolve_anchor(
        sovereign_repo, UI_SOURCE_COMMIT, UI_SOURCE_TREE, "UI evidence source"
    )
    _resolve_anchor(
        sovereign_repo,
        UI_VERIFIER_COMMIT,
        UI_VERIFIER_TREE,
        "UI evidence verifier source",
    )
    _require_ancestor(sovereign_repo, UI_SOURCE_COMMIT, sovereign_commit)
    _require_ancestor(sovereign_repo, UI_VERIFIER_COMMIT, sovereign_commit)
    candidate_bytes = _git_bytes(sovereign_repo, sovereign_commit, UI_VERIFIER_PATH)
    source_bytes = _git_bytes(sovereign_repo, UI_VERIFIER_COMMIT, UI_VERIFIER_PATH)
    if (
        candidate_bytes != source_bytes
        or _sha256(candidate_bytes) != UI_VERIFIER_SHA256
    ):
        _fail("The existing UI evidence verifier bytes drifted")
    return candidate_bytes


def _read_receipt_once(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow == 0:
        try:
            if path.is_symlink():
                _fail("The UI receipt must not be a symbolic link")
        except OSError:
            _fail("The UI receipt could not be inspected")
    try:
        descriptor = os.open(path, flags | no_follow)
    except OSError:
        _fail("The UI receipt could not be read")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail("The UI receipt must be a regular file")
        if before.st_size > MAX_RECEIPT_BYTES:
            _fail("The UI receipt exceeds the maximum size")
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
            _fail("The UI receipt changed while being read")
    except OSError:
        _fail("The UI receipt could not be read")
    finally:
        os.close(descriptor)
    if len(raw) > MAX_RECEIPT_BYTES:
        _fail("The UI receipt exceeds the maximum size")
    return raw


def _write_all(path: Path, value: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(value):
            written = os.write(descriptor, value[offset:])
            if written <= 0:
                _fail("A private verification input could not be copied")
            offset += written
    finally:
        os.close(descriptor)


def _run_existing_ui_verifier(
    sovereign_repo: Path, verifier: bytes, receipt: bytes
) -> None:
    with tempfile.TemporaryDirectory(prefix="session-prep-source-candidate-") as raw:
        directory = Path(raw)
        verifier_path = directory / "verify_session_prep_ui_evidence.py"
        receipt_path = directory / "ui-evidence.json"
        _write_all(verifier_path, verifier)
        _write_all(receipt_path, receipt)
        environment = {
            **_git_environment(),
            "NODE_OPTIONS": "",
            "NODE_PATH": "",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": "",
        }
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    os.fspath(verifier_path),
                    os.fspath(receipt_path),
                    "--sovereign-repo",
                    os.fspath(sovereign_repo),
                ],
                check=False,
                capture_output=True,
                timeout=90,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired):
            _fail("The exact existing UI evidence verifier could not complete")
        if (
            result.returncode != 0
            or result.stdout != UI_VERIFIER_PASS_OUTPUT
            or result.stderr
        ):
            _fail("The UI operator-attestation receipt was rejected")


def verify_source_pair(
    *,
    core_repo: Path,
    core_commit: str,
    sovereign_repo: Path,
    sovereign_commit: str,
) -> dict[str, Any]:
    """Verify exact candidate lineage and accepted byte compatibility."""
    _validate_static_pins()
    if (
        COMMIT_PATTERN.fullmatch(core_commit) is None
        or COMMIT_PATTERN.fullmatch(sovereign_commit) is None
    ):
        _fail("Candidate commits must be exact lowercase 40-character IDs")
    supplied_pair = (
        (core_commit, CANDIDATE_CORE_TREE),
        (sovereign_commit, CANDIDATE_SOVEREIGN_TREE),
    )
    if supplied_pair != CANDIDATE_PAIR:
        _fail("The supplied commits are not the reviewed exact source pair")
    resolved_core, core_tree = _resolve_exact_commit(
        core_repo,
        core_commit,
        CANDIDATE_CORE_COMMIT,
        CANDIDATE_CORE_TREE,
        "Core candidate",
    )
    resolved_sovereign, sovereign_tree = _resolve_exact_commit(
        sovereign_repo,
        sovereign_commit,
        CANDIDATE_SOVEREIGN_COMMIT,
        CANDIDATE_SOVEREIGN_TREE,
        "Sovereign candidate",
    )
    _resolve_anchor(
        core_repo, ACCEPTED_CORE_COMMIT, ACCEPTED_CORE_TREE, "Accepted Core base"
    )
    _resolve_anchor(
        sovereign_repo,
        ACCEPTED_SOVEREIGN_COMMIT,
        ACCEPTED_SOVEREIGN_TREE,
        "Accepted Sovereign base",
    )
    _require_ancestor(core_repo, ACCEPTED_CORE_COMMIT, resolved_core)
    _require_ancestor(sovereign_repo, ACCEPTED_SOVEREIGN_COMMIT, resolved_sovereign)
    _resolve_anchor(
        sovereign_repo,
        PANEL_SOURCE_COMMIT,
        PANEL_SOURCE_TREE,
        "Accepted panel source",
    )
    _require_ancestor(sovereign_repo, PANEL_SOURCE_COMMIT, resolved_sovereign)
    contracts = _verify_contracts(
        core_repo, resolved_core, sovereign_repo, resolved_sovereign
    )
    panel = _verify_panel(core_repo, resolved_core, sovereign_repo, resolved_sovereign)
    controls = _verify_controls(
        core_repo, resolved_core, sovereign_repo, resolved_sovereign
    )
    verifier = _verify_ui_verifier_provenance(sovereign_repo, resolved_sovereign)
    return {
        "core": {
            "accepted_base_commit": ACCEPTED_CORE_COMMIT,
            "candidate_commit": resolved_core,
            "candidate_tree": core_tree,
            "lineage_verified": True,
        },
        "sovereign": {
            "accepted_base_commit": ACCEPTED_SOVEREIGN_COMMIT,
            "candidate_commit": resolved_sovereign,
            "candidate_tree": sovereign_tree,
            "lineage_verified": True,
        },
        "compatibility": {
            "contracts": contracts,
            "controls": controls,
            "panel": panel,
        },
        "ui_verifier": verifier,
    }


def _validate_public_evidence(evidence: dict[str, Any]) -> None:
    for claim in (
        "staging_deployed",
        "staging_validated",
        "image_built",
        "auth_iam_ready",
        "durable_operational_controls_ready",
        "real_provider_phi_allowed",
        "production_ready",
    ):
        if evidence.get(claim) is not False:
            _fail("A deferred readiness claim was promoted")
    serialized = _canonical_json(evidence)
    if len(serialized.encode("ascii")) > MAX_OUTPUT_BYTES:
        _fail("The canonical evidence exceeds its output bound")
    forbidden = (
        "authorization",
        "bearer ",
        "client_secret",
        "api_key",
        "private_key",
        "patient_name",
        "provider_identifier",
        os.fspath(ROOT),
    )
    if any(item.lower() in serialized.lower() for item in forbidden):
        _fail("The canonical evidence violates the metadata allowlist")
    if re.search(r"(?<![A-Za-z0-9])/(?:Users|home|private|tmp|var)/", serialized):
        _fail("The canonical evidence contains an absolute filesystem path")


def reconcile(
    *,
    core_repo: Path,
    core_commit: str,
    sovereign_repo: Path,
    sovereign_commit: str,
    ui_receipt: Path,
) -> dict[str, Any]:
    """Return a bounded source-only packet for the exact candidate pair."""
    source = verify_source_pair(
        core_repo=core_repo,
        core_commit=core_commit,
        sovereign_repo=sovereign_repo,
        sovereign_commit=sovereign_commit,
    )
    verifier = source.pop("ui_verifier")
    receipt = _read_receipt_once(ui_receipt)
    _run_existing_ui_verifier(sovereign_repo, verifier, receipt)

    evidence: dict[str, Any] = {
        "schema_version": SCHEMA,
        "source_pair_reconciled": True,
        "core": source["core"],
        "sovereign": source["sovereign"],
        "compatibility": source["compatibility"],
        "ui_operator_attestation": {
            "existing_verifier_validated": True,
            "operator_observation_proven": False,
            "authority": "closed-metadata-and-pinned-automated-support-only",
            "receipt_file_sha256": "sha256:" + _sha256(receipt),
        },
        "staging_deployed": False,
        "staging_validated": False,
        "image_built": False,
        "auth_iam_ready": False,
        "durable_operational_controls_ready": False,
        "real_provider_phi_allowed": False,
        "production_ready": False,
    }
    _validate_public_evidence(evidence)
    canonical = _canonical_json(evidence)
    return {
        "evidence": evidence,
        "evidence_sha256": "sha256:" + _sha256(canonical.encode("ascii")),
    }


class _SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        _fail("The command invocation is invalid")


def _parser() -> argparse.ArgumentParser:
    parser = _SanitizedArgumentParser(
        description="Reconcile one exact synthetic-only Session Prep source pair."
    )
    parser.add_argument("--core-repo", required=True, type=Path)
    parser.add_argument("--core-commit", required=True)
    parser.add_argument("--sovereign-repo", default=ROOT, type=Path)
    parser.add_argument("--sovereign-commit", required=True)
    parser.add_argument("--ui-receipt", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        packet = reconcile(
            core_repo=arguments.core_repo,
            core_commit=arguments.core_commit,
            sovereign_repo=arguments.sovereign_repo,
            sovereign_commit=arguments.sovereign_commit,
            ui_receipt=arguments.ui_receipt,
        )
        output = _canonical_json(packet)
        if (
            not output.startswith(PASS_PREFIX)
            or len(output.encode("ascii")) > MAX_OUTPUT_BYTES
        ):
            _fail("The canonical packet exceeds its output boundary")
    except Exception:
        print(FAIL_SUMMARY, file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
