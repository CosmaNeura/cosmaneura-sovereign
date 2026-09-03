from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tools import verify_session_prep_acceptance as acceptance


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write(repo: Path, relative_path: str, value: str | bytes) -> None:
    destination = repo / relative_path
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
        "user.name=Acceptance Test",
        "-c",
        "user.email=acceptance@example.invalid",
        "commit",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def _initialize(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "--quiet")


def _contracts(repo: Path) -> None:
    for path in acceptance.CONTRACT_PATHS:
        name = Path(path).name
        _write(repo, path, f'{{"synthetic_contract":"{name}"}}\n')


def _panel_constants(panel: bytes, source_commit: str) -> str:
    digest = hashlib.sha256(panel).hexdigest()
    integrity = base64.b64encode(hashlib.sha256(panel).digest()).decode("ascii")
    return f'''export const SOVEREIGN_PANEL_SHA256 = "{digest}"
export const SOVEREIGN_PANEL_ASSET_PATH =
  `/vendor/cosmaneura-panel.v1.${{SOVEREIGN_PANEL_SHA256}}.js`
export const SOVEREIGN_PANEL_INTEGRITY = "sha256-{integrity}"
export const SOVEREIGN_PANEL_UPSTREAM_COMMIT = "{source_commit}"
'''


def _sha256_file(repo: Path, relative_path: str) -> str:
    return hashlib.sha256((repo / relative_path).read_bytes()).hexdigest()


def _pin_reviewed_commits(
    monkeypatch: pytest.MonkeyPatch,
    core: Path,
    core_commit: str,
    sovereign: Path,
    sovereign_commit: str,
) -> None:
    monkeypatch.setattr(acceptance, "REVIEWED_CORE_COMMIT", core_commit)
    monkeypatch.setattr(
        acceptance,
        "REVIEWED_CORE_TREE",
        _git(core, "rev-parse", f"{core_commit}^{{tree}}"),
    )
    monkeypatch.setattr(acceptance, "REVIEWED_SOVEREIGN_COMMIT", sovereign_commit)
    monkeypatch.setattr(
        acceptance,
        "REVIEWED_SOVEREIGN_TREE",
        _git(sovereign, "rev-parse", f"{sovereign_commit}^{{tree}}"),
    )


def _pin_sovereign_source_manifest(
    monkeypatch: pytest.MonkeyPatch, sovereign: Path
) -> None:
    monkeypatch.setattr(
        acceptance,
        "REVIEWED_SOVEREIGN_SOURCE_SHA256",
        {
            path: _sha256_file(sovereign, path)
            for path in (
                acceptance.SOVEREIGN_CONFIG_PATH,
                acceptance.DEMO_PATH,
                acceptance.HARNESS_PATH,
            )
        },
    )


def _pin_reviewed_inputs(
    monkeypatch: pytest.MonkeyPatch,
    core: Path,
    core_commit: str,
    pinned_source: str,
    sovereign: Path,
    sovereign_commit: str,
) -> None:
    _pin_reviewed_commits(monkeypatch, core, core_commit, sovereign, sovereign_commit)
    panel = (sovereign / acceptance.SOVEREIGN_PANEL_PATH).read_bytes()
    panel_digest = hashlib.sha256(panel).digest()
    monkeypatch.setattr(acceptance, "REVIEWED_PANEL_SOURCE_COMMIT", pinned_source)
    monkeypatch.setattr(acceptance, "REVIEWED_PANEL_SHA256", panel_digest.hex())
    monkeypatch.setattr(
        acceptance,
        "REVIEWED_PANEL_SRI",
        "sha256-" + base64.b64encode(panel_digest).decode("ascii"),
    )
    monkeypatch.setattr(
        acceptance,
        "REVIEWED_CORE_SOURCE_SHA256",
        {
            path: _sha256_file(core, path)
            for path in (
                acceptance.PANEL_CONSTANTS_PATH,
                acceptance.CORE_DEPLOY_PATH,
                acceptance.CORE_WORKFLOW_PATH,
            )
        },
    )
    _pin_sovereign_source_manifest(monkeypatch, sovereign)


