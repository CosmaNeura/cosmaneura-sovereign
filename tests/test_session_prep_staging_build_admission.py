"""Fail-closed tests for exact Session Prep staging build admission."""

from __future__ import annotations

import io
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

from tools import admit_session_prep_staging_build as admission


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "infra/scripts/build-bootstrap-image.sh"
FINALIZE_SCRIPT = ROOT / "infra/scripts/finalize-nixpacks-context.py"
ADMISSION_SCRIPT = ROOT / "tools/admit_session_prep_staging_build.py"


def _source_packet() -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "schema_version": admission.source_candidate.SCHEMA,
        "source_pair_reconciled": True,
        "core": {
            "accepted_base_commit": admission.source_candidate.ACCEPTED_CORE_COMMIT,
            "candidate_commit": admission.source_candidate.CANDIDATE_CORE_COMMIT,
            "candidate_tree": admission.source_candidate.CANDIDATE_CORE_TREE,
            "lineage_verified": True,
        },
        "sovereign": {
            "accepted_base_commit": (
                admission.source_candidate.ACCEPTED_SOVEREIGN_COMMIT
            ),
            "candidate_commit": (admission.source_candidate.CANDIDATE_SOVEREIGN_COMMIT),
            "candidate_tree": admission.source_candidate.CANDIDATE_SOVEREIGN_TREE,
            "lineage_verified": True,
        },
        "compatibility": {
            "contracts": {
                "byte_compatible": True,
                "file_count": len(admission.source_candidate.CONTRACT_PATHS),
                "manifest_sha256": "sha256:" + "b" * 64,
            },
            "controls": {
                "byte_compatible": True,
                "core_runtime_default_off": True,
                "file_count": len(admission.source_candidate.CORE_RUNTIME_PATHS)
                + len(admission.source_candidate.SOVEREIGN_RUNTIME_PATHS),
                "manifest_sha256": "sha256:" + "c" * 64,
                "sovereign_runtime_default_off": True,
                "staging_feature_forced_off": True,
            },
            "panel": {
                "byte_compatible": True,
                "sha256": "sha256:" + admission.source_candidate.PANEL_SHA256,
                "sri": admission.source_candidate.PANEL_SRI,
            },
        },
        "ui_operator_attestation": {
            "existing_verifier_validated": True,
            "operator_observation_proven": False,
            "authority": "closed-metadata-and-pinned-automated-support-only",
            "receipt_file_sha256": "sha256:" + "a" * 64,
        },
        "image_built": False,
        "staging_deployed": False,
        "staging_validated": False,
        "auth_iam_ready": False,
        "durable_operational_controls_ready": False,
        "real_provider_phi_allowed": False,
        "production_ready": False,
    }
    return {
        "evidence": evidence,
        "evidence_sha256": admission._sha256(
            admission._canonical_json(evidence).encode("ascii")
        ),
    }


