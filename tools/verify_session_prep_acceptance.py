#!/usr/bin/env python3
"""Verify the exact, synthetic-only Session Prep cross-repository slice.

The verifier reads repository content from explicit Git commits.  It never
uses either repository's mutable working tree and never reports subprocess
output, repository paths, fixture values, or runtime payloads.
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, NoReturn, Sequence


ROOT = Path(__file__).resolve().parents[1]
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
PANEL_CONSTANTS_PATH = "web/app/sovereign/SovereignPanelAsset.ts"
CORE_DEPLOY_PATH = "infra-staging/deploy.sh"
CORE_WORKFLOW_PATH = ".github/workflows/deploy_staging_candidate.yml"
SOVEREIGN_PANEL_PATH = "app/panel.js"
SOVEREIGN_CONFIG_PATH = "app/config.py"
DEMO_PATH = "tools/run_session_prep_demo.py"
HARNESS_PATH = "tests/panel_contract_harness.js"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
PACKET_SCHEMA = "cosmaneura.session-prep.synthetic-acceptance.v1"
REVIEWED_CORE_COMMIT = "3d56d68e285ad0a136904f0632874ac67797db5e"
REVIEWED_CORE_TREE = "eefd766abbe20c51669705c5c6b238ddbd3a5cd4"
REVIEWED_SOVEREIGN_COMMIT = "b4da6defb2c31166fc2902c320cc1aa028e3cc3f"
REVIEWED_SOVEREIGN_TREE = "66b1484a96bd8a5d14fe9bb27ab19d0088bcef03"
REVIEWED_PANEL_SOURCE_COMMIT = "024655ca3a542caf02e46d1b9b209d3c5b4d96d0"
REVIEWED_PANEL_SHA256 = (
    "ab64c6d2135d7b217834363791728ab04fb9d86524f1643cb72f88cfeb4e0d5c"
)
REVIEWED_PANEL_SRI = "sha256-q2TG0hNdeyF4NDY3kXKKsE+52GUk8WQ8ty+Iz+tODVw="
REVIEWED_CORE_SOURCE_SHA256 = {
    PANEL_CONSTANTS_PATH: (
        "4abceffafbee0e2460ac9297078cbb088d8ea0c5ca9c045c8c5c2affbe40e92b"
    ),
    CORE_DEPLOY_PATH: (
        "4a00301db74368b7377c74869786a83be8ca45308fcfd71013835c22d6fc34dc"
    ),
    CORE_WORKFLOW_PATH: (
        "5c5ee07d2748ea41c4983df0a29146955994d968d58b93751d7da84dcfb82aa8"
    ),
}
REVIEWED_SOVEREIGN_SOURCE_SHA256 = {
    SOVEREIGN_CONFIG_PATH: (
        "af7dee5c32443947cf514aaef61701a9545b3a71f1f59ab3fa112dd2b618a376"
    ),
    DEMO_PATH: ("c4326ea1784edc7262ad3d6ae07b265f160874a5bceffdac27ffe199aaefe2b1"),
    HARNESS_PATH: ("777c8076b832959e6cb05829d84f7ca2ee462d7d37b98fd149163ec927a6c6fa"),
}


class VerificationError(RuntimeError):
    """A fail-closed acceptance check failed."""


def _fail(message: str) -> NoReturn:
    raise VerificationError(message)


def _run_git(
    repo: Path,
    arguments: Sequence[str],
    *,
    binary: bool = False,
) -> bytes | str:
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", os.fspath(repo), *arguments],
            check=False,
            capture_output=True,
            text=not binary,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("Git object verification could not be completed")
    if result.returncode != 0:
        _fail("A required Git object is unavailable")
    return result.stdout


def _resolve_exact_commit(repo: Path, supplied: str, label: str) -> tuple[str, str]:
    if COMMIT_PATTERN.fullmatch(supplied) is None:
        _fail(f"{label} must be an exact lowercase 40-character commit ID")
    commit = str(_run_git(repo, ["rev-parse", "--verify", f"{supplied}^{{commit}}"]))
    commit = commit.strip()
    if commit != supplied:
        _fail(f"{label} did not resolve to the supplied commit ID")
    tree = str(_run_git(repo, ["rev-parse", "--verify", f"{commit}^{{tree}}"]))
    tree = tree.strip()
    if COMMIT_PATTERN.fullmatch(tree) is None:
        _fail(f"{label} tree could not be verified")
    return commit, tree


def _git_bytes(repo: Path, commit: str, relative_path: str) -> bytes:
    value = _run_git(repo, ["show", f"{commit}:{relative_path}"], binary=True)
    assert isinstance(value, bytes)
    return value


def _git_text(repo: Path, commit: str, relative_path: str) -> str:
    try:
        return _git_bytes(repo, commit, relative_path).decode("utf-8")
    except UnicodeDecodeError:
        _fail("A required source object is not UTF-8 text")


def _git_blob_id(repo: Path, commit: str, relative_path: str) -> str:
    blob = str(
        _run_git(repo, ["rev-parse", "--verify", f"{commit}:{relative_path}"])
    ).strip()
    if COMMIT_PATTERN.fullmatch(blob) is None:
        _fail("A required contract blob could not be verified")
    return blob


def _contract_paths(repo: Path, commit: str) -> set[str]:
    value = _run_git(
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
        binary=True,
    )
    assert isinstance(value, bytes)
    try:
        return {item.decode("utf-8") for item in value.split(b"\0") if item}
    except UnicodeDecodeError:
        _fail("A contract object path is not UTF-8 text")


def _tree_file_paths(repo: Path, commit: str) -> set[str]:
    value = _run_git(repo, ["ls-tree", "-r", "-z", commit], binary=True)
    assert isinstance(value, bytes)
    paths: set[str] = set()
    try:
        for entry in value.split(b"\0"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            mode, object_type, _object_id = metadata.decode("ascii").split(" ")
            path = raw_path.decode("utf-8")
            if object_type != "blob" or mode not in {"100644", "100755"}:
                _fail("The exact source tree contains an unsupported object type")
            candidate = Path(path)
            if candidate.is_absolute() or ".." in candidate.parts or path in paths:
                _fail("The exact source tree contains an unsafe object path")
            paths.add(path)
    except (UnicodeDecodeError, ValueError):
        _fail("The exact source tree could not be enumerated")
    return paths


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
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
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("Panel source lineage could not be verified")
    if result.returncode not in (0, 1):
        _fail("Panel source lineage could not be verified")
    return result.returncode == 0


def _reviewed_source_provenance(
    core_repo: Path,
    core_commit: str,
    core_tree: str,
    sovereign_repo: Path,
    sovereign_commit: str,
    sovereign_tree: str,
) -> dict[str, Any]:
    """Require the immutable source pair and reviewed control-file manifest."""
    if (
        core_commit != REVIEWED_CORE_COMMIT
        or core_tree != REVIEWED_CORE_TREE
        or sovereign_commit != REVIEWED_SOVEREIGN_COMMIT
        or sovereign_tree != REVIEWED_SOVEREIGN_TREE
    ):
        _fail("The supplied commits are not the reviewed v1 source inputs")

    manifests = {
        "core": REVIEWED_CORE_SOURCE_SHA256,
        "sovereign": REVIEWED_SOVEREIGN_SOURCE_SHA256,
    }
    for repo, commit, expected in (
        (core_repo, core_commit, REVIEWED_CORE_SOURCE_SHA256),
        (sovereign_repo, sovereign_commit, REVIEWED_SOVEREIGN_SOURCE_SHA256),
    ):
        for path, expected_digest in expected.items():
            if SHA256_PATTERN.fullmatch(expected_digest) is None:
                _fail("The reviewed source manifest is invalid")
            actual_digest = hashlib.sha256(_git_bytes(repo, commit, path)).hexdigest()
            if actual_digest != expected_digest:
                _fail("A reviewed source object does not match the v1 manifest")
    canonical_manifest = _canonical_json(manifests).encode("utf-8")
    return {
        "file_count": sum(len(manifest) for manifest in manifests.values()),
        "sha256": f"sha256:{hashlib.sha256(canonical_manifest).hexdigest()}",
    }


def _panel_provenance(
    core_repo: Path,
    core_commit: str,
    sovereign_repo: Path,
    sovereign_commit: str,
) -> tuple[dict[str, Any], bytes]:
    digest = REVIEWED_PANEL_SHA256
    integrity = REVIEWED_PANEL_SRI
    upstream_commit = REVIEWED_PANEL_SOURCE_COMMIT
    expected_asset_path = f"/vendor/cosmaneura-panel.v1.{digest}.js"

    core_asset = _git_bytes(core_repo, core_commit, f"web/public{expected_asset_path}")
    calculated_digest = hashlib.sha256(core_asset).hexdigest()
    calculated_integrity = "sha256-" + base64.b64encode(
        hashlib.sha256(core_asset).digest()
    ).decode("ascii")
    if calculated_digest != digest or calculated_integrity != integrity:
        _fail("The Core panel asset does not match its SHA-256 and SRI pins")

    pinned_commit, _ = _resolve_exact_commit(
        sovereign_repo, upstream_commit, "Core-pinned Sovereign source commit"
    )
    if not _is_ancestor(sovereign_repo, pinned_commit, sovereign_commit):
        _fail("The Core-pinned panel source is not in the supplied source lineage")
    pinned_panel = _git_bytes(sovereign_repo, pinned_commit, SOVEREIGN_PANEL_PATH)
    supplied_panel = _git_bytes(sovereign_repo, sovereign_commit, SOVEREIGN_PANEL_PATH)
    if core_asset != pinned_panel or core_asset != supplied_panel:
        _fail("The Core panel bytes do not match the exact Sovereign source lineage")

    return (
        {
            "byte_equality": True,
            "sha256": f"sha256:{digest}",
            "source_commit": pinned_commit,
            "source_is_ancestor": True,
            "sri": integrity,
        },
        core_asset,
    )


def _contract_provenance(
    core_repo: Path,
    core_commit: str,
    sovereign_repo: Path,
    sovereign_commit: str,
) -> dict[str, Any]:
    core_paths = _contract_paths(core_repo, core_commit)
    sovereign_paths = _contract_paths(sovereign_repo, sovereign_commit)
    if core_paths != CONTRACT_PATHS or sovereign_paths != CONTRACT_PATHS:
        _fail("The Session Prep schema and fixture set is not the exact v1 set")
    core_blobs: dict[str, str] = {}
    sovereign_blobs: dict[str, str] = {}
    for path in sorted(core_paths):
        core_bytes = _git_bytes(core_repo, core_commit, path)
        sovereign_bytes = _git_bytes(sovereign_repo, sovereign_commit, path)
        if core_bytes != sovereign_bytes:
            _fail("A Session Prep schema or fixture differs across repositories")
        core_blobs[path] = _git_blob_id(core_repo, core_commit, path)
        sovereign_blobs[path] = _git_blob_id(sovereign_repo, sovereign_commit, path)

    if core_blobs != sovereign_blobs:
        _fail("Session Prep contract blob identities differ across repositories")
    manifest = _canonical_json(core_blobs).encode("utf-8")
    return {
        "blob_count": len(core_blobs),
        "byte_equality": True,
        "manifest_sha256": f"sha256:{hashlib.sha256(manifest).hexdigest()}",
    }


def _sovereign_feature_default_is_false(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        _fail("The Sovereign configuration source could not be parsed")
    settings_classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Settings"
    ]
    if len(settings_classes) != 1:
        return False
    declarations = [
        node
        for node in settings_classes[0].body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "feature_session_prep"
    ]
    if (
        len(declarations) != 1
        or not isinstance(declarations[0].value, ast.Constant)
        or declarations[0].value.value is not False
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


def _core_staging_forces_false(deploy: bytes, workflow: bytes) -> bool:
    return (
        hashlib.sha256(deploy).hexdigest()
        == REVIEWED_CORE_SOURCE_SHA256[CORE_DEPLOY_PATH]
        and hashlib.sha256(workflow).hexdigest()
        == REVIEWED_CORE_SOURCE_SHA256[CORE_WORKFLOW_PATH]
    )


def _archive_commit(repo: Path, commit: str, destination: Path) -> None:
    expected_paths = _tree_file_paths(repo, commit)
    archive = _run_git(repo, ["archive", "--format=tar", commit], binary=True)
    assert isinstance(archive, bytes)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            tar.extractall(destination, filter="data")
    except (OSError, tarfile.TarError, ValueError):
        _fail("The exact Sovereign source archive could not be extracted")
    actual_paths = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual_paths != expected_paths:
        _fail("The extracted source archive does not match the exact Git tree")
    for path in sorted(expected_paths):
        extracted = destination / path
        if extracted.is_symlink() or extracted.read_bytes() != _git_bytes(
            repo, commit, path
        ):
            _fail("The extracted source archive differs from an exact Git object")


def _run_checked(command: Sequence[str], cwd: Path, timeout_seconds: int) -> None:
    environment = {
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.fspath(cwd),
    }
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            check=False,
            capture_output=True,
            text=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("A local synthetic acceptance command could not be completed")
    if result.returncode != 0:
        _fail("A local synthetic acceptance command failed")


def _run_local_acceptance(
    sovereign_repo: Path,
    sovereign_commit: str,
    core_asset: bytes,
    timeout_seconds: int,
) -> dict[str, bool]:
    node = shutil.which("node")
    if node is None:
        _fail("The Node.js runtime required by the panel harness is unavailable")
    with tempfile.TemporaryDirectory(prefix="session-prep-acceptance-") as raw:
        extracted = Path(raw) / "source"
        extracted.mkdir()
        _archive_commit(sovereign_repo, sovereign_commit, extracted)
        _run_checked(
            [sys.executable, os.fspath(extracted / DEMO_PATH), "--self-test"],
            extracted,
            timeout_seconds,
        )
        asset = Path(raw) / "panel.js"
        asset.write_bytes(core_asset)
        _run_checked(
            [node, os.fspath(extracted / HARNESS_PATH), os.fspath(asset)],
            extracted,
            timeout_seconds,
        )
    return {
        "exact_core_panel_harness": True,
        "four_state_demo_self_test": True,
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _validate_public_evidence(evidence: dict[str, Any]) -> None:
    serialized = _canonical_json(evidence)
    forbidden = (
        "context_snapshot_ref",
        "context_synthetic_",
        "authorization",
        "bearer ",
        "client_secret",
        "api_key",
        "private_key",
        os.fspath(ROOT),
    )
    if any(value.lower() in serialized.lower() for value in forbidden):
        _fail("The acceptance evidence did not satisfy the disclosure allowlist")
    if re.search(r"(?<![A-Za-z0-9])/(?:Users|home|private|tmp|var)/", serialized):
        _fail("The acceptance evidence contains an absolute filesystem path")


def build_packet(
    *,
    core_repo: Path,
    core_commit: str,
    sovereign_repo: Path,
    sovereign_source_commit: str,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Build deterministic evidence for two explicit repository commits."""
    if timeout_seconds < 1 or timeout_seconds > 300:
        _fail("Acceptance command timeout must be between 1 and 300 seconds")
    resolved_core, core_tree = _resolve_exact_commit(
        core_repo, core_commit, "Core commit"
    )
    resolved_sovereign, sovereign_tree = _resolve_exact_commit(
        sovereign_repo, sovereign_source_commit, "Sovereign source commit"
    )
    reviewed_source = _reviewed_source_provenance(
        core_repo,
        resolved_core,
        core_tree,
        sovereign_repo,
        resolved_sovereign,
        sovereign_tree,
    )
    panel, core_asset = _panel_provenance(
        core_repo,
        resolved_core,
        sovereign_repo,
        resolved_sovereign,
    )
    contracts = _contract_provenance(
        core_repo,
        resolved_core,
        sovereign_repo,
        resolved_sovereign,
    )
    sovereign_config = _git_text(
        sovereign_repo, resolved_sovereign, SOVEREIGN_CONFIG_PATH
    )
    if not _sovereign_feature_default_is_false(sovereign_config):
        _fail("Sovereign Session Prep is not default-off at the supplied commit")
    core_deploy = _git_bytes(core_repo, resolved_core, CORE_DEPLOY_PATH)
    core_workflow = _git_bytes(core_repo, resolved_core, CORE_WORKFLOW_PATH)
    if not _core_staging_forces_false(core_deploy, core_workflow):
        _fail("Core canonical staging does not force Session Prep synthetic off")

    local_checks = _run_local_acceptance(
        sovereign_repo,
        resolved_sovereign,
        core_asset,
        timeout_seconds,
    )
    evidence: dict[str, Any] = {
        "auth_iam_ready": False,
        "checks": {
            "contracts": contracts,
            "core_canonical_staging_forces_synthetic_off": True,
            "panel": panel,
            "reviewed_source_manifest": reviewed_source,
            "sovereign_feature_default_off": True,
            **local_checks,
        },
        "core": {"commit": resolved_core, "tree": core_tree},
        "durable_operational_controls_ready": False,
        "local_synthetic_acceptance": "passed",
        "real_provider_phi_allowed": False,
        "schema_version": PACKET_SCHEMA,
        "source_provenance": "passed",
        "sovereign": {
            "commit": resolved_sovereign,
            "tree": sovereign_tree,
        },
        "staging_deployed": False,
    }
    _validate_public_evidence(evidence)
    canonical_evidence = _canonical_json(evidence)
    return {
        "evidence": evidence,
        "evidence_sha256": (
            "sha256:" + hashlib.sha256(canonical_evidence.encode("utf-8")).hexdigest()
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify exact Session Prep source provenance and local synthetic acceptance"
        )
    )
    parser.add_argument("--core-repo", type=Path, required=True)
    parser.add_argument("--core-commit", required=True)
    parser.add_argument("--sovereign-repo", type=Path, default=ROOT)
    parser.add_argument("--sovereign-source-commit", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        packet = build_packet(
            core_repo=arguments.core_repo,
            core_commit=arguments.core_commit,
            sovereign_repo=arguments.sovereign_repo,
            sovereign_source_commit=arguments.sovereign_source_commit,
            timeout_seconds=arguments.timeout_seconds,
        )
    except VerificationError as error:
        print(f"Session Prep acceptance failed: {error}", file=sys.stderr)
        return 1
    print(_canonical_json(packet))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