def _build_repositories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str, str, Path, str]:
    panel = b"window.CosmaNeuraPanel={mount:function(){}};\n"
    sovereign = tmp_path / "sovereign"
    _initialize(sovereign)
    _write(sovereign, acceptance.SOVEREIGN_PANEL_PATH, panel)
    _write(
        sovereign,
        acceptance.SOVEREIGN_CONFIG_PATH,
        "class Settings:\n    feature_session_prep: bool = False\n",
    )
    _write(
        sovereign,
        acceptance.DEMO_PATH,
        (
            "import sys\n"
            "assert sys.argv[1:] == ['--self-test']\n"
            "print('synthetic demo passed')\n"
        ),
    )
    _write(
        sovereign,
        acceptance.HARNESS_PATH,
        (
            '"use strict";\n'
            'const fs=require("fs");\n'
            'if(!fs.readFileSync(process.argv[2],"utf8").includes('
            '"CosmaNeuraPanel")) process.exit(2);\n'
        ),
    )
    _contracts(sovereign)
    pinned_source = _commit(sovereign, "synthetic panel source")
    _write(sovereign, "README.md", "descendant source commit\n")
    sovereign_commit = _commit(sovereign, "retain panel bytes")

    core = tmp_path / "core"
    _initialize(core)
    _contracts(core)
    digest = hashlib.sha256(panel).hexdigest()
    _write(
        core, acceptance.PANEL_CONSTANTS_PATH, _panel_constants(panel, pinned_source)
    )
    _write(
        core,
        f"web/public/vendor/cosmaneura-panel.v1.{digest}.js",
        panel,
    )
    deploy = """ENV_JSON=$(jq '. + {
  SESSION_PREP_SYNTHETIC_ENABLED: "false"
}')
build_configuration_evidence() {
  python3 -c '
flags = {}
forced_false = "SCRIBE_WORKFLOW_ENABLED SESSION_PREP_SYNTHETIC_ENABLED".split()
if any(flags[k] != "false" for k in forced_false):
    fail()
' "$EXPECTED_STAGING_IDENTITY_FINGERPRINTS_SHA256" <<<"$ENV_JSON"
}
"""
    workflow = """name: Deploy Staging Candidate
flags:
  SESSION_PREP_SYNTHETIC_ENABLED: "false"
"""
    _write(core, acceptance.CORE_DEPLOY_PATH, deploy)
    _write(core, acceptance.CORE_WORKFLOW_PATH, workflow)
    core_commit = _commit(core, "exact synthetic core")
    _pin_reviewed_inputs(
        monkeypatch,
        core,
        core_commit,
        pinned_source,
        sovereign,
        sovereign_commit,
    )
    return core, core_commit, pinned_source, sovereign, sovereign_commit


def _packet(repositories: tuple[Path, str, str, Path, str]) -> dict[str, object]:
    core, core_commit, _pinned, sovereign, sovereign_commit = repositories
    return acceptance.build_packet(
        core_repo=core,
        core_commit=core_commit,
        sovereign_repo=sovereign,
        sovereign_source_commit=sovereign_commit,
    )