def _admit(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(
        admission.source_candidate,
        "reconcile",
        lambda **_arguments: _source_packet(),
    )
    return admission.admit(
        core_repo=Path("/not-emitted/core"),
        sovereign_repo=Path("/not-emitted/sovereign"),
        ui_receipt=Path("/not-emitted/provider-receipt.json"),
    )


def _make_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _pre_network_environment(
    tmp_path: Path,
    *,
    fail_git_status: bool = False,
    fail_git_filter_check: bool = False,
    local_git_filter: bool = False,
) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "external-calls.log"
    _make_executable(
        bin_dir / "git",
        f"""#!{sys.executable}
import pathlib
import os
import sys

args = sys.argv[1:]
if args[:1] == ["--no-replace-objects"]:
    args = args[1:]
while args[:1] == ["-c"]:
    args = args[2:]
if args[:1] == ["-C"]:
    args = args[2:]
if args == ["config", "--includes", "--get-regexp", "^filter\\."]:
    if {fail_git_filter_check!r}:
        raise SystemExit(90)
    if {local_git_filter!r}:
        print("filter.synthetic.clean /private/not-emitted")
        raise SystemExit(0)
    raise SystemExit(1)
if args == ["status", "--porcelain", "--untracked-files=all"]:
    if {fail_git_status!r}:
        raise SystemExit(90)
    raise SystemExit(0)
pathlib.Path({str(call_log)!r}).open("a").write("git " + " ".join(args) + "\\n")
raise SystemExit(91)
""",
    )
    for command in ("az", "curl", "tar"):
        _make_executable(
            bin_dir / command,
            f"""#!/bin/sh
printf '%s\\n' {command!r} >> {str(call_log)!r}
exit 92
""",
        )
    _make_executable(
        bin_dir / "python3",
        f"""#!{sys.executable}
import os
import pathlib
import sys

raw_args = sys.argv[1:]
if raw_args[:3] != ["-I", "-B", "-S"]:
    pathlib.Path({str(call_log)!r}).open("a").write("python3-unisolated\\n")
    raise SystemExit(94)
args = raw_args[3:]
pathlib.Path({str(call_log)!r}).open("a").write("python3 " + " ".join(args[:1]) + "\\n")
if "FAKE_ADMISSION_OUTPUT" in os.environ:
    sys.stdout.write(os.environ["FAKE_ADMISSION_OUTPUT"])
    raise SystemExit(0)
raise SystemExit(93)
""",
    )
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "DEPLOYMENT_ENVIRONMENT": "staging",
        "REGISTRY": "cosmaneurasovereignstagingregistry.azurecr.io",
        "IMAGE_REPO": "cosmaneura/sovereign",
    }
    return environment, call_log


def test_admission_reuses_exact_reconciler_inputs_and_keeps_all_gates_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def reconcile(**arguments: object) -> dict[str, Any]:
        captured.update(arguments)
        return _source_packet()

    monkeypatch.setattr(admission.source_candidate, "reconcile", reconcile)
    core = Path("/private/core-candidate")
    sovereign = Path("/private/sovereign-controller")
    receipt = Path("/private/fresh-ui-receipt.json")

    packet = admission.admit(
        core_repo=core,
        sovereign_repo=sovereign,
        ui_receipt=receipt,
    )

    assert captured == {
        "core_repo": core,
        "core_commit": admission.source_candidate.CANDIDATE_CORE_COMMIT,
        "sovereign_repo": sovereign,
        "sovereign_commit": admission.source_candidate.CANDIDATE_SOVEREIGN_COMMIT,
        "ui_receipt": receipt,
    }
    evidence = packet["evidence"]
    assert evidence["schema_version"] == admission.SCHEMA
    assert evidence["environment"] == "staging"
    assert evidence["registry"] == admission.STAGING_REGISTRY
    assert evidence["repository"] == admission.IMAGE_REPOSITORY
    assert evidence["build_input_admitted"] is True
    assert evidence["source_pair_reconciled"] is True
    assert evidence["ui_operator_attestation"] == {
        "existing_verifier_validated": True,
        "operator_observation_proven": False,
        "receipt_file_sha256": "sha256:" + "a" * 64,
    }
    for claim in admission.DEFERRED_CLAIMS:
        assert evidence[claim] is False
    assert packet["evidence_sha256"] == admission._sha256(
        admission._canonical_json(evidence).encode("ascii")
    )
    serialized = admission._canonical_json(packet)
    assert len(serialized.encode("ascii")) <= admission.MAX_OUTPUT_BYTES
    assert "/private/" not in serialized
    assert "fresh-ui-receipt" not in serialized


def test_build_fields_are_exact_bounded_and_shell_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _admit(monkeypatch)

    output = admission.build_fields(packet)

    assert output.split("\t") == [
        admission.source_candidate.CANDIDATE_SOVEREIGN_COMMIT,
        admission.source_candidate.CANDIDATE_SOVEREIGN_TREE,
        packet["evidence_sha256"],
    ]
    assert "\n" not in output
    assert len(output.encode("ascii")) <= 256


