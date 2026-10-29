#!/usr/bin/env python3
"""Prepare and reverify one offline, provider-only production artifact pair.

This tool deliberately has no registry, Azure, build, or deployment client.  It
accepts two existing OCI image layouts, proves their raw content-address chain,
and binds the provider Sovereign image to the exact reviewed source archive and
one exact Core source/image candidate.  The resulting document authorizes no
activation or deployment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SOVEREIGN_SOURCE_COMMIT = "66bba1e6814203a2268bf2104e33b390a7c1db55"
CONTRACT = "cosmaneura.session-prep.provider-production-preparation"
SCHEMA_VERSION = 1
PROVIDER_MOUNT = "provider_assistant"
PLATFORM = {"architecture": "amd64", "os": "linux"}
MANIFEST_MEDIA_TYPES = {
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
}
CONFIG_MEDIA_TYPES = {
    "application/vnd.docker.container.image.v1+json",
    "application/vnd.oci.image.config.v1+json",
}
INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_BLOB_BYTES = 1024 * 1024 * 1024


class PreparationError(RuntimeError):
    """The local inputs do not establish the bounded release preparation."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _require_digest(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
    ):
        raise PreparationError(f"{label} must be one full lowercase SHA-256 digest")
    return value


def _require_commit(value: object, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40,64}", value) is None:
        raise PreparationError(f"{label} must be one full lowercase Git object ID")
    return value


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PreparationError(f"{label} has a duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(data, object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationError(f"{label} must be valid JSON") from exc
    if not isinstance(value, dict):
        raise PreparationError(f"{label} must be a JSON object")
    return value


def _read_bounded(path: Path, limit: int, label: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise PreparationError(f"{label} must be one regular file")
        size = path.stat().st_size
        if size > limit:
            raise PreparationError(f"{label} exceeds its size limit")
        data = path.read_bytes()
    except OSError as exc:
        raise PreparationError(f"{label} could not be read") from exc
    if len(data) != size:
        raise PreparationError(f"{label} changed while it was read")
    return data


def _closed_git_environment(**extra: str) -> dict[str, str]:
    path = os.environ.get("PATH")
    if not path:
        raise PreparationError("the executable search path is unavailable")
    environment = {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": path,
    }
    environment.update(extra)
    return environment


def _git(
    repo: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(repo),
                *arguments,
            ],
            check=check,
            capture_output=True,
            env=environment or _closed_git_environment(),
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreparationError(
            "the exact Sovereign Git source could not be verified"
        ) from exc


def _source_identity(repo: Path, expected_commit: str) -> dict[str, str]:
    expected_commit = _require_commit(expected_commit, "Sovereign source commit")
    filter_check = _git(
        repo,
        "config",
        "--includes",
        "--get-regexp",
        r"^filter\.",
        check=False,
    )
    if filter_check.returncode == 0:
        raise PreparationError("the Sovereign source repository has content filters")
    if filter_check.returncode != 1:
        raise PreparationError("Sovereign source filters could not be verified")

    status = _git(repo, "status", "--porcelain", "--untracked-files=all").stdout
    if status:
        raise PreparationError("the Sovereign source worktree is not clean")
    head = _git(repo, "rev-parse", "--verify", "HEAD^{commit}").stdout.decode().strip()
    if head != expected_commit:
        raise PreparationError("Sovereign HEAD is not the exact reviewed merge commit")
    parents = (
        _git(repo, "show", "-s", "--format=%P", expected_commit).stdout.decode().split()
    )
    if len(parents) != 2:
        raise PreparationError("the exact Sovereign source is not a two-parent merge")
    tree = (
        _git(repo, "rev-parse", "--verify", f"{expected_commit}^{{tree}}")
        .stdout.decode()
        .strip()
    )
    _require_commit(tree, "Sovereign source tree")
    object_directory = (
        _git(
            repo,
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "objects",
        )
        .stdout.decode()
        .strip()
    )
    if not object_directory or not Path(object_directory).is_dir():
        raise PreparationError("Sovereign Git object storage is unavailable")

    with tempfile.TemporaryDirectory(
        prefix="sovereign-production-source-"
    ) as temporary:
        temporary_path = Path(temporary)
        bare = temporary_path / "archive.git"
        template = temporary_path / "template"
        template.mkdir()
        try:
            subprocess.run(
                [
                    "git",
                    "init",
                    "--bare",
                    "--quiet",
                    f"--template={template}",
                    str(bare),
                ],
                check=True,
                capture_output=True,
                env=_closed_git_environment(),
                timeout=30,
            )
            archive = subprocess.run(
                [
                    "git",
                    "--no-replace-objects",
                    f"--git-dir={bare}",
                    "-c",
                    "core.attributesFile=/dev/null",
                    "archive",
                    "--format=tar",
                    expected_commit,
                ],
                check=True,
                capture_output=True,
                env=_closed_git_environment(
                    GIT_ALTERNATE_OBJECT_DIRECTORIES=object_directory
                ),
                timeout=60,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise PreparationError(
                "the isolated Sovereign source archive failed"
            ) from exc
    if not archive:
        raise PreparationError("the isolated Sovereign source archive is empty")
    return {
        "archive_sha256": _digest(archive),
        "commit": expected_commit,
        "tree": tree,
    }


def _descriptor(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PreparationError(f"{label} must be one OCI descriptor")
    digest = _require_digest(value.get("digest"), f"{label} digest")
    size = value.get("size")
    if type(size) is not int or size < 0 or size > MAX_BLOB_BYTES:
        raise PreparationError(f"{label} size is invalid")
    media_type = value.get("mediaType")
    if not isinstance(media_type, str) or not media_type:
        raise PreparationError(f"{label} media type is invalid")
    return {"digest": digest, "mediaType": media_type, "size": size, **value}


def _blob(
    layout: Path,
    descriptor: dict[str, Any],
    label: str,
    *,
    limit: int = MAX_BLOB_BYTES,
) -> bytes:
    digest = _require_digest(descriptor.get("digest"), f"{label} digest")
    if descriptor.get("size", MAX_BLOB_BYTES + 1) > limit:
        raise PreparationError(f"{label} exceeds its size limit")
    algorithm, hexadecimal = digest.split(":", 1)
    path = layout / "blobs" / algorithm / hexadecimal
    data = _read_bounded(path, limit, label)
    if len(data) != descriptor.get("size") or _digest(data) != digest:
        raise PreparationError(f"{label} does not match its OCI descriptor")
    return data


def _public_configuration_excluded(image_config: dict[str, Any]) -> None:
    runtime = image_config.get("config")
    if runtime is None:
        runtime = {}
    if not isinstance(runtime, dict):
        raise PreparationError("OCI runtime configuration is invalid")
    environment = runtime.get("Env", [])
    if environment is None:
        environment = []
    if not isinstance(environment, list) or any(
        not isinstance(item, str) for item in environment
    ):
        raise PreparationError("OCI runtime environment is invalid")
    mount_values = [
        item.split("=", 1)[1]
        for item in environment
        if item.startswith("MOUNT_PROFILE=")
    ]
    if len(mount_values) > 1 or (mount_values and mount_values != [PROVIDER_MOUNT]):
        raise PreparationError("the Sovereign artifact contains a public mount profile")
    labels = runtime.get("Labels", {})
    if labels is None:
        labels = {}
    if not isinstance(labels, dict):
        raise PreparationError("OCI labels are invalid")
    declared_mount = labels.get("org.cosmaneura.mount-profile")
    if declared_mount is not None and declared_mount != PROVIDER_MOUNT:
        raise PreparationError("the Sovereign artifact declares a non-provider mount")


def _oci_identity(
    layout: Path,
    *,
    expected_digest: str,
    expected_revision: str,
    exclude_public: bool,
) -> dict[str, Any]:
    expected_digest = _require_digest(expected_digest, "expected image digest")
    expected_revision = _require_commit(expected_revision, "expected OCI revision")
    layout_metadata = _json_object(
        _read_bounded(layout / "oci-layout", MAX_JSON_BYTES, "OCI layout metadata"),
        "OCI layout metadata",
    )
    if layout_metadata != {"imageLayoutVersion": "1.0.0"}:
        raise PreparationError("OCI layout metadata is not version 1.0.0")
    index_bytes = _read_bounded(layout / "index.json", MAX_JSON_BYTES, "OCI index")
    index = _json_object(index_bytes, "OCI index")
    if index.get("schemaVersion") != 2 or index.get("mediaType") != INDEX_MEDIA_TYPE:
        raise PreparationError("OCI index identity is invalid")
    manifests = index.get("manifests")
    if not isinstance(manifests, list) or len(manifests) != 1:
        raise PreparationError("OCI index must contain exactly one manifest")
    manifest_descriptor = _descriptor(manifests[0], "OCI image manifest")
    if manifest_descriptor["mediaType"] not in MANIFEST_MEDIA_TYPES:
        raise PreparationError("OCI index does not select one image manifest")
    if manifest_descriptor.get("platform") != PLATFORM:
        raise PreparationError("OCI index does not select exactly linux/amd64")
    if manifest_descriptor["digest"] != expected_digest:
        raise PreparationError(
            "OCI manifest is not the expected immutable image digest"
        )
    manifest_bytes = _blob(
        layout,
        manifest_descriptor,
        "OCI image manifest",
        limit=MAX_JSON_BYTES,
    )
    manifest = _json_object(manifest_bytes, "OCI image manifest")
    if (
        manifest.get("schemaVersion") != 2
        or manifest.get("mediaType") not in MANIFEST_MEDIA_TYPES
    ):
        raise PreparationError("OCI image manifest media type is invalid")
    config_descriptor = _descriptor(manifest.get("config"), "OCI image config")
    if config_descriptor["mediaType"] not in CONFIG_MEDIA_TYPES:
        raise PreparationError("OCI image config media type is invalid")
    config_bytes = _blob(
        layout,
        config_descriptor,
        "OCI image config",
        limit=MAX_JSON_BYTES,
    )
    image_config = _json_object(config_bytes, "OCI image config")
    if image_config.get("os") != "linux" or image_config.get("architecture") != "amd64":
        raise PreparationError("OCI image config is not exactly linux/amd64")
    runtime = image_config.get("config")
    labels = runtime.get("Labels") if isinstance(runtime, dict) else None
    revision = (
        labels.get("org.opencontainers.image.revision")
        if isinstance(labels, dict)
        else None
    )
    if revision != expected_revision:
        raise PreparationError(
            "OCI revision label does not match the exact source commit"
        )
    if exclude_public:
        _public_configuration_excluded(image_config)

    layers = manifest.get("layers")
    if not isinstance(layers, list):
        raise PreparationError("OCI image manifest layers are invalid")
    layer_digests: list[str] = []
    for index_number, layer in enumerate(layers):
        layer_descriptor = _descriptor(layer, f"OCI layer {index_number}")
        _blob(layout, layer_descriptor, f"OCI layer {index_number}")
        layer_digests.append(layer_descriptor["digest"])
    rootfs = image_config.get("rootfs")
    diff_ids = rootfs.get("diff_ids") if isinstance(rootfs, dict) else None
    if (
        not isinstance(rootfs, dict)
        or rootfs.get("type") != "layers"
        or not isinstance(diff_ids, list)
        or len(diff_ids) != len(layers)
        or any(
            not isinstance(item, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", item) is None
            for item in diff_ids
        )
    ):
        raise PreparationError("OCI root filesystem metadata is invalid")
    return {
        "config_digest": config_descriptor["digest"],
        "image_digest": manifest_descriptor["digest"],
        "index_sha256": _digest(index_bytes),
        "layer_digests": layer_digests,
        "platform": dict(PLATFORM),
        "revision": expected_revision,
    }


def prepare(
    *,
    sovereign_repo: Path,
    sovereign_oci_layout: Path,
    sovereign_image_digest: str,
    core_oci_layout: Path,
    core_source_commit: str,
    core_image_digest: str,
    sovereign_source_commit: str = SOVEREIGN_SOURCE_COMMIT,
) -> dict[str, Any]:
    source = _source_identity(sovereign_repo, sovereign_source_commit)
    provider_artifact = _oci_identity(
        sovereign_oci_layout,
        expected_digest=sovereign_image_digest,
        expected_revision=sovereign_source_commit,
        exclude_public=True,
    )
    core_source_commit = _require_commit(core_source_commit, "Core source commit")
    core_candidate = _oci_identity(
        core_oci_layout,
        expected_digest=core_image_digest,
        expected_revision=core_source_commit,
        exclude_public=False,
    )
    pair = {
        "core_image_digest": core_candidate["image_digest"],
        "core_source_commit": core_source_commit,
        "sovereign_image_digest": provider_artifact["image_digest"],
        "sovereign_source_commit": sovereign_source_commit,
    }
    payload: dict[str, Any] = {
        "authorization": {
            "activation_authorized": False,
            "azure_mutation_authorized": False,
            "deployment_authorized": False,
            "production_enabled": False,
            "public_deployment_authorized": False,
        },
        "candidate_pair_sha256": _digest(_canonical(pair)),
        "contract": CONTRACT,
        "core_candidate": core_candidate,
        "environment": "production",
        "provider_artifact": provider_artifact,
        "public_release": {"image_included": False, "source_included": False},
        "schema_version": SCHEMA_VERSION,
        "scope": {"mount_profile": PROVIDER_MOUNT, "provider_only": True},
        "source": source,
    }
    return {"payload": payload, "payload_sha256": _digest(_canonical(payload))}


def _validate_static_contract(envelope: dict[str, Any]) -> None:
    if set(envelope) != {"payload", "payload_sha256"}:
        raise PreparationError("preparation envelope fields are invalid")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise PreparationError("preparation payload is invalid")
    if envelope.get("payload_sha256") != _digest(_canonical(payload)):
        raise PreparationError("preparation payload digest does not match")
    if (
        payload.get("contract") != CONTRACT
        or payload.get("schema_version") != SCHEMA_VERSION
    ):
        raise PreparationError("preparation contract identity is invalid")
    if payload.get("environment") != "production":
        raise PreparationError("preparation environment is invalid")
    if payload.get("scope") != {"mount_profile": PROVIDER_MOUNT, "provider_only": True}:
        raise PreparationError("preparation scope is not provider-only")
    if payload.get("public_release") != {
        "image_included": False,
        "source_included": False,
    }:
        raise PreparationError("public release material is not excluded")
    authorization = payload.get("authorization")
    if authorization != {
        "activation_authorized": False,
        "azure_mutation_authorized": False,
        "deployment_authorized": False,
        "production_enabled": False,
        "public_deployment_authorized": False,
    }:
        raise PreparationError("preparation authorization must remain fully disabled")


def verify(
    manifest: Path,
    *,
    sovereign_repo: Path,
    sovereign_oci_layout: Path,
    sovereign_image_digest: str,
    core_oci_layout: Path,
    core_source_commit: str,
    core_image_digest: str,
    sovereign_source_commit: str = SOVEREIGN_SOURCE_COMMIT,
) -> dict[str, Any]:
    observed = _json_object(
        _read_bounded(manifest, MAX_JSON_BYTES, "preparation manifest"),
        "preparation manifest",
    )
    _validate_static_contract(observed)
    expected = prepare(
        sovereign_repo=sovereign_repo,
        sovereign_oci_layout=sovereign_oci_layout,
        sovereign_image_digest=sovereign_image_digest,
        core_oci_layout=core_oci_layout,
        core_source_commit=core_source_commit,
        core_image_digest=core_image_digest,
        sovereign_source_commit=sovereign_source_commit,
    )
    if observed != expected:
        raise PreparationError(
            "preparation manifest does not match the exact current inputs"
        )
    return observed


def _write_new(path: Path, envelope: dict[str, Any]) -> None:
    data = _canonical(envelope) + b"\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise PreparationError(
            "the preparation manifest could not be created exclusively"
        ) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "verify"):
        command = subparsers.add_parser(name)
        command.add_argument("--sovereign-repo", type=Path, required=True)
        command.add_argument("--sovereign-oci-layout", type=Path, required=True)
        command.add_argument("--sovereign-image-digest", required=True)
        command.add_argument("--core-oci-layout", type=Path, required=True)
        command.add_argument("--core-source-commit", required=True)
        command.add_argument("--core-image-digest", required=True)
        command.add_argument(
            "--output" if name == "prepare" else "--manifest",
            type=Path,
            required=True,
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    common = {
        "sovereign_repo": arguments.sovereign_repo,
        "sovereign_oci_layout": arguments.sovereign_oci_layout,
        "sovereign_image_digest": arguments.sovereign_image_digest,
        "core_oci_layout": arguments.core_oci_layout,
        "core_source_commit": arguments.core_source_commit,
        "core_image_digest": arguments.core_image_digest,
    }
    try:
        if arguments.command == "prepare":
            envelope = prepare(**common)
            _validate_static_contract(envelope)
            _write_new(arguments.output, envelope)
        else:
            envelope = verify(arguments.manifest, **common)
    except PreparationError:
        print("provider production artifact preparation refused", file=sys.stderr)
        return 1
    print(envelope["payload_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