def test_packet_is_deterministic_sanitized_and_reads_exact_git_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, _core_commit, _pinned, sovereign, _sovereign_commit = repositories

    # Uncommitted hostile values cannot affect exact-object verification.
    _write(
        sovereign,
        acceptance.SOVEREIGN_CONFIG_PATH,
        "class Settings:\n    feature_session_prep: bool = True\n",
    )
    _write(core, acceptance.CORE_DEPLOY_PATH, "SESSION_PREP_SYNTHETIC_ENABLED: true\n")

    first = _packet(repositories)
    second = _packet(repositories)
    assert first == second
    evidence = first["evidence"]
    assert isinstance(evidence, dict)
    assert evidence["source_provenance"] == "passed"
    assert evidence["local_synthetic_acceptance"] == "passed"
    for gate in (
        "staging_deployed",
        "auth_iam_ready",
        "durable_operational_controls_ready",
        "real_provider_phi_allowed",
    ):
        assert evidence[gate] is False
    serialized = acceptance._canonical_json(first)
    assert serialized == json.dumps(first, separators=(",", ":"), sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "context_snapshot_ref" not in serialized
    assert "context_synthetic_" not in serialized
    assert "secret" not in serialized.lower()
    assert "token" not in serialized.lower()
    canonical_evidence = acceptance._canonical_json(evidence).encode()
    assert first["evidence_sha256"] == (
        "sha256:" + hashlib.sha256(canonical_evidence).hexdigest()
    )


def test_panel_byte_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, _core_commit, _pinned, sovereign, sovereign_commit = repositories
    different_panel = b"window.CosmaNeuraPanel={mount:function(){return null}};\n"
    digest = acceptance.REVIEWED_PANEL_SHA256
    _write(
        core,
        f"web/public/vendor/cosmaneura-panel.v1.{digest}.js",
        different_panel,
    )
    mismatched_core = _commit(core, "mismatched panel")
    _pin_reviewed_commits(
        monkeypatch, core, mismatched_core, sovereign, sovereign_commit
    )

    with pytest.raises(acceptance.VerificationError, match="panel asset"):
        acceptance.build_packet(
            core_repo=core,
            core_commit=mismatched_core,
            sovereign_repo=sovereign,
            sovereign_source_commit=sovereign_commit,
        )


def test_contract_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, _core_commit, _pinned, sovereign, sovereign_commit = repositories
    contract = sorted(acceptance.CONTRACT_PATHS)[0]
    _write(core, contract, '{"synthetic_contract":"different"}\n')
    mismatched_core = _commit(core, "mismatched contract")
    _pin_reviewed_commits(
        monkeypatch, core, mismatched_core, sovereign, sovereign_commit
    )

    with pytest.raises(acceptance.VerificationError, match="differs"):
        acceptance.build_packet(
            core_repo=core,
            core_commit=mismatched_core,
            sovereign_repo=sovereign,
            sovereign_source_commit=sovereign_commit,
        )


def test_extra_contract_object_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, _core_commit, _pinned, sovereign, sovereign_commit = repositories
    _write(
        core,
        f"contracts/fixtures/{acceptance.CONTRACT_DIRECTORY}/unexpected.json",
        "{}\n",
    )
    extra_contract = _commit(core, "unexpected contract object")
    _pin_reviewed_commits(
        monkeypatch, core, extra_contract, sovereign, sovereign_commit
    )

    with pytest.raises(acceptance.VerificationError, match="exact v1 set"):
        acceptance.build_packet(
            core_repo=core,
            core_commit=extra_contract,
            sovereign_repo=sovereign,
            sovereign_source_commit=sovereign_commit,
        )


def test_shared_extra_contract_object_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, _core_commit, _pinned, sovereign, _sovereign_commit = repositories
    path = f"contracts/fixtures/{acceptance.CONTRACT_DIRECTORY}/unexpected.json"
    _write(core, path, "{}\n")
    _write(sovereign, path, "{}\n")
    extra_core = _commit(core, "unexpected shared contract object")
    extra_sovereign = _commit(sovereign, "unexpected shared contract object")
    _pin_reviewed_commits(monkeypatch, core, extra_core, sovereign, extra_sovereign)

    with pytest.raises(acceptance.VerificationError, match="exact v1 set"):
        acceptance.build_packet(
            core_repo=core,
            core_commit=extra_core,
            sovereign_repo=sovereign,
            sovereign_source_commit=extra_sovereign,
        )


def test_sovereign_default_on_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, core_commit, _pinned, sovereign, _sovereign_commit = repositories
    _write(
        sovereign,
        acceptance.SOVEREIGN_CONFIG_PATH,
        "class Settings:\n    feature_session_prep: bool = True\n",
    )
    default_on = _commit(sovereign, "unsafe Sovereign default")
    _pin_reviewed_commits(monkeypatch, core, core_commit, sovereign, default_on)
    _pin_sovereign_source_manifest(monkeypatch, sovereign)

    with pytest.raises(acceptance.VerificationError, match="default-off"):
        acceptance.build_packet(
            core_repo=core,
            core_commit=core_commit,
            sovereign_repo=sovereign,
            sovereign_source_commit=default_on,
        )


def test_sovereign_commentary_decoy_and_runtime_override_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, core_commit, _pinned, sovereign, _sovereign_commit = repositories
    _write(
        sovereign,
        acceptance.SOVEREIGN_CONFIG_PATH,
        (
            "class DocumentationExample:\n"
            "    feature_session_prep: bool = False\n\n"
            "class Settings:\n"
            "    feature_session_prep = True\n"
        ),
    )
    default_on = _commit(sovereign, "unsafe runtime override")
    _pin_reviewed_commits(monkeypatch, core, core_commit, sovereign, default_on)
    _pin_sovereign_source_manifest(monkeypatch, sovereign)

    with pytest.raises(acceptance.VerificationError, match="default-off"):
        acceptance.build_packet(
            core_repo=core,
            core_commit=core_commit,
            sovereign_repo=sovereign,
            sovereign_source_commit=default_on,
        )


def test_sovereign_post_class_override_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, core_commit, _pinned, sovereign, _sovereign_commit = repositories
    _write(
        sovereign,
        acceptance.SOVEREIGN_CONFIG_PATH,
        (
            "class Settings:\n"
            "    feature_session_prep: bool = False\n\n"
            "Settings.feature_session_prep = True\n"
        ),
    )
    default_on = _commit(sovereign, "unsafe post-class override")
    _pin_reviewed_commits(monkeypatch, core, core_commit, sovereign, default_on)
    _pin_sovereign_source_manifest(monkeypatch, sovereign)

    with pytest.raises(acceptance.VerificationError, match="default-off"):
        acceptance.build_packet(
            core_repo=core,
            core_commit=core_commit,
            sovereign_repo=sovereign,
            sovereign_source_commit=default_on,
        )


def test_panel_comment_decoys_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, _core_commit, pinned, sovereign, sovereign_commit = repositories
    panel = (sovereign / acceptance.SOVEREIGN_PANEL_PATH).read_bytes()
    valid = _panel_constants(panel, pinned)
    invalid = valid.replace(
        "/vendor/cosmaneura-panel.v1.",
        "/vendor/not-the-pinned-panel.",
    )
    _write(
        core,
        acceptance.PANEL_CONSTANTS_PATH,
        f"/*\n{valid}*/\n{invalid}",
    )
    decoy_commit = _commit(core, "commented panel provenance decoy")
    _pin_reviewed_commits(monkeypatch, core, decoy_commit, sovereign, sovereign_commit)

    with pytest.raises(acceptance.VerificationError, match="reviewed source object"):
        acceptance.build_packet(
            core_repo=core,
            core_commit=decoy_commit,
            sovereign_repo=sovereign,
            sovereign_source_commit=sovereign_commit,
        )


def test_panel_string_literal_decoys_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, _core_commit, pinned, sovereign, sovereign_commit = repositories
    panel = (sovereign / acceptance.SOVEREIGN_PANEL_PATH).read_bytes()
    digest = hashlib.sha256(panel).hexdigest()
    integrity = base64.b64encode(hashlib.sha256(panel).digest()).decode("ascii")
    documented = f'''const documentation = `
export const SOVEREIGN_PANEL_SHA256 = "{digest}"
export const SOVEREIGN_PANEL_ASSET_PATH = "/vendor/cosmaneura-panel.v1.{digest}.js"
export const SOVEREIGN_PANEL_INTEGRITY = "sha256-{integrity}"
export const SOVEREIGN_PANEL_UPSTREAM_COMMIT = "{pinned}"
`;
'''
    invalid = _panel_constants(panel, pinned).replace(
        "/vendor/cosmaneura-panel.v1.",
        "/vendor/not-the-pinned-panel.",
    )
    _write(core, acceptance.PANEL_CONSTANTS_PATH, documented + invalid)
    decoy_commit = _commit(core, "string-literal panel provenance decoy")
    _pin_reviewed_commits(monkeypatch, core, decoy_commit, sovereign, sovereign_commit)

    with pytest.raises(acceptance.VerificationError, match="reviewed source object"):
        acceptance.build_packet(
            core_repo=core,
            core_commit=decoy_commit,
            sovereign_repo=sovereign,
            sovereign_source_commit=sovereign_commit,
        )


def test_core_staging_default_on_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, _core_commit, _pinned, sovereign, sovereign_commit = repositories
    _write(
        core,
        acceptance.CORE_DEPLOY_PATH,
        """ENV_JSON=$(jq '. + {
  SESSION_PREP_SYNTHETIC_ENABLED: "true"
}')
forced_false = "SCRIBE_WORKFLOW_ENABLED"
""",
    )
    _write(
        core,
        acceptance.CORE_WORKFLOW_PATH,
        'flags:\n  SESSION_PREP_SYNTHETIC_ENABLED: "true"\n',
    )
    default_on = _commit(core, "unsafe Core staging default")
    _pin_reviewed_commits(monkeypatch, core, default_on, sovereign, sovereign_commit)

    with pytest.raises(acceptance.VerificationError, match="reviewed source object"):
        acceptance.build_packet(
            core_repo=core,
            core_commit=default_on,
            sovereign_repo=sovereign,
            sovereign_source_commit=sovereign_commit,
        )


def test_core_staging_comment_decoys_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, _core_commit, _pinned, sovereign, sovereign_commit = repositories
    _write(
        core,
        acceptance.CORE_DEPLOY_PATH,
        (
            '# SESSION_PREP_SYNTHETIC_ENABLED: "false"\n'
            '# forced_false = "SESSION_PREP_SYNTHETIC_ENABLED"\n'
            '# flags[k] != "false" for k in forced_false\n'
        ),
    )
    _write(
        core,
        acceptance.CORE_WORKFLOW_PATH,
        '# SESSION_PREP_SYNTHETIC_ENABLED: "false"\n',
    )
    decoy_commit = _commit(core, "commented staging-off decoys")
    _pin_reviewed_commits(monkeypatch, core, decoy_commit, sovereign, sovereign_commit)

    with pytest.raises(acceptance.VerificationError, match="reviewed source object"):
        acceptance.build_packet(
            core_repo=core,
            core_commit=decoy_commit,
            sovereign_repo=sovereign,
            sovereign_source_commit=sovereign_commit,
        )


def test_core_staging_inert_and_unreachable_decoys_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, _core_commit, _pinned, sovereign, sovereign_commit = repositories
    _write(
        core,
        acceptance.CORE_DEPLOY_PATH,
        """: <<'INERT'
SESSION_PREP_SYNTHETIC_ENABLED: "false"
INERT
build_configuration_evidence() {
  python3 -c '
flags = {}
forced_false = "SESSION_PREP_SYNTHETIC_ENABLED".split()
if False and any(flags[k] != "false" for k in forced_false):
    fail()
' "$EXPECTED_STAGING_IDENTITY_FINGERPRINTS_SHA256" <<<"$ENV_JSON"
}
""",
    )
    decoy_commit = _commit(core, "inert staging-off decoys")
    _pin_reviewed_commits(monkeypatch, core, decoy_commit, sovereign, sovereign_commit)

    with pytest.raises(acceptance.VerificationError, match="reviewed source object"):
        acceptance.build_packet(
            core_repo=core,
            core_commit=decoy_commit,
            sovereign_repo=sovereign,
            sovereign_source_commit=sovereign_commit,
        )


def test_non_exact_commit_inputs_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, _core_commit, _pinned, sovereign, sovereign_commit = repositories
    with pytest.raises(acceptance.VerificationError, match="exact lowercase"):
        acceptance.build_packet(
            core_repo=core,
            core_commit="HEAD",
            sovereign_repo=sovereign,
            sovereign_source_commit=sovereign_commit,
        )


def test_unreviewed_exact_commit_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, _core_commit, _pinned, sovereign, sovereign_commit = repositories
    _write(core, "README.md", "unreviewed descendant\n")
    unreviewed_core = _commit(core, "unreviewed exact commit")

    with pytest.raises(acceptance.VerificationError, match="reviewed v1"):
        acceptance.build_packet(
            core_repo=core,
            core_commit=unreviewed_core,
            sovereign_repo=sovereign,
            sovereign_source_commit=sovereign_commit,
        )


def test_git_replace_objects_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    _core, _core_commit, _pinned, sovereign, sovereign_commit = repositories
    original_text = "descendant source commit\n"
    original_blob = _git(sovereign, "rev-parse", f"{sovereign_commit}:README.md")
    replacement_path = tmp_path / "replacement-object.txt"
    replacement_path.write_text("replacement content\n", encoding="utf-8")
    replacement_blob = _git(sovereign, "hash-object", "-w", str(replacement_path))
    _git(sovereign, "replace", original_blob, replacement_blob)

    assert _git(sovereign, "show", f"{sovereign_commit}:README.md") == (
        "replacement content"
    )
    assert (
        acceptance._git_text(sovereign, sovereign_commit, "README.md") == original_text
    )
    extracted = tmp_path / "exact-archive"
    extracted.mkdir()
    acceptance._archive_commit(sovereign, sovereign_commit, extracted)
    assert (extracted / "README.md").read_text(encoding="utf-8") == original_text


def test_failed_child_output_is_not_reported(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repositories = _build_repositories(tmp_path, monkeypatch)
    core, core_commit, _pinned, sovereign, _sovereign_commit = repositories
    _write(
        sovereign,
        acceptance.HARNESS_PATH,
        (
            'process.stderr.write("context_synthetic_forbidden token_forbidden '
            f'{tmp_path}\\n");\nprocess.exit(2);\n'
        ),
    )
    failing_source = _commit(sovereign, "failing harness")
    _pin_reviewed_commits(monkeypatch, core, core_commit, sovereign, failing_source)
    _pin_sovereign_source_manifest(monkeypatch, sovereign)

    result = acceptance.main(
        [
            "--core-repo",
            str(core),
            "--core-commit",
            core_commit,
            "--sovereign-repo",
            str(sovereign),
            "--sovereign-source-commit",
            failing_source,
        ]
    )
    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert "local synthetic acceptance command failed" in captured.err
    assert "context_synthetic_forbidden" not in captured.err
    assert "token_forbidden" not in captured.err
    assert str(tmp_path) not in captured.err