@pytest.mark.parametrize("claim", admission.DEFERRED_CLAIMS)
def test_source_packet_cannot_promote_any_deferred_claim(
    monkeypatch: pytest.MonkeyPatch,
    claim: str,
) -> None:
    packet = _source_packet()
    packet["evidence"][claim] = True
    packet["evidence_sha256"] = admission._sha256(
        admission._canonical_json(packet["evidence"]).encode("ascii")
    )
    monkeypatch.setattr(
        admission.source_candidate,
        "reconcile",
        lambda **_arguments: packet,
    )

    with pytest.raises(admission.AdmissionError, match="promoted"):
        admission.admit(
            core_repo=Path("core"),
            sovereign_repo=Path("sovereign"),
            ui_receipt=Path("receipt"),
        )


def test_tampered_source_packet_and_feature_controls_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _source_packet()
    packet["evidence_sha256"] = "sha256:" + "0" * 64
    monkeypatch.setattr(
        admission.source_candidate,
        "reconcile",
        lambda **_arguments: packet,
    )
    with pytest.raises(admission.AdmissionError, match="digest"):
        admission.admit(
            core_repo=Path("core"),
            sovereign_repo=Path("sovereign"),
            ui_receipt=Path("receipt"),
        )

    packet = _source_packet()
    packet["evidence"]["compatibility"]["controls"]["staging_feature_forced_off"] = (
        False
    )
    packet["evidence_sha256"] = admission._sha256(
        admission._canonical_json(packet["evidence"]).encode("ascii")
    )
    with pytest.raises(admission.AdmissionError, match="fail-closed"):
        admission._validate_source_packet(packet)


@pytest.mark.parametrize("section", ("contracts", "panel"))
def test_source_packet_requires_all_byte_compatibility_claims(
    section: str,
) -> None:
    packet = _source_packet()
    packet["evidence"]["compatibility"][section]["byte_compatible"] = False
    packet["evidence_sha256"] = admission._sha256(
        admission._canonical_json(packet["evidence"]).encode("ascii")
    )

    with pytest.raises(admission.AdmissionError, match="fail-closed"):
        admission._validate_source_packet(packet)


