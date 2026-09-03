"""Fail-closed tests for the exact Session Prep source-candidate reconciler."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import reconcile_session_prep_source_candidate as candidate


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write(repo: Path, path: str, value: str | bytes) -> None:
    destination = repo / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        destination.write_bytes(value)
    else:
        destination.write_text(value, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "user.name=Source Candidate Test",
        "-c",
        "user.email=source-candidate@example.invalid",
        "commit",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def _tree(repo: Path, commit: str) -> str:
    return _git(repo, "rev-parse", f"{commit}^{{tree}}")


def _initialize(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "--quiet")


def _contracts(repo: Path) -> None:
    for path in candidate.CONTRACT_PATHS:
        _write(repo, path, f'{{"synthetic_contract":"{Path(path).name}"}}\n')


def _fake_ui_verifier() -> str:
    return """#!/usr/bin/env python3
import datetime as dt
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as stream:
        receipt = json.load(stream)
    if set(receipt) != {"observed_at_utc", "status"}:
        raise ValueError
    observed = dt.datetime.strptime(
        receipt["observed_at_utc"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=dt.UTC)
    age = dt.datetime.now(dt.UTC) - observed
    if receipt["status"] != "pass" or age < dt.timedelta(0):
        raise ValueError
    if age > dt.timedelta(hours=24):
        raise ValueError
except Exception:
    sys.stderr.write("receipt rejected with synthetic secret and path\\n")
    raise SystemExit(1)
print("[PASS] receipt structure and pinned automated support validated; the synthetic UI operator attestation remains unproven.")
"""


def _core_runtime(default: str = "False") -> str:
    return (
        "from decouple import config\n"
        'enabled = config("SESSION_PREP_SYNTHETIC_ENABLED", '
        f"default={default}, cast=bool)\n"
    )


def _sovereign_runtime(default: str = "False") -> str:
    return f"class Settings:\n    feature_session_prep: bool = {default}\n"


def _fresh_receipt(tmp_path: Path, **extra: object) -> Path:
    observed = dt.datetime.now(dt.UTC).replace(microsecond=0)
    value: dict[str, object] = {
        "observed_at_utc": observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "pass",
        **extra,
    }
    path = tmp_path / "ui-receipt.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _stale_receipt(tmp_path: Path) -> Path:
    observed = dt.datetime.now(dt.UTC) - dt.timedelta(hours=25)
    path = tmp_path / "stale-ui-receipt.json"
    path.write_text(
        json.dumps(
            {
                "observed_at_utc": observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "pass",
            }
        ),
        encoding="utf-8",
    )
    return path


def _pin_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    core: Path,
    accepted_core: str,
    core_commit: str,
    sovereign: Path,
    panel_source: str,
    accepted_sovereign: str,
    ui_source: str,
    ui_verifier_source: str,
    sovereign_commit: str,
    panel: bytes,
) -> None:
    monkeypatch.setattr(candidate, "ACCEPTED_CORE_COMMIT", accepted_core)
    monkeypatch.setattr(candidate, "ACCEPTED_CORE_TREE", _tree(core, accepted_core))
    monkeypatch.setattr(candidate, "ACCEPTED_SOVEREIGN_COMMIT", accepted_sovereign)
    monkeypatch.setattr(
        candidate, "ACCEPTED_SOVEREIGN_TREE", _tree(sovereign, accepted_sovereign)
    )
    monkeypatch.setattr(candidate, "CANDIDATE_CORE_COMMIT", core_commit)
    monkeypatch.setattr(candidate, "CANDIDATE_CORE_TREE", _tree(core, core_commit))
    monkeypatch.setattr(candidate, "CANDIDATE_SOVEREIGN_COMMIT", sovereign_commit)
    monkeypatch.setattr(
        candidate, "CANDIDATE_SOVEREIGN_TREE", _tree(sovereign, sovereign_commit)
    )
    monkeypatch.setattr(
        candidate,
        "CANDIDATE_PAIR",
        (
            (core_commit, _tree(core, core_commit)),
            (sovereign_commit, _tree(sovereign, sovereign_commit)),
        ),
    )
    monkeypatch.setattr(candidate, "PANEL_SOURCE_COMMIT", panel_source)
    monkeypatch.setattr(candidate, "PANEL_SOURCE_TREE", _tree(sovereign, panel_source))
    digest = hashlib.sha256(panel).digest()
    panel_sha = digest.hex()
    monkeypatch.setattr(candidate, "CORE_PANEL_ASSET_SHA256", panel_sha)
    monkeypatch.setattr(
        candidate,
        "CORE_PANEL_ASSET_PATH",
        f"web/public/vendor/cosmaneura-panel.v1.{panel_sha}.js",
    )
    monkeypatch.setattr(candidate, "PANEL_SHA256", panel_sha)
    monkeypatch.setattr(
        candidate,
        "PANEL_SRI",
        "sha256-" + base64.b64encode(digest).decode("ascii"),
    )
    constants = (core / candidate.CORE_PANEL_CONSTANTS_PATH).read_bytes()
    monkeypatch.setattr(
        candidate, "CORE_PANEL_CONSTANTS_SHA256", hashlib.sha256(constants).hexdigest()
    )
    monkeypatch.setattr(
        candidate,
        "CONTROL_SHA256",
        {
            ("core", candidate.CORE_RUNTIME_CONFIG_PATH): hashlib.sha256(
                (core / candidate.CORE_RUNTIME_CONFIG_PATH).read_bytes()
            ).hexdigest(),
            ("core", candidate.CORE_STAGING_DEPLOY_PATH): hashlib.sha256(
                (core / candidate.CORE_STAGING_DEPLOY_PATH).read_bytes()
            ).hexdigest(),
            ("core", candidate.CORE_STAGING_WORKFLOW_PATH): hashlib.sha256(
                (core / candidate.CORE_STAGING_WORKFLOW_PATH).read_bytes()
            ).hexdigest(),
            ("sovereign", candidate.SOVEREIGN_CONFIG_PATH): hashlib.sha256(
                (sovereign / candidate.SOVEREIGN_CONFIG_PATH).read_bytes()
            ).hexdigest(),
        },
    )
    monkeypatch.setattr(candidate, "UI_SOURCE_COMMIT", ui_source)
    monkeypatch.setattr(candidate, "UI_SOURCE_TREE", _tree(sovereign, ui_source))
    monkeypatch.setattr(candidate, "UI_VERIFIER_COMMIT", ui_verifier_source)
    monkeypatch.setattr(
        candidate,
        "UI_VERIFIER_TREE",
        _tree(sovereign, ui_verifier_source),
    )
    verifier = (sovereign / candidate.UI_VERIFIER_PATH).read_bytes()
    monkeypatch.setattr(
        candidate, "UI_VERIFIER_SHA256", hashlib.sha256(verifier).hexdigest()
    )


@pytest.fixture
def repositories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str, Path, str]:
    panel = b"window.CosmaNeuraPanel={mount:function(){}};\n"
    sovereign = tmp_path / "sovereign"
    _initialize(sovereign)
    _contracts(sovereign)
    for path in candidate.SOVEREIGN_RUNTIME_PATHS:
        if path == candidate.SOVEREIGN_CONFIG_PATH:
            value: str | bytes = _sovereign_runtime()
        elif path == candidate.SOVEREIGN_PANEL_PATH:
            value = panel
        else:
            value = f"# accepted synthetic source: {path}\n"
        _write(sovereign, path, value)
    panel_source = _commit(sovereign, "panel source")
    _write(sovereign, "accepted.txt", "accepted Session Prep base\n")
    accepted_sovereign = _commit(sovereign, "accepted Sovereign base")
    _write(sovereign, "ui-source.txt", "synthetic UI source\n")
    ui_source = _commit(sovereign, "UI source")
    _write(sovereign, candidate.UI_VERIFIER_PATH, _fake_ui_verifier())
    ui_verifier_source = _commit(sovereign, "UI evidence verifier")
    _write(sovereign, "candidate.txt", "exact Sovereign candidate\n")
    sovereign_commit = _commit(sovereign, "Sovereign source candidate")

    core = tmp_path / "core"
    _initialize(core)
    _contracts(core)
    digest = hashlib.sha256(panel).hexdigest()
    for path in candidate.CORE_RUNTIME_PATHS:
        if path == candidate.CORE_RUNTIME_CONFIG_PATH:
            value = _core_runtime()
        elif path == candidate.CORE_PANEL_CONSTANTS_PATH:
            value = "export const acceptedPanel = true\n"
        elif path == candidate.CORE_STAGING_DEPLOY_PATH:
            value = 'SESSION_PREP_SYNTHETIC_ENABLED: "false"\nforced_false\n'
        elif path == candidate.CORE_STAGING_WORKFLOW_PATH:
            value = 'SESSION_PREP_SYNTHETIC_ENABLED: "false"\n'
        else:
            value = f"# accepted synthetic source: {path}\n"
        _write(core, path, value)
    _write(
        core,
        f"web/public/vendor/cosmaneura-panel.v1.{digest}.js",
        panel,
    )
    accepted_core = _commit(core, "accepted Core base")
    _write(core, "candidate.txt", "exact Core candidate\n")
    core_commit = _commit(core, "Core source candidate")

    _pin_fixture(
        monkeypatch,
        core=core,
        accepted_core=accepted_core,
        core_commit=core_commit,
        sovereign=sovereign,
        panel_source=panel_source,
        accepted_sovereign=accepted_sovereign,
        ui_source=ui_source,
        ui_verifier_source=ui_verifier_source,
        sovereign_commit=sovereign_commit,
        panel=panel,
    )
    return core, core_commit, sovereign, sovereign_commit


def _reconcile(
    repositories: tuple[Path, str, Path, str], receipt: Path
) -> dict[str, Any]:
    core, core_commit, sovereign, sovereign_commit = repositories
    return candidate.reconcile(
        core_repo=core,
        core_commit=core_commit,
        sovereign_repo=sovereign,
        sovereign_commit=sovereign_commit,
        ui_receipt=receipt,
    )


def _advance_candidate(
    monkeypatch: pytest.MonkeyPatch,
    repositories: tuple[Path, str, Path, str],
    *,
    repository: str,
) -> tuple[Path, str, Path, str]:
    core, core_commit, sovereign, sovereign_commit = repositories
    if repository == "core":
        core_commit = _commit(core, "mutated Core candidate")
        monkeypatch.setattr(candidate, "CANDIDATE_CORE_COMMIT", core_commit)
        monkeypatch.setattr(candidate, "CANDIDATE_CORE_TREE", _tree(core, core_commit))
    else:
        sovereign_commit = _commit(sovereign, "mutated Sovereign candidate")
        monkeypatch.setattr(candidate, "CANDIDATE_SOVEREIGN_COMMIT", sovereign_commit)
        monkeypatch.setattr(
            candidate, "CANDIDATE_SOVEREIGN_TREE", _tree(sovereign, sovereign_commit)
        )
    monkeypatch.setattr(
        candidate,
        "CANDIDATE_PAIR",
        (
            (core_commit, _tree(core, core_commit)),
            (sovereign_commit, _tree(sovereign, sovereign_commit)),
        ),
    )
    return core, core_commit, sovereign, sovereign_commit


def test_reconciliation_is_deterministic_canonical_and_source_only(
    repositories: tuple[Path, str, Path, str], tmp_path: Path
) -> None:
    core, _core_commit, sovereign, _sovereign_commit = repositories
    receipt = _fresh_receipt(tmp_path)

    # Mutable files may be hostile; all source/verifier reads must remain bound
    # to the candidate Git objects.
    _write(core, sorted(candidate.CONTRACT_PATHS)[0], '{"patient_name":"mutable"}')
    _write(core, candidate.CORE_RUNTIME_CONFIG_PATH, _core_runtime("True"))
    _write(sovereign, candidate.SOVEREIGN_CONFIG_PATH, _sovereign_runtime("True"))
    _write(sovereign, candidate.UI_VERIFIER_PATH, "raise SystemExit(99)\n")

    first = _reconcile(repositories, receipt)
    second = _reconcile(repositories, receipt)
    assert first == second
    evidence = first["evidence"]
    assert evidence["source_pair_reconciled"] is True
    assert evidence["compatibility"]["contracts"]["file_count"] == 9
    assert evidence["compatibility"]["controls"]["file_count"] == 18
    assert evidence["ui_operator_attestation"] == {
        "authority": "closed-metadata-and-pinned-automated-support-only",
        "existing_verifier_validated": True,
        "operator_observation_proven": False,
        "receipt_file_sha256": "sha256:"
        + hashlib.sha256(receipt.read_bytes()).hexdigest(),
    }
    for claim in (
        "staging_deployed",
        "staging_validated",
        "image_built",
        "auth_iam_ready",
        "durable_operational_controls_ready",
        "real_provider_phi_allowed",
        "production_ready",
    ):
        assert evidence[claim] is False
    serialized = candidate._canonical_json(first)
    assert serialized == json.dumps(first, separators=(",", ":"), sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "patient_name" not in serialized
    assert "context_synthetic_" not in serialized
    canonical_evidence = candidate._canonical_json(evidence).encode("ascii")
    assert first["evidence_sha256"] == (
        "sha256:" + hashlib.sha256(canonical_evidence).hexdigest()
    )


@pytest.mark.parametrize(
    "bad_commit",
    ["HEAD", "f" * 7, "F" * 40, "f" * 39, "f" * 41],
)
def test_non_exact_or_unapproved_commit_is_rejected(
    repositories: tuple[Path, str, Path, str], bad_commit: str
) -> None:
    core, _core_commit, sovereign, sovereign_commit = repositories
    with pytest.raises(candidate.ReconciliationError):
        candidate.verify_source_pair(
            core_repo=core,
            core_commit=bad_commit,
            sovereign_repo=sovereign,
            sovereign_commit=sovereign_commit,
        )


def test_missing_exact_commit_is_rejected(
    repositories: tuple[Path, str, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    core, _core_commit, sovereign, sovereign_commit = repositories
    missing = "f" * 40
    monkeypatch.setattr(candidate, "CANDIDATE_CORE_COMMIT", missing)
    monkeypatch.setattr(candidate, "CANDIDATE_CORE_TREE", "e" * 40)
    monkeypatch.setattr(
        candidate,
        "CANDIDATE_PAIR",
        ((missing, "e" * 40), (sovereign_commit, _tree(sovereign, sovereign_commit))),
    )
    with pytest.raises(candidate.ReconciliationError, match="unavailable"):
        candidate.verify_source_pair(
            core_repo=core,
            core_commit=missing,
            sovereign_repo=sovereign,
            sovereign_commit=sovereign_commit,
        )


def test_wrong_candidate_tree_is_rejected(
    repositories: tuple[Path, str, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    core, core_commit, sovereign, sovereign_commit = repositories
    wrong_tree = "0" * 40
    monkeypatch.setattr(candidate, "CANDIDATE_CORE_TREE", wrong_tree)
    monkeypatch.setattr(
        candidate,
        "CANDIDATE_PAIR",
        (
            (core_commit, wrong_tree),
            (sovereign_commit, _tree(sovereign, sovereign_commit)),
        ),
    )
    with pytest.raises(candidate.ReconciliationError, match="tree"):
        candidate.verify_source_pair(
            core_repo=core,
            core_commit=core_commit,
            sovereign_repo=sovereign,
            sovereign_commit=sovereign_commit,
        )


def test_pair_splicing_is_rejected_before_object_acceptance(
    repositories: tuple[Path, str, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    core, _core_commit, sovereign, sovereign_commit = repositories
    _write(core, "later.txt", "otherwise compatible descendant\n")
    later = _commit(core, "later Core descendant")
    monkeypatch.setattr(candidate, "CANDIDATE_CORE_COMMIT", later)
    monkeypatch.setattr(candidate, "CANDIDATE_CORE_TREE", _tree(core, later))
    # Deliberately leave the ordered pair allowlist unchanged.
    with pytest.raises(candidate.ReconciliationError, match="exact source pair"):
        candidate.verify_source_pair(
            core_repo=core,
            core_commit=later,
            sovereign_repo=sovereign,
            sovereign_commit=sovereign_commit,
        )


def test_non_descendant_candidate_is_rejected(
    repositories: tuple[Path, str, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    core, _core_commit, sovereign, sovereign_commit = repositories
    accepted_tree = _tree(core, candidate.ACCEPTED_CORE_COMMIT)
    orphan = _git(
        core,
        "-c",
        "user.name=Source Candidate Test",
        "-c",
        "user.email=source-candidate@example.invalid",
        "commit-tree",
        accepted_tree,
        "-m",
        "non-descendant candidate",
    )
    monkeypatch.setattr(candidate, "CANDIDATE_CORE_COMMIT", orphan)
    monkeypatch.setattr(candidate, "CANDIDATE_CORE_TREE", accepted_tree)
    monkeypatch.setattr(
        candidate,
        "CANDIDATE_PAIR",
        (
            (orphan, accepted_tree),
            (sovereign_commit, _tree(sovereign, sovereign_commit)),
        ),
    )
    with pytest.raises(candidate.ReconciliationError, match="not a descendant"):
        candidate.verify_source_pair(
            core_repo=core,
            core_commit=orphan,
            sovereign_repo=sovereign,
            sovereign_commit=sovereign_commit,
        )


def test_non_descendant_panel_source_is_rejected(
    repositories: tuple[Path, str, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _core, _core_commit, sovereign, sovereign_commit = repositories
    source_tree = _tree(sovereign, candidate.PANEL_SOURCE_COMMIT)
    orphan = _git(
        sovereign,
        "-c",
        "user.name=Source Candidate Test",
        "-c",
        "user.email=source-candidate@example.invalid",
        "commit-tree",
        source_tree,
        "-m",
        "non-descendant panel source",
    )
    monkeypatch.setattr(candidate, "PANEL_SOURCE_COMMIT", orphan)
    monkeypatch.setattr(candidate, "PANEL_SOURCE_TREE", source_tree)
    with pytest.raises(candidate.ReconciliationError, match="not a descendant"):
        core, core_commit, sovereign, sovereign_commit = repositories
        candidate.verify_source_pair(
            core_repo=core,
            core_commit=core_commit,
            sovereign_repo=sovereign,
            sovereign_commit=sovereign_commit,
        )


def test_git_replacement_objects_are_ignored(
    repositories: tuple[Path, str, Path, str], tmp_path: Path
) -> None:
    core, _core_commit, sovereign, sovereign_commit = repositories
    receipt = _fresh_receipt(tmp_path)
    _write(sovereign, candidate.SOVEREIGN_CONFIG_PATH, _sovereign_runtime("True"))
    unsafe = _commit(sovereign, "unsafe replacement commit")
    _git(sovereign, "replace", sovereign_commit, unsafe)
    assert "True" in _git(
        sovereign, "show", f"{sovereign_commit}:{candidate.SOVEREIGN_CONFIG_PATH}"
    )
    assert _reconcile(repositories, receipt)["evidence"]["source_pair_reconciled"]


def test_git_commands_disable_lazy_fetch_and_replacement_objects(
    repositories: tuple[Path, str, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    core, core_commit, _sovereign, _sovereign_commit = repositories
    real_run = candidate.subprocess.run
    observed: list[tuple[list[str], dict[str, str]]] = []

    def recording_run(command: list[str], **kwargs: Any) -> Any:
        observed.append((command, kwargs["env"]))
        return real_run(command, **kwargs)

    monkeypatch.setattr(candidate.subprocess, "run", recording_run)
    candidate._run_git(core, ["rev-parse", "--verify", f"{core_commit}^{{commit}}"])
    command, environment = observed[0]
    assert "--no-replace-objects" in command
    assert environment["GIT_NO_LAZY_FETCH"] == "1"
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert "GIT_DIR" not in environment


def test_nonregular_source_object_fails_closed(
    repositories: tuple[Path, str, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    core, _core_commit, _sovereign, _sovereign_commit = repositories
    path = core / candidate.CORE_RUNTIME_CONFIG_PATH
    path.unlink()
    path.symlink_to("../../candidate.txt")
    repositories = _advance_candidate(monkeypatch, repositories, repository="core")
    with pytest.raises(candidate.ReconciliationError, match="regular Git blob"):
        core, core_commit, sovereign, sovereign_commit = repositories
        candidate.verify_source_pair(
            core_repo=core,
            core_commit=core_commit,
            sovereign_repo=sovereign,
            sovereign_commit=sovereign_commit,
        )


@pytest.mark.parametrize("drift", ["changed", "missing", "extra"])
def test_contract_drift_fails_closed(
    repositories: tuple[Path, str, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    core, _core_commit, _sovereign, _sovereign_commit = repositories
    path = sorted(candidate.CONTRACT_PATHS)[0]
    if drift == "changed":
        _write(core, path, '{"synthetic_contract":"changed"}\n')
    elif drift == "missing":
        (core / path).unlink()
        _git(core, "add", "-u")
    else:
        _write(
            core,
            f"contracts/fixtures/{candidate.CONTRACT_DIRECTORY}/unexpected.json",
            "{}\n",
        )
    repositories = _advance_candidate(monkeypatch, repositories, repository="core")
    with pytest.raises(candidate.ReconciliationError, match="contract"):
        core, core_commit, sovereign, sovereign_commit = repositories
        candidate.verify_source_pair(
            core_repo=core,
            core_commit=core_commit,
            sovereign_repo=sovereign,
            sovereign_commit=sovereign_commit,
        )


@pytest.mark.parametrize(
    ("repository", "path", "value"),
    [
        ("core", candidate.CORE_PANEL_CONSTANTS_PATH, "changed constants\n"),
        ("core", "<candidate-panel-asset>", "changed panel\n"),
        ("sovereign", candidate.SOVEREIGN_PANEL_PATH, "changed panel\n"),
    ],
)
def test_panel_or_panel_control_drift_fails_closed(
    repositories: tuple[Path, str, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    repository: str,
    path: str,
    value: str,
) -> None:
    core, _core_commit, sovereign, _sovereign_commit = repositories
    if path == "<candidate-panel-asset>":
        path = candidate.CORE_PANEL_ASSET_PATH
    _write(core if repository == "core" else sovereign, path, value)
    repositories = _advance_candidate(monkeypatch, repositories, repository=repository)
    with pytest.raises(candidate.ReconciliationError, match="panel"):
        core, core_commit, sovereign, sovereign_commit = repositories
        candidate.verify_source_pair(
            core_repo=core,
            core_commit=core_commit,
            sovereign_repo=sovereign,
            sovereign_commit=sovereign_commit,
        )


@pytest.mark.parametrize(
    ("repository", "path", "value"),
    [
        ("core", candidate.CORE_RUNTIME_CONFIG_PATH, _core_runtime("True")),
        (
            "core",
            candidate.CORE_STAGING_DEPLOY_PATH,
            'SESSION_PREP_SYNTHETIC_ENABLED: "true"\n',
        ),
        (
            "core",
            candidate.CORE_STAGING_WORKFLOW_PATH,
            'SESSION_PREP_SYNTHETIC_ENABLED: "true"\n',
        ),
        ("sovereign", candidate.SOVEREIGN_CONFIG_PATH, _sovereign_runtime("True")),
    ],
)
def test_runtime_or_staging_gate_drift_fails_closed(
    repositories: tuple[Path, str, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    repository: str,
    path: str,
    value: str,
) -> None:
    core, _core_commit, sovereign, _sovereign_commit = repositories
    _write(core if repository == "core" else sovereign, path, value)
    repositories = _advance_candidate(monkeypatch, repositories, repository=repository)
    with pytest.raises(candidate.ReconciliationError, match="runtime or control"):
        core, core_commit, sovereign, sovereign_commit = repositories
        candidate.verify_source_pair(
            core_repo=core,
            core_commit=core_commit,
            sovereign_repo=sovereign,
            sovereign_commit=sovereign_commit,
        )


@pytest.mark.parametrize(
    "source",
    [
        _core_runtime("True"),
        'enabled = config("SESSION_PREP_SYNTHETIC_ENABLED", cast=bool)\n',
        _core_runtime() + _core_runtime("True"),
    ],
)
def test_core_runtime_parser_rejects_default_on_missing_or_duplicate(
    source: str,
) -> None:
    assert candidate._core_runtime_default_off(source) is False


@pytest.mark.parametrize(
    "source",
    [
        _sovereign_runtime("True"),
        _sovereign_runtime() + "Settings.feature_session_prep = True\n",
        _sovereign_runtime() + "feature_session_prep = True\n",
        _sovereign_runtime() + 'setattr(Settings, "feature_session_prep", True)\n',
        "class Settings:\n    feature_session_prep = False\n",
    ],
)
def test_sovereign_runtime_parser_rejects_default_on_or_reassignment(
    source: str,
) -> None:
    assert candidate._sovereign_runtime_default_off(source) is False


def test_stale_tampered_and_unknown_ui_receipts_are_rejected(
    repositories: tuple[Path, str, Path, str], tmp_path: Path
) -> None:
    stale = _stale_receipt(tmp_path)
    with pytest.raises(candidate.ReconciliationError, match="receipt was rejected"):
        _reconcile(repositories, stale)

    tampered = _fresh_receipt(tmp_path)
    tampered.write_text("{}", encoding="utf-8")
    with pytest.raises(candidate.ReconciliationError, match="receipt was rejected"):
        _reconcile(repositories, tampered)

    unknown = _fresh_receipt(tmp_path, notes="free form is forbidden")
    with pytest.raises(candidate.ReconciliationError, match="receipt was rejected"):
        _reconcile(repositories, unknown)


def test_ui_verifier_drift_fails_closed(
    repositories: tuple[Path, str, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _core, _core_commit, sovereign, _sovereign_commit = repositories
    _write(sovereign, candidate.UI_VERIFIER_PATH, "raise SystemExit(0)\n")
    repositories = _advance_candidate(monkeypatch, repositories, repository="sovereign")
    with pytest.raises(candidate.ReconciliationError, match="verifier bytes drifted"):
        core, core_commit, sovereign, sovereign_commit = repositories
        candidate.verify_source_pair(
            core_repo=core,
            core_commit=core_commit,
            sovereign_repo=sovereign,
            sovereign_commit=sovereign_commit,
        )


def test_receipt_symlink_and_size_bound_fail_closed(
    repositories: tuple[Path, str, Path, str], tmp_path: Path
) -> None:
    target = _fresh_receipt(tmp_path)
    link = tmp_path / "receipt-link.json"
    link.symlink_to(target)
    with pytest.raises(candidate.ReconciliationError):
        _reconcile(repositories, link)

    large = tmp_path / "large.json"
    large.write_bytes(b"x" * (candidate.MAX_RECEIPT_BYTES + 1))
    with pytest.raises(candidate.ReconciliationError, match="maximum size"):
        _reconcile(repositories, large)


def test_failure_output_is_constant_bounded_and_sanitized(
    repositories: tuple[Path, str, Path, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    core, _core_commit, sovereign, sovereign_commit = repositories
    secret_path = tmp_path / "provider_identifier_secret_receipt.json"
    secret_path.write_text("{}", encoding="utf-8")
    result = candidate.main(
        [
            "--core-repo",
            str(core),
            "--core-commit",
            "HEAD",
            "--sovereign-repo",
            str(sovereign),
            "--sovereign-commit",
            sovereign_commit,
            "--ui-receipt",
            str(secret_path),
        ]
    )
    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == candidate.FAIL_SUMMARY + "\n"
    assert len(captured.err.encode()) < 128
    assert str(tmp_path) not in captured.err
    assert "provider_identifier" not in captured.err


def test_child_failure_output_is_never_relayed(
    repositories: tuple[Path, str, Path, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    core, core_commit, sovereign, sovereign_commit = repositories
    receipt = _fresh_receipt(tmp_path)
    receipt.write_text("{}", encoding="utf-8")
    result = candidate.main(
        [
            "--core-repo",
            str(core),
            "--core-commit",
            core_commit,
            "--sovereign-repo",
            str(sovereign),
            "--sovereign-commit",
            sovereign_commit,
            "--ui-receipt",
            str(receipt),
        ]
    )
    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == candidate.FAIL_SUMMARY + "\n"
    assert "synthetic secret" not in captured.err
    assert str(tmp_path) not in captured.err


def test_ui_verifier_timeout_is_fail_closed_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*_args: object, **_kwargs: object) -> Any:
        raise subprocess.TimeoutExpired("secret-command", 90, output=b"secret")

    monkeypatch.setattr(candidate.subprocess, "run", timeout)
    with pytest.raises(candidate.ReconciliationError, match="could not complete"):
        candidate._run_existing_ui_verifier(Path("repo"), b"pass\n", b"{}")


def test_exact_committed_ui_verifier_pin_matches_candidate_history() -> None:
    resolved = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(candidate.ROOT),
            "show",
            f"{candidate.CANDIDATE_SOVEREIGN_COMMIT}:{candidate.UI_VERIFIER_PATH}",
        ],
        check=True,
        capture_output=True,
    ).stdout
    tree = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(candidate.ROOT),
            "rev-parse",
            "--verify",
            f"{candidate.CANDIDATE_SOVEREIGN_COMMIT}^{{tree}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert tree == candidate.CANDIDATE_SOVEREIGN_TREE
    assert hashlib.sha256(resolved).hexdigest() == candidate.UI_VERIFIER_SHA256


def test_public_evidence_cannot_promote_any_deferred_gate() -> None:
    evidence: dict[str, object] = {
        "staging_deployed": False,
        "staging_validated": False,
        "image_built": False,
        "auth_iam_ready": False,
        "durable_operational_controls_ready": False,
        "real_provider_phi_allowed": False,
        "production_ready": False,
    }
    candidate._validate_public_evidence(evidence)
    for claim in tuple(evidence):
        promoted = dict(evidence)
        promoted[claim] = True
        with pytest.raises(candidate.ReconciliationError, match="promoted"):
            candidate._validate_public_evidence(promoted)


def test_cli_argument_errors_do_not_echo_supplied_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = candidate.main(["--unknown-secret", "provider_identifier_value"])
    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == candidate.FAIL_SUMMARY + "\n"


def test_missing_runtime_fails_closed_without_child_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        candidate.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=127, stdout=b"secret", stderr=b"provider path"
        ),
    )
    with pytest.raises(candidate.ReconciliationError, match="receipt was rejected"):
        candidate._run_existing_ui_verifier(Path("repo"), b"pass\n", b"{}")
