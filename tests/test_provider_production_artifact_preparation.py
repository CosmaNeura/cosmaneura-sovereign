"""Offline tests for the provider-only production artifact preparation."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tools import prepare_provider_production_artifact as preparation


CORE_COMMIT = "c" * 40
OBSERVATION_KEYS = {
    "core_image_digest",
    "core_oci_layout",
    "core_source_commit",
    "sovereign_image_digest",
    "sovereign_oci_layout",
    "sovereign_repo",
    "sovereign_source_commit",
}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *arguments], text=True
    ).strip()


def _arguments(inputs: dict[str, Any]) -> dict[str, Any]:
    return {key: inputs[key] for key in OBSERVATION_KEYS}


def _source_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "sovereign-source"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Synthetic Test"], check=True
    )
    (repo / "app.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "app.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--quiet", "-m", "base"], check=True
    )
    main_branch = _git(repo, "branch", "--show-current")
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "--quiet", "-b", "reviewed"], check=True
    )
    (repo / "provider.txt").write_text("provider\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "provider.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--quiet", "-m", "provider"], check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "--quiet", main_branch], check=True
    )
    (repo / "control.txt").write_text("control\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "control.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--quiet", "-m", "control"], check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "merge",
            "--quiet",
            "--no-ff",
            "reviewed",
            "-m",
            "reviewed merge",
        ],
        check=True,
    )
    return repo, _git(repo, "rev-parse", "HEAD")


def _write_blob(layout: Path, data: bytes) -> dict[str, Any]:
    digest = _digest(data)
    blob = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(data)
    return {"digest": digest, "size": len(data)}


def _oci_layout(
    root: Path,
    *,
    revision: str,
    architecture: str = "amd64",
    mount_profile: str | None = None,
) -> tuple[Path, str, dict[str, Path]]:
    root.mkdir()
    (root / "oci-layout").write_bytes(_canonical({"imageLayoutVersion": "1.0.0"}))
    layer = b"synthetic application layer"
    layer_descriptor = _write_blob(root, layer)
    layer_descriptor["mediaType"] = "application/vnd.oci.image.layer.v1.tar"
    labels = {"org.opencontainers.image.revision": revision}
    environment: list[str] = []
    if mount_profile is not None:
        environment.append("MOUNT_PROFILE=" + mount_profile)
    config = {
        "architecture": architecture,
        "config": {"Env": environment, "Labels": labels},
        "os": "linux",
        "rootfs": {"diff_ids": [_digest(layer)], "type": "layers"},
    }
    config_descriptor = _write_blob(root, _canonical(config))
    config_descriptor["mediaType"] = "application/vnd.oci.image.config.v1+json"
    manifest = {
        "config": config_descriptor,
        "layers": [layer_descriptor],
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "schemaVersion": 2,
    }
    manifest_descriptor = _write_blob(root, _canonical(manifest))
    manifest_descriptor.update(
        {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "platform": {"architecture": architecture, "os": "linux"},
        }
    )
    index = {
        "manifests": [manifest_descriptor],
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "schemaVersion": 2,
    }
    (root / "index.json").write_bytes(_canonical(index))
    return (
        root,
        manifest_descriptor["digest"],
        {
            "config": root
            / "blobs"
            / "sha256"
            / config_descriptor["digest"].split(":", 1)[1],
            "index": root / "index.json",
            "layer": root
            / "blobs"
            / "sha256"
            / layer_descriptor["digest"].split(":", 1)[1],
            "manifest": root
            / "blobs"
            / "sha256"
            / manifest_descriptor["digest"].split(":", 1)[1],
        },
    )


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Any]:
    repo, source_commit = _source_repo(tmp_path)
    sovereign_layout, sovereign_digest, sovereign_paths = _oci_layout(
        tmp_path / "sovereign-oci",
        revision=source_commit,
    )
    core_layout, core_digest, core_paths = _oci_layout(
        tmp_path / "core-oci",
        revision=CORE_COMMIT,
    )
    return {
        "core_image_digest": core_digest,
        "core_oci_layout": core_layout,
        "core_paths": core_paths,
        "core_source_commit": CORE_COMMIT,
        "sovereign_image_digest": sovereign_digest,
        "sovereign_oci_layout": sovereign_layout,
        "sovereign_paths": sovereign_paths,
        "sovereign_repo": repo,
        "sovereign_source_commit": source_commit,
    }


def test_prepare_and_reverify_exact_pair_offline(
    inputs: dict[str, Any], tmp_path: Path
) -> None:
    envelope = preparation.prepare(**_arguments(inputs))
    payload = envelope["payload"]
    assert payload["scope"] == {
        "mount_profile": "provider_assistant",
        "provider_only": True,
    }
    assert payload["public_release"] == {
        "image_included": False,
        "source_included": False,
    }
    assert set(payload["authorization"].values()) == {False}
    assert payload["source"]["commit"] == inputs["sovereign_source_commit"]
    assert (
        payload["provider_artifact"]["image_digest"] == inputs["sovereign_image_digest"]
    )
    assert payload["core_candidate"]["image_digest"] == inputs["core_image_digest"]
    manifest = tmp_path / "provider-preparation.json"
    preparation._write_new(manifest, envelope)
    assert preparation.verify(manifest, **_arguments(inputs)) == envelope
    assert manifest.stat().st_mode & 0o777 == 0o400


def test_source_identity_rejects_wrong_head_and_dirty_tree(
    inputs: dict[str, Any],
) -> None:
    repo = inputs["sovereign_repo"]
    expected = inputs["sovereign_source_commit"]
    (repo / "untracked.txt").write_text("not reviewed\n", encoding="utf-8")
    with pytest.raises(preparation.PreparationError, match="not clean"):
        preparation._source_identity(repo, expected)
    (repo / "untracked.txt").unlink()
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "--quiet", "HEAD^1"], check=True
    )
    with pytest.raises(preparation.PreparationError, match="not the exact reviewed"):
        preparation._source_identity(repo, expected)


@pytest.mark.parametrize("blob_name", ["manifest", "config", "layer"])
def test_raw_oci_blob_tamper_is_rejected(
    inputs: dict[str, Any], blob_name: str
) -> None:
    path = inputs["sovereign_paths"][blob_name]
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(preparation.PreparationError, match="does not match"):
        preparation.prepare(**_arguments(inputs))


def test_platform_drift_is_rejected(tmp_path: Path) -> None:
    layout, digest, _paths = _oci_layout(
        tmp_path / "arm-image",
        revision=CORE_COMMIT,
        architecture="arm64",
    )
    with pytest.raises(preparation.PreparationError, match="linux/amd64"):
        preparation._oci_identity(
            layout,
            expected_digest=digest,
            expected_revision=CORE_COMMIT,
            exclude_public=False,
        )


def test_core_source_image_pair_cannot_be_spliced(inputs: dict[str, Any]) -> None:
    inputs["core_source_commit"] = "d" * 40
    with pytest.raises(preparation.PreparationError, match="revision label"):
        preparation.prepare(**_arguments(inputs))
    inputs["core_source_commit"] = CORE_COMMIT
    inputs["core_image_digest"] = "sha256:" + "0" * 64
    with pytest.raises(preparation.PreparationError, match="expected immutable"):
        preparation.prepare(**_arguments(inputs))


def test_public_mount_configuration_is_rejected(
    inputs: dict[str, Any], tmp_path: Path
) -> None:
    layout, digest, _paths = _oci_layout(
        tmp_path / "public-sovereign",
        revision=inputs["sovereign_source_commit"],
        mount_profile="public",
    )
    inputs["sovereign_oci_layout"] = layout
    inputs["sovereign_image_digest"] = digest
    with pytest.raises(preparation.PreparationError, match="public mount"):
        preparation.prepare(**_arguments(inputs))


def test_manifest_tamper_and_authorization_flip_are_rejected(
    inputs: dict[str, Any], tmp_path: Path
) -> None:
    envelope = preparation.prepare(**_arguments(inputs))
    manifest = tmp_path / "provider-preparation.json"
    preparation._write_new(manifest, envelope)
    manifest.chmod(0o600)
    changed = json.loads(manifest.read_text())
    changed["payload"]["authorization"]["activation_authorized"] = True
    changed["payload_sha256"] = preparation._digest(
        preparation._canonical(changed["payload"])
    )
    manifest.write_bytes(preparation._canonical(changed) + b"\n")
    with pytest.raises(preparation.PreparationError, match="fully disabled"):
        preparation.verify(manifest, **_arguments(inputs))


def test_public_release_inclusion_is_rejected_even_with_rehashed_payload(
    inputs: dict[str, Any], tmp_path: Path
) -> None:
    envelope = preparation.prepare(**_arguments(inputs))
    envelope["payload"]["public_release"]["image_included"] = True
    envelope["payload_sha256"] = preparation._digest(
        preparation._canonical(envelope["payload"])
    )
    manifest = tmp_path / "provider-preparation.json"
    preparation._write_new(manifest, envelope)
    with pytest.raises(preparation.PreparationError, match="not excluded"):
        preparation.verify(manifest, **_arguments(inputs))


def test_exclusive_output_refuses_overwrite(
    inputs: dict[str, Any], tmp_path: Path
) -> None:
    manifest = tmp_path / "provider-preparation.json"
    manifest.write_text("preserve me", encoding="utf-8")
    with pytest.raises(preparation.PreparationError, match="created exclusively"):
        preparation._write_new(manifest, preparation.prepare(**_arguments(inputs)))
    assert manifest.read_text() == "preserve me"


@pytest.mark.parametrize("commit", ["main", "a" * 39, "a" * 41, "a" * 63, "A" * 40])
def test_source_commit_requires_full_immutable_object_id(commit: str) -> None:
    with pytest.raises(preparation.PreparationError, match="full lowercase Git"):
        preparation._require_commit(commit, "Sovereign source commit")


@pytest.mark.parametrize("command", ["prepare", "verify"])
def test_cli_requires_explicit_sovereign_source_commit(
    inputs: dict[str, Any], tmp_path: Path, command: str
) -> None:
    arguments = [command]
    for name in sorted(OBSERVATION_KEYS - {"sovereign_source_commit"}):
        arguments.extend(["--" + name.replace("_", "-"), str(inputs[name])])
    arguments.extend(
        [
            "--output" if command == "prepare" else "--manifest",
            str(tmp_path / "pair.json"),
        ]
    )
    with pytest.raises(SystemExit) as refusal:
        preparation.main(arguments)
    assert refusal.value.code == 2
    assert not (tmp_path / "pair.json").exists()


def test_cli_prepares_and_reverifies_explicit_source_pair(
    inputs: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments: list[str] = []
    for name in sorted(OBSERVATION_KEYS):
        arguments.extend(["--" + name.replace("_", "-"), str(inputs[name])])
    manifest = tmp_path / "cli-pair.json"
    assert preparation.main(["prepare", *arguments, "--output", str(manifest)]) == 0
    expected = preparation.prepare(**_arguments(inputs))
    assert capsys.readouterr().out.strip() == expected["payload_sha256"]
    assert preparation.main(["verify", *arguments, "--manifest", str(manifest)]) == 0
    assert capsys.readouterr().out.strip() == expected["payload_sha256"]


def test_corrected_source_requires_new_image_and_preparation(
    inputs: dict[str, Any], tmp_path: Path
) -> None:
    previous = preparation.prepare(**_arguments(inputs))
    manifest = tmp_path / "previous-preparation.json"
    preparation._write_new(manifest, previous)
    repo = inputs["sovereign_repo"]
    main_branch = _git(repo, "branch", "--show-current")
    _git(repo, "checkout", "--quiet", "-b", "dependency-correction")
    (repo / "provider.txt").write_text("corrected dependencies\n", encoding="utf-8")
    _git(repo, "add", "provider.txt")
    _git(repo, "commit", "--quiet", "-m", "dependency correction")
    _git(repo, "checkout", "--quiet", main_branch)
    _git(
        repo,
        "merge",
        "--quiet",
        "--no-ff",
        "dependency-correction",
        "-m",
        "corrected merge",
    )
    inputs["sovereign_source_commit"] = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(preparation.PreparationError, match="revision label"):
        preparation.prepare(**_arguments(inputs))

    layout, digest, _paths = _oci_layout(
        tmp_path / "corrected-sovereign-oci",
        revision=inputs["sovereign_source_commit"],
    )
    inputs.update(sovereign_oci_layout=layout, sovereign_image_digest=digest)
    with pytest.raises(preparation.PreparationError, match="exact current inputs"):
        preparation.verify(manifest, **_arguments(inputs))
    corrected = preparation.prepare(**_arguments(inputs))
    assert (
        corrected["payload"]["candidate_pair_sha256"]
        != previous["payload"]["candidate_pair_sha256"]
    )
    corrected_manifest = tmp_path / "corrected-preparation.json"
    preparation._write_new(corrected_manifest, corrected)
    assert preparation.verify(corrected_manifest, **_arguments(inputs)) == corrected
    assert set(corrected["payload"]["authorization"].values()) == {False}