def test_python_entrypoints_are_isolated_and_cli_failures_are_sanitized(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = "/private/provider_identifier_receipt.json"

    result = admission.main(["--unknown", secret])

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == admission.FAIL_SUMMARY + "\n"
    assert secret not in captured.err
    assert "provider_identifier" not in captured.err

    poison = tmp_path / "poison" / "tools"
    poison.mkdir(parents=True)
    (poison / "__init__.py").write_text(
        "raise SystemExit('loaded inherited PYTHONPATH')\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(poison.parent),
        "PYTHONWARNINGS": "error",
    }
    isolated = subprocess.run(
        [sys.executable, "-I", "-B", "-S", ADMISSION_SCRIPT, "--unknown", secret],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert isolated.returncode == 1
    assert isolated.stdout == ""
    assert isolated.stderr == admission.FAIL_SUMMARY + "\n"
    assert secret not in isolated.stderr

    observed: dict[str, object] = {}

    def accept_verifier(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess:
        observed["command"] = command
        observed["environment"] = kwargs["env"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=admission.source_candidate.UI_VERIFIER_PASS_OUTPUT,
            stderr=b"",
        )

    monkeypatch.setenv("PYTHONINSPECT", "1")
    monkeypatch.setattr(admission.source_candidate.subprocess, "run", accept_verifier)
    admission.source_candidate._run_existing_ui_verifier(
        tmp_path,
        b"pass\n",
        b"{}",
    )
    command = observed["command"]
    verifier_environment = observed["environment"]
    assert isinstance(command, list)
    assert isinstance(verifier_environment, dict)
    assert command[1:4] == ["-I", "-B", "-S"]
    assert verifier_environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert verifier_environment["PYTHONPATH"] == ""
    assert "PYTHONINSPECT" not in verifier_environment
    assert "PYTHONWARNINGS" not in verifier_environment


def test_staging_builder_requires_admission_before_any_network_or_build_action(
    tmp_path: Path,
) -> None:
    environment, call_log = _pre_network_environment(tmp_path)

    result = subprocess.run(
        [BUILD_SCRIPT],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "SESSION_PREP_CORE_REPO is required" in result.stderr
    assert not call_log.exists()


def test_staging_builder_rejects_unverifiable_or_filtered_repository_before_network(
    tmp_path: Path,
) -> None:
    cases = (
        ("unverifiable", {"fail_git_status": True}, "unable to verify"),
        (
            "filtered",
            {"local_git_filter": True},
            "repository-local Git content filters",
        ),
        (
            "filter-check-failed",
            {"fail_git_filter_check": True},
            "unable to verify repository-local Git filter configuration",
        ),
    )
    for name, options, expected_error in cases:
        case_dir = tmp_path / name
        case_dir.mkdir()
        environment, call_log = _pre_network_environment(case_dir, **options)

        result = subprocess.run(
            [BUILD_SCRIPT],
            env=environment,
            text=True,
            capture_output=True,
        )

        assert result.returncode != 0
        assert expected_error in result.stderr
        assert not call_log.exists()


def test_staging_builder_stops_on_rejected_admission_before_network(
    tmp_path: Path,
) -> None:
    environment, call_log = _pre_network_environment(tmp_path)
    environment["SESSION_PREP_CORE_REPO"] = str(tmp_path / "core")
    environment["SESSION_PREP_UI_RECEIPT"] = str(tmp_path / "receipt.json")

    result = subprocess.run(
        [BUILD_SCRIPT],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert call_log.read_text().splitlines() == [
        f"python3 {ROOT / 'tools/admit_session_prep_staging_build.py'}"
    ]
    assert "curl" not in call_log.read_text()
    assert "az" not in call_log.read_text()
    assert "tar" not in call_log.read_text()


@pytest.mark.parametrize(
    "output",
    (
        "",
        "a" * 40 + "\t\tsha256:" + "b" * 64 + "\n",
        "a" * 40 + "\t" + "b" * 40 + "\tsha256:" + "c" * 64 + "\n\n",
        "a" * 40 + "\t" + "b" * 40 + "\tsha256:" + "c" * 64 + "\t\n",
        "x" * 1024 + "\n",
    ),
)
def test_staging_builder_rejects_noncanonical_admission_fields_before_network(
    tmp_path: Path,
    output: str,
) -> None:
    environment, call_log = _pre_network_environment(tmp_path)
    environment.update(
        {
            "SESSION_PREP_CORE_REPO": str(tmp_path / "core"),
            "SESSION_PREP_UI_RECEIPT": str(tmp_path / "receipt.json"),
            "FAKE_ADMISSION_OUTPUT": output,
        }
    )

    result = subprocess.run(
        [BUILD_SCRIPT],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "invalid result" in result.stderr
    calls = call_log.read_text(encoding="utf-8")
    assert calls.splitlines() == [
        f"python3 {ROOT / 'tools/admit_session_prep_staging_build.py'}"
    ]
    assert "curl" not in calls
    assert "az" not in calls
    assert "tar" not in calls


def test_staging_builder_preserves_exact_archive_and_admission_labels(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    for relative in (
        "infra/scripts/build-bootstrap-image.sh",
        "infra/scripts/deployment-context.sh",
        "infra/scripts/finalize-nixpacks-context.py",
    ):
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    (repository / "requirements.lock").write_text(
        "synthetic locked build input\n",
        encoding="utf-8",
    )
    git_environment = {
        **os.environ,
        "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
        "GIT_AUTHOR_NAME": "Synthetic Test",
        "GIT_COMMITTER_EMAIL": "synthetic@example.invalid",
        "GIT_COMMITTER_NAME": "Synthetic Test",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    subprocess.run(
        ["git", "init", "--quiet", repository],
        env=git_environment,
        check=True,
    )
    subprocess.run(
        ["git", "-C", repository, "add", "."],
        env=git_environment,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            repository,
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "synthetic staging source",
        ],
        env=git_environment,
        check=True,
    )
    revision = subprocess.run(
        ["git", "-C", repository, "rev-parse", "HEAD"],
        env=git_environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "-C", repository, "rev-parse", "HEAD^{tree}"],
        env=git_environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    admission_digest = "sha256:" + "c" * 64
    image_digest = "sha256:" + "d" * 64

    # Repository-local operator metadata can affect an ordinary git archive,
    # but it is not part of the admitted tree and must not affect the builder.
    info_attributes = repository / ".git" / "info" / "attributes"
    info_attributes.write_text("requirements.lock export-ignore\n", encoding="utf-8")
    xdg_config_home = tmp_path / "xdg"
    xdg_attributes = xdg_config_home / "git" / "attributes"
    xdg_attributes.parent.mkdir(parents=True)
    xdg_attributes.write_text(
        "requirements.lock export-ignore\n",
        encoding="utf-8",
    )
    git_environment["XDG_CONFIG_HOME"] = str(xdg_config_home)
    fsmonitor_marker = tmp_path / "fsmonitor-ran"
    fsmonitor_hook = tmp_path / "fsmonitor-hook"
    _make_executable(
        fsmonitor_hook,
        f"#!/bin/sh\ntouch {shlex.quote(str(fsmonitor_marker))}\nexit 1\n",
    )
    subprocess.run(
        ["git", "-C", repository, "config", "core.fsmonitor", str(fsmonitor_hook)],
        env=git_environment,
        check=True,
    )
    ordinary_archive = subprocess.run(
        ["git", "-C", repository, "archive", "--format=tar", revision],
        env=git_environment,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(ordinary_archive), mode="r:") as archive:
        assert "requirements.lock" not in archive.getnames()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    system_tar = shutil.which("tar")
    assert system_tar is not None
    nixpacks_payload = tmp_path / "nixpacks"
    nixpacks_log = tmp_path / "nixpacks.log"
    acr_log = tmp_path / "acr.log"
    call_log = tmp_path / "call-order.log"
    _make_executable(
        bin_dir / "tar",
        f"#!{sys.executable}\n"
        + f"""import os
import pathlib
import sys

if any(name in os.environ for name in ("TAR_OPTIONS", "TAPE", "FAKE_TAR_CONFIG")):
    raise SystemExit("tar inherited operator configuration")
args = sys.argv[1:]
if args[:1] == ["-xzf"]:
    marker = "tar-release"
elif args[:2] == ["-xf", "-"]:
    marker = "tar-source"
else:
    raise SystemExit("unexpected tar invocation")
pathlib.Path({str(call_log)!r}).open("a").write(marker + "\\n")
os.execv({system_tar!r}, [{system_tar!r}, *args])
""",
    )
    _make_executable(
        nixpacks_payload,
        f"#!{sys.executable}\n"
        + """import os
import pathlib
import shlex
import sys

def log(value):
    pathlib.Path(os.environ["CALL_LOG"]).open("a").write(value + "\\n")

inherited = sorted(name for name in os.environ if name.startswith("NIXPACKS_"))
if inherited:
    raise SystemExit("inherited Nixpacks configuration: " + ",".join(inherited))
args = sys.argv[1:]
if args == ["--version"]:
    log("nixpacks-version")
    print("nixpacks 1.41.0")
    raise SystemExit(0)
if len(args) < 2 or args[0] != "build":
    raise SystemExit(2)
log("nixpacks-build")
source = pathlib.Path(args[1])
if not (source / "requirements.lock").is_file():
    raise SystemExit("isolated archive omitted the committed lock")
image = args[args.index("--name") + 1]
revision = args[args.index("--cache-key") + 1]
labels = [args[index + 1] for index, value in enumerate(args) if value == "--label"]
expected_labels = [
    f"org.opencontainers.image.revision={revision}",
    "org.cosmaneura.session-prep.build-admission=" + os.environ["ADMISSION_DIGEST"],
]
if labels != expected_labels:
    raise SystemExit("staging provenance labels drifted")
generated = source / ".nixpacks"
generated.mkdir()
dockerfile = generated / "Dockerfile"
dockerfile.write_text(
    "FROM ghcr.io/railwayapp/nixpacks:ubuntu-1745885067\\n"
    f"RUN --mount=type=cache,id={revision}-/root/cache/pip,"
    "target=/root/.cache/pip "
    "python -m venv --copies /opt/venv && . /opt/venv/bin/activate && "
    "python -m pip install --require-hashes --only-binary=:all: "
    "-r requirements.lock\\n"
)
build_args = [
    "NIXPACKS_METADATA=python",
    "NIXPACKS_PYTHON_VERSION=3.13",
    "PIP_DEFAULT_TIMEOUT=100",
    "PIP_DISABLE_PIP_VERSION_CHECK=1",
    "PIP_NO_CACHE_DIR=1",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONFAULTHANDLER=1",
    "PYTHONHASHSEED=random",
    "PYTHONUNBUFFERED=1",
]
command = ["docker", "build", str(source), "-f", str(dockerfile), "-t", image]
for argument in build_args:
    command.extend(("--build-arg", argument))
for label in labels:
    command.extend(("--label", label))
command.extend(("--platform", "linux/amd64"))
(generated / "build.sh").write_text(shlex.join(command))
pathlib.Path(os.environ["FAKE_NIXPACKS_LOG"]).write_text("archive-and-labels-accepted\\n")
""",
    )
    _make_executable(
        bin_dir / "curl",
        f"#!{sys.executable}\n"
        + """import os
import pathlib
import sys
import tarfile

pathlib.Path(os.environ["CALL_LOG"]).open("a").write("curl\\n")
args = sys.argv[1:]
output = pathlib.Path(args[args.index("--output") + 1])
with tarfile.open(output, "w:gz") as archive:
    archive.add(os.environ["FAKE_NIXPACKS"], arcname="nixpacks")
""",
    )
    _make_executable(
        bin_dir / "python3",
        f"#!{sys.executable}\n"
        + f"""import os
import pathlib
import sys

raw_args = sys.argv[1:]
if raw_args[:3] != ["-I", "-B", "-S"]:
    raise SystemExit("python was not isolated")
args = raw_args[3:]
if args[:1] == ["-"]:
    pathlib.Path(os.environ["CALL_LOG"]).open("a").write("checksum\\n")
    raise SystemExit(0)
if pathlib.Path(args[0]).name == "admit_session_prep_staging_build.py":
    pathlib.Path(os.environ["CALL_LOG"]).open("a").write("admission\\n")
    print(os.environ["ADMISSION_FIELDS"])
    raise SystemExit(0)
if pathlib.Path(args[0]).name == "finalize-nixpacks-context.py":
    pathlib.Path(os.environ["CALL_LOG"]).open("a").write("finalizer\\n")
os.execv({sys.executable!r}, [{sys.executable!r}, *args])
""",
    )
    _make_executable(
        bin_dir / "az",
        f"#!{sys.executable}\n"
        + """import json
import os
import pathlib
import sys

args = sys.argv[1:]
if args[:2] == ["acr", "build"]:
    pathlib.Path(os.environ["CALL_LOG"]).open("a").write("acr-build\\n")
    dockerfile = pathlib.Path(args[args.index("--file") + 1])
    content = dockerfile.read_text()
    revision_label = (
        'LABEL org.opencontainers.image.revision="'
        + os.environ["SOURCE_COMMIT"]
        + '"'
    )
    admission_label = (
        'LABEL org.cosmaneura.session-prep.build-admission="'
        + os.environ["ADMISSION_DIGEST"]
        + '"'
    )
    if content.splitlines().count(revision_label) != 1:
        raise SystemExit("normalized revision label missing")
    if content.splitlines().count(admission_label) != 1:
        raise SystemExit("normalized admission label missing")
    pathlib.Path(os.environ["ACR_LOG"]).write_text(
        json.dumps({"revision": revision_label, "admission": admission_label})
    )
elif args[:3] == ["acr", "manifest", "show-metadata"]:
    pathlib.Path(os.environ["CALL_LOG"]).open("a").write("acr-manifest\\n")
    print(os.environ["IMAGE_DIGEST"])
else:
    raise SystemExit(4)
""",
    )
    core_repo = tmp_path / "core"
    core_repo.mkdir()
    receipt = tmp_path / "fresh-synthetic-ui-receipt.json"
    receipt.write_text("{}\n", encoding="utf-8")
    environment = {
        **git_environment,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "DEPLOYMENT_ENVIRONMENT": "staging",
        "REGISTRY": admission.STAGING_REGISTRY,
        "IMAGE_REPO": admission.IMAGE_REPOSITORY,
        "SESSION_PREP_CORE_REPO": str(core_repo),
        "SESSION_PREP_UI_RECEIPT": str(receipt),
        "ADMISSION_FIELDS": "\t".join((revision, tree, admission_digest)),
        "ADMISSION_DIGEST": admission_digest,
        "SOURCE_COMMIT": revision,
        "FAKE_NIXPACKS": str(nixpacks_payload),
        "FAKE_NIXPACKS_LOG": str(nixpacks_log),
        "NIXPACKS_CONFIG_FILE": "/private/not-emitted/nixpacks.toml",
        "NIXPACKS_LOG": "/private/not-emitted/nixpacks.log",
        "TAR_OPTIONS": "--help",
        "TAPE": "/private/not-emitted/tape",
        "FAKE_TAR_CONFIG": "must-not-reach-tar",
        "ACR_LOG": str(acr_log),
        "IMAGE_DIGEST": image_digest,
        "XDG_CONFIG_HOME": str(xdg_config_home),
        "CALL_LOG": str(call_log),
    }

    result = subprocess.run(
        [repository / "infra/scripts/build-bootstrap-image.sh"],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        f"{admission.STAGING_REGISTRY}/{admission.IMAGE_REPOSITORY}@{image_digest}"
    )
    assert nixpacks_log.read_text(encoding="utf-8") == "archive-and-labels-accepted\n"
    assert call_log.read_text(encoding="utf-8").splitlines() == [
        "admission",
        "curl",
        "checksum",
        "tar-release",
        "nixpacks-version",
        "tar-source",
        "nixpacks-build",
        "finalizer",
        "acr-build",
        "acr-manifest",
    ]
    assert not fsmonitor_marker.exists()
    logged_labels = acr_log.read_text(encoding="utf-8")
    assert revision in logged_labels
    assert admission_digest in logged_labels


def test_builder_archives_and_labels_the_admitted_commit_not_head() -> None:
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    admission_call = source.index("tools/admit_session_prep_staging_build.py")
    first_network = source.index("curl --fail")
    first_build = source.index("run_closed_nixpacks build")
    cleanliness_check = source.index("status --porcelain --untracked-files=all")
    deployment_context = source.index('source "$HERE/deployment-context.sh"')

    assert admission_call < first_network < first_build
    assert cleanliness_check < deployment_context < admission_call
    assert 'archive --format=tar "$sha"' in source
    assert "archive --format=tar HEAD" not in source
    assert '--label "org.opencontainers.image.revision=$sha"' in source
    assert (
        '--label "org.cosmaneura.session-prep.build-admission=$admission_digest"'
        in source
    )
    assert '"$sha" "$commit_epoch" "$admission_digest"' in source
    assert 'show -s --format=%ct "$sha"' in source
    assert "run_closed_git()" in source
    assert "GIT_NO_LAZY_FETCH=1" in source
    assert 'git --no-replace-objects "$@"' in source
    assert "-c core.fsmonitor=false" in source
    assert "-c core.attributesFile=/dev/null" in source
    assert '[[ ! "$commit_epoch" =~ ^[0-9]{1,12}$ ]]' in source
    assert 'if [[ "$DEPLOYMENT_ENVIRONMENT" == "staging" ]]' in source
    assert '"$IMAGE_REPO" != "cosmaneura/sovereign"' in source
    assert "--output build-fields" in source


def _write_nixpacks_context(
    source: Path,
    *,
    revision: str,
    image: str,
    admission_digest: str | None,
) -> Path:
    generated = source / ".nixpacks"
    generated.mkdir(parents=True)
    dockerfile = generated / "Dockerfile"
    dockerfile.write_text(
        "FROM ghcr.io/railwayapp/nixpacks:ubuntu-1745885067\n"
        f"RUN --mount=type=cache,id={revision}-/root/cache/pip,"
        "target=/root/.cache/pip "
        "python -m venv --copies /opt/venv && . /opt/venv/bin/activate && "
        "python -m pip install --require-hashes --only-binary=:all: "
        "-r requirements.lock\n",
        encoding="utf-8",
    )
    command = [
        "docker",
        "build",
        str(source),
        "-f",
        str(dockerfile),
        "-t",
        image,
    ]
    for argument in (
        "NIXPACKS_METADATA=python",
        "NIXPACKS_PYTHON_VERSION=3.13",
        "PIP_DEFAULT_TIMEOUT=100",
        "PIP_DISABLE_PIP_VERSION_CHECK=1",
        "PIP_NO_CACHE_DIR=1",
        "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONFAULTHANDLER=1",
        "PYTHONHASHSEED=random",
        "PYTHONUNBUFFERED=1",
    ):
        command.extend(("--build-arg", argument))
    command.extend(("--label", f"org.opencontainers.image.revision={revision}"))
    if admission_digest is not None:
        command.extend(
            (
                "--label",
                "org.cosmaneura.session-prep.build-admission=" + admission_digest,
            )
        )
    command.extend(("--platform", "linux/amd64"))
    (generated / "build.sh").write_text(
        shlex.join(command),
        encoding="utf-8",
    )
    return dockerfile


def test_finalizer_binds_only_the_exact_admission_digest_to_staging_context(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    revision = admission.source_candidate.CANDIDATE_SOVEREIGN_COMMIT
    image = f"{admission.STAGING_REGISTRY}/{admission.IMAGE_REPOSITORY}:{revision}"
    digest = "sha256:" + "b" * 64
    dockerfile = _write_nixpacks_context(
        source,
        revision=revision,
        image=image,
        admission_digest=digest,
    )

    result = subprocess.run(
        [
            sys.executable,
            FINALIZE_SCRIPT,
            source,
            image,
            revision,
            "1700000000",
            digest,
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    normalized = dockerfile.read_text(encoding="utf-8")
    assert normalized.count("org.opencontainers.image.revision=") == 1
    assert normalized.count("org.cosmaneura.session-prep.build-admission=") == 1
    assert f'LABEL org.cosmaneura.session-prep.build-admission="{digest}"' in normalized
    assert str(source) not in normalized


@pytest.mark.parametrize(
    "digest",
    (
        "b" * 64,
        "sha256:" + "B" * 64,
        "sha256:" + "b" * 63,
        "sha256:" + "b" * 64 + "extra",
    ),
)
def test_finalizer_rejects_non_exact_admission_digest(
    tmp_path: Path,
    digest: str,
) -> None:
    source = tmp_path / "source"
    revision = admission.source_candidate.CANDIDATE_SOVEREIGN_COMMIT
    image = f"{admission.STAGING_REGISTRY}/{admission.IMAGE_REPOSITORY}:{revision}"
    _write_nixpacks_context(
        source,
        revision=revision,
        image=image,
        admission_digest=digest,
    )

    result = subprocess.run(
        [
            sys.executable,
            FINALIZE_SCRIPT,
            source,
            image,
            revision,
            "1700000000",
            digest,
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "admission digest must be an exact lowercase SHA-256 digest" in result.stderr
