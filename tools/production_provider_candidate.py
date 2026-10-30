#!/usr/bin/env python3
"""Prepare/select a trusted provider-only artifact; never deploy or activate it.

The authenticated GitHub producer and artifact association establish provenance.
The JSON document alone, an image label or a readiness Boolean cannot do that.
Core pairing, production identity/TLS/schema and runtime acceptance remain separate.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

from tools.prepare_provider_production_artifact import (
    MANIFEST_MEDIA_TYPES,
    PreparationError,
    _closed_git_environment,
    _git,
)

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "CosmaNeura/cosmaneura-sovereign"
WORKFLOW = ".github/workflows/prepare_provider_candidate.yml"
WORKFLOW_REF = f"{REPOSITORY}/{WORKFLOW}@refs/heads/main"
REGISTRY = "cosmaneurasovereignprodregistry"
IMAGE_REPOSITORY = "cosmaneura/sovereign"
IMAGE = f"{REGISTRY}.azurecr.io/{IMAGE_REPOSITORY}"
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
MAX_JSON = 128 * 1024
MAX_METADATA = 1024 * 1024
MAX_ARCHIVE = 1024 * 1024
MAX_SOURCE = 32 * 1024 * 1024
DARK_ENV = {
    "MOUNT_PROFILE": "provider_assistant",
    "FEATURE_SESSION_PREP": "false",
    "SESSION_PREP_CONTEXT_SOURCE": "synthetic",
}
JOBS = {
    "Authorize provider artifact preparation",
    "Provider artifact quality & tests",
    "Provider artifact infrastructure",
    "Prepare provider candidate",
    "Select provider candidate",
}
CONTRACT_FILES = (
    WORKFLOW,
    "tools/production_provider_candidate.py",
    "tools/prepare_provider_production_artifact.py",
    "infra/scripts/finalize-nixpacks-context.py",
    "nixpacks.toml",
    "requirements.lock",
    "app/main.py",
    "app/config.py",
    "app/security/auth.py",
    "app/sovereign/orchestrator.py",
    "app/panel.js",
)
IMAGE_SOURCE_FILES = (
    "nixpacks.toml",
    "requirements.lock",
    "app/main.py",
    "app/config.py",
    "app/security/auth.py",
    "app/sovereign/orchestrator.py",
    "app/panel.js",
)
NIXPACKS_URL = (
    "https://github.com/railwayapp/nixpacks/releases/download/v1.41.0/"
    "nixpacks-v1.41.0-x86_64-unknown-linux-gnu.tar.gz"
)
NIXPACKS_SHA256 = "194bcad8c379f78a309eee1a88b2e6b2abc59f354efe7ecd7b4bbaf21de99a06"


class CandidateRefused(ValueError):
    """Closed, value-free refusal; upstream credentials/config are not printed."""


def require(condition: Any) -> None:
    if not condition:
        raise CandidateRefused("production_provider_candidate_refused")


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def decode(raw: bytes, maximum: int = MAX_JSON) -> Any:
    require(0 < len(raw) <= maximum)

    def unique(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            require(key not in value)
            value[key] = item
        return value

    def invalid(_value: str) -> None:
        raise CandidateRefused("production_provider_candidate_refused")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def positive(value: Any) -> int:
    require(type(value) is int and 0 < value < 10**20)
    return value


def command(
    args: list[str],
    *,
    github: bool = False,
    maximum: int = MAX_METADATA,
    timeout: int = 60,
    extra: dict[str, str] | None = None,
) -> bytes:
    allowed = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL")
    if args[0] in {"docker", "az"}:
        allowed += ("DOCKER_CONFIG", "AZURE_CONFIG_DIR", "AZURE_EXTENSION_DIR")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    if github:
        require(bool(os.environ.get("GH_TOKEN")))
        environment["GH_TOKEN"] = os.environ["GH_TOKEN"]
    environment.update(_closed_git_environment())
    environment.update(extra or {})
    # A bounded private spool plus client deadline, not a streaming output limit.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            args,
            stdout=output,
            stderr=subprocess.DEVNULL,
            env=environment,
            check=False,
            timeout=timeout,
        )
        require(result.returncode == 0)
        output.seek(0)
        raw = output.read(maximum + 1)
        require(len(raw) <= maximum)
        return raw


def api(path: str) -> Any:
    require(path.startswith(f"repos/{REPOSITORY}/") and ".." not in path)
    return decode(
        command(["gh", "api", "--hostname", "github.com", path], github=True),
        MAX_METADATA,
    )


def source_context() -> dict[str, Any]:
    expected = {
        "GITHUB_ACTOR": "JohnClem",
        "GITHUB_TRIGGERING_ACTOR": "JohnClem",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
    }
    require(all(os.environ.get(key) == value for key, value in expected.items()))
    sha = os.environ.get("GITHUB_SHA", "")
    require(SHA.fullmatch(sha))
    identity = source_identity(sha)
    require(api(f"repos/{REPOSITORY}/git/ref/heads/main")["object"]["sha"] == sha)
    run = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    require(re.fullmatch(r"[1-9][0-9]{0,19}", run))
    require(re.fullmatch(r"[1-9][0-9]{0,9}", attempt))
    contract = {path: digest((ROOT / path).read_bytes()) for path in CONTRACT_FILES}
    return {
        "source_sha": sha,
        "source_tree": identity["tree"],
        "source_archive_digest": identity["archive_sha256"],
        "build_contract_digest": digest(canonical(contract)),
        "run_id": int(run),
        "run_attempt": int(attempt),
    }


def source_identity(sha: str) -> dict[str, str]:
    # Preserve the offline pair preparer's two-parent/all-false contract separately.
    filters = _git(
        ROOT, "config", "--includes", "--get-regexp", r"^filter\.", check=False
    )
    require(filters.returncode == 1)
    require(not _git(ROOT, "status", "--porcelain", "--untracked-files=all").stdout)
    head = _git(ROOT, "rev-parse", "--verify", "HEAD^{commit}").stdout.decode().strip()
    require(head == sha and SHA.fullmatch(sha))
    tree = _git(ROOT, "rev-parse", "--verify", "HEAD^{tree}").stdout.decode().strip()
    require(SHA.fullmatch(tree))
    return {"tree": tree, "archive_sha256": digest(source_archive(sha))}


def source_archive(sha: str) -> bytes:
    git = ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(ROOT)]
    objects = (
        command(
            [
                *git,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "objects",
            ]
        )
        .decode()
        .strip()
    )
    require(Path(objects).is_dir())
    with tempfile.TemporaryDirectory() as directory:
        bare, template = Path(directory) / "archive.git", Path(directory) / "template"
        template.mkdir()
        command(
            ["git", "init", "--bare", "--quiet", f"--template={template}", str(bare)]
        )
        raw = command(
            [
                "git",
                "--no-replace-objects",
                f"--git-dir={bare}",
                "-c",
                "core.attributesFile=/dev/null",
                "archive",
                "--format=tar",
                sha,
            ],
            maximum=MAX_SOURCE,
            extra={"GIT_ALTERNATE_OBJECT_DIRECTORIES": objects},
        )
    return raw


def extract_source(raw: bytes, destination: Path) -> None:
    require(0 < len(raw) <= MAX_SOURCE)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        members = archive.getmembers()
        require(members and len(members) <= 10000)
        require(all(item.isfile() or item.isdir() for item in members))
        # No source-controlled links/devices or path escape enters the builder.
        archive.extractall(destination, members=members, filter="data")


def image_source(context: dict[str, Any], config: Any) -> dict[str, Any]:
    require(type(config) is dict)
    require(config.get("Os") == "linux" and config.get("Architecture") == "amd64")
    require(type(config.get("Id")) is str and DIGEST.fullmatch(config["Id"]))
    runtime = config["Config"]
    require(
        runtime["Labels"]["org.opencontainers.image.revision"] == context["source_sha"]
    )
    env = runtime["Env"]
    require(type(env) is list and all(type(item) is str for item in env))
    for key, value in DARK_ENV.items():
        require(
            [item for item in env if item.startswith(key + "=")] == [key + "=" + value]
        )
    return config


def manifest_identity(raw: bytes, expected: str, config_digest: str) -> None:
    require(type(expected) is str and DIGEST.fullmatch(expected))
    if digest(raw) != expected and raw.endswith(b"\n"):
        raw = raw[:-1]
    require(digest(raw) == expected)
    manifest = decode(raw)
    require(
        manifest["schemaVersion"] == 2 and manifest["mediaType"] in MANIFEST_MEDIA_TYPES
    )
    require(manifest["config"]["digest"] == config_digest)
    layers = manifest["layers"]
    require(type(layers) is list and layers)
    require(
        all(
            type(item) is dict and DIGEST.fullmatch(item.get("digest", ""))
            for item in layers
        )
    )


def source_files_in_image(config_digest: str) -> None:
    """Check fixed source/build files in an owned stopped image; never execute it."""
    owned = "provider-candidate-proof-" + uuid.uuid4().hex
    created = False
    try:
        identity = (
            command(
                [
                    "docker",
                    "create",
                    "--name",
                    owned,
                    "--network",
                    "none",
                    config_digest,
                ]
            )
            .decode()
            .strip()
        )
        created = True
        require(re.fullmatch(r"[0-9a-f]{64}", identity))
        with tempfile.TemporaryDirectory() as directory:
            for index, path in enumerate(IMAGE_SOURCE_FILES):
                output = Path(directory) / str(index)
                command(["docker", "cp", owned + ":/app/" + path, str(output)])
                require(output.is_file() and not output.is_symlink())
                require(output.stat().st_size <= MAX_JSON)
                require(
                    digest(output.read_bytes()) == digest((ROOT / path).read_bytes())
                )
    finally:
        if created:
            command(["docker", "rm", owned])


def validate_record(
    value: Any,
    context: dict[str, Any],
    run_id: int,
    attempt: int,
) -> dict[str, Any]:
    fields = set(
        "schema_version purpose repository workflow_ref source_sha source_tree "
        "source_archive_digest build_contract_digest run_id run_attempt image "
        "image_config_digest dark_configuration deployment_authorized".split()
    )
    require(type(value) is dict and set(value) == fields)
    require(type(value["schema_version"]) is int and value["schema_version"] == 1)
    require(value["purpose"] == "verified-production-provider-candidate")
    require(value["repository"] == REPOSITORY and value["workflow_ref"] == WORKFLOW_REF)
    for key in (
        "source_sha",
        "source_tree",
        "source_archive_digest",
        "build_contract_digest",
    ):
        require(value[key] == context[key])
    require(
        positive(value["run_id"]) == run_id
        and positive(value["run_attempt"]) == attempt
    )
    require(
        type(value["image"]) is str
        and re.fullmatch(re.escape(IMAGE) + r"@sha256:[0-9a-f]{64}", value["image"])
    )
    require(
        type(value["image_config_digest"]) is str
        and DIGEST.fullmatch(value["image_config_digest"])
    )
    require(
        value["dark_configuration"] == DARK_ENV
        and value["deployment_authorized"] is False
    )
    return value


def archive_record(raw: bytes, expected_digest: str) -> Any:
    require(0 < len(raw) <= MAX_ARCHIVE and digest(raw) == expected_digest)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) == 1)
        item = entries[0]
        require(item.filename == "candidate.json" and not item.is_dir())
        require(not item.flag_bits & 1 and 0 < item.file_size <= MAX_JSON)
        require(not stat.S_ISLNK(item.external_attr >> 16))
        with archive.open(item) as stream:
            return decode(stream.read(MAX_JSON + 1))


def verify_evidence(
    context: dict[str, Any],
    run_id: int,
    attempt: int,
    artifact_id: int,
    run: Any,
    workflow: Any,
    jobs: Any,
    artifact: Any,
    archive: bytes,
) -> dict[str, Any]:
    run_id, attempt, artifact_id = (
        positive(value) for value in (run_id, attempt, artifact_id)
    )
    require(run_id != context["run_id"])
    require(positive(run["id"]) == run_id and positive(run["run_attempt"]) == attempt)
    require(run["status"] == "completed" and run["conclusion"] == "success")
    require(run["event"] == "workflow_dispatch" and run["head_branch"] == "main")
    require(
        run["repository"]["full_name"] == REPOSITORY
        and run["head_sha"] == context["source_sha"]
    )
    require(
        run["actor"]["login"] == "JohnClem"
        and run["triggering_actor"]["login"] == "JohnClem"
    )
    require(workflow["path"] == WORKFLOW and workflow["state"] == "active")
    require(
        run["path"] == WORKFLOW
        and positive(run["workflow_id"]) == positive(workflow["id"])
    )
    observed = jobs["jobs"]
    require(jobs["total_count"] == len(JOBS) and len(observed) == len(JOBS))
    require(len({positive(item["id"]) for item in observed}) == len(JOBS))
    require({item["name"] for item in observed} == JOBS)
    require(
        all(
            item["head_sha"] == context["source_sha"]
            and positive(item["run_id"]) == run_id
            and positive(item["run_attempt"]) == attempt
            and item["status"] == "completed"
            and item["conclusion"]
            == ("skipped" if item["name"] == "Select provider candidate" else "success")
            for item in observed
        )
    )
    require(positive(artifact["id"]) == artifact_id and artifact["expired"] is False)
    require(
        artifact["name"]
        == f"provider-production-candidate-{run_id}-{attempt}-{context['source_sha']}"
    )
    require(
        type(artifact["size_in_bytes"]) is int
        and 0 < artifact["size_in_bytes"] <= MAX_ARCHIVE
    )
    require(
        type(artifact.get("digest")) is str and DIGEST.fullmatch(artifact["digest"])
    )
    association = artifact["workflow_run"]
    require(
        positive(association["id"]) == run_id
        and association["head_sha"] == context["source_sha"]
        and association["head_branch"] == "main"
    )
    repository_id = positive(run["repository"]["id"])
    require(
        positive(association["repository_id"]) == repository_id
        and positive(association["head_repository_id"]) == repository_id
    )
    return validate_record(
        archive_record(archive, artifact["digest"]), context, run_id, attempt
    )


def admit(run_id: int, attempt: int, artifact_id: int) -> tuple[dict[str, Any], str]:
    run_id, attempt, artifact_id = (
        positive(value) for value in (run_id, attempt, artifact_id)
    )
    context = source_context()
    prefix = f"repos/{REPOSITORY}/actions"
    run = api(f"{prefix}/runs/{run_id}")
    workflow = api(f"{prefix}/workflows/prepare_provider_candidate.yml")
    jobs = api(f"{prefix}/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    artifact = api(f"{prefix}/artifacts/{artifact_id}")
    require(
        artifact.get("expired") is False and type(artifact.get("size_in_bytes")) is int
    )
    require(0 < artifact["size_in_bytes"] <= MAX_ARCHIVE)
    raw = command(
        [
            "gh",
            "api",
            "--hostname",
            "github.com",
            f"{prefix}/artifacts/{artifact_id}/zip",
        ],
        github=True,
        maximum=MAX_ARCHIVE,
    )
    record = verify_evidence(
        context, run_id, attempt, artifact_id, run, workflow, jobs, artifact, raw
    )
    require(source_context() == context)
    return record, artifact["digest"]


def retention(record: dict[str, Any]) -> None:
    image_digest = record["image"].split("@")[1]
    tag = f"candidate-{record['source_sha']}-{record['run_id']}-{record['run_attempt']}"
    for reference in (
        IMAGE_REPOSITORY + ":" + tag,
        IMAGE_REPOSITORY + "@" + image_digest,
    ):
        observed = decode(
            command(
                [
                    "az",
                    "acr",
                    "repository",
                    "show",
                    "--name",
                    REGISTRY,
                    "--image",
                    reference,
                    "--output",
                    "json",
                ]
            )
        )
        require(observed["digest"] == image_digest)
        require(
            observed["changeableAttributes"]
            == {
                "writeEnabled": False,
                "deleteEnabled": False,
                "readEnabled": True,
                "listEnabled": True,
            }
        )


def build() -> None:
    context = source_context()
    require(os.environ.get("PROVIDER_ARTIFACT_PREPARATION_ENABLED") == "true")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source, vendor = root / "source", root / "nixpacks"
        source.mkdir()
        vendor.mkdir()
        raw_source = source_archive(context["source_sha"])
        require(digest(raw_source) == context["source_archive_digest"])
        extract_source(raw_source, source)
        raw = command(
            [
                "curl",
                "--disable",
                "--proto",
                "=https",
                "--proto-redir",
                "=https",
                "--fail",
                "--location",
                "--silent",
                "--show-error",
                NIXPACKS_URL,
            ],
            maximum=MAX_SOURCE,
        )
        require(hashlib.sha256(raw).hexdigest() == NIXPACKS_SHA256)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            members = archive.getmembers()
            require(members and all(item.isfile() or item.isdir() for item in members))
            archive.extractall(vendor, members=members, filter="data")
        nixpacks = vendor / "nixpacks"
        require(nixpacks.is_file() and not nixpacks.is_symlink())
        require(
            command([str(nixpacks), "--version"]).decode().strip() == "nixpacks 1.41.0"
        )
        image = "provider-candidate:" + context["source_sha"]
        command(
            [
                str(nixpacks),
                "build",
                str(source),
                "--name",
                image,
                "--out",
                str(source),
                "--cache-key",
                context["source_sha"],
                "--platform",
                "linux/amd64",
                "--label",
                "org.opencontainers.image.revision=" + context["source_sha"],
            ]
        )
        epoch = (
            command(
                [
                    "git",
                    "--no-replace-objects",
                    "-C",
                    str(ROOT),
                    "show",
                    "-s",
                    "--format=%ct",
                    context["source_sha"],
                ]
            )
            .decode()
            .strip()
        )
        require(re.fullmatch(r"[0-9]{1,12}", epoch))
        command(
            [
                "python3",
                "-I",
                "-B",
                "-S",
                str(ROOT / "infra/scripts/finalize-nixpacks-context.py"),
                str(source),
                image,
                context["source_sha"],
                epoch,
            ]
        )
        dockerfile = source / ".nixpacks/Dockerfile"
        with dockerfile.open("a") as stream:
            stream.write(
                "\nENV "
                + " ".join(key + "=" + value for key, value in DARK_ENV.items())
                + "\n"
            )
        os.utime(dockerfile, (int(epoch), int(epoch)))
        require(source_context() == context)
        command(
            [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "-t",
                image,
                "-f",
                str(dockerfile),
                str(source),
                *[
                    part
                    for key, value in {
                        "NIXPACKS_METADATA": "python",
                        "NIXPACKS_PYTHON_VERSION": "3.13",
                        "PIP_DEFAULT_TIMEOUT": "100",
                        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                        "PIP_NO_CACHE_DIR": "1",
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONFAULTHANDLER": "1",
                        "PYTHONHASHSEED": "random",
                        "PYTHONUNBUFFERED": "1",
                    }.items()
                    for part in ("--build-arg", key + "=" + value)
                ],
            ],
            timeout=3600,
        )
        inspected = decode(command(["docker", "image", "inspect", image]))
        require(type(inspected) is list and len(inspected) == 1)
        image_source(context, inspected[0])
        require(source_context() == context)


def write_new(output: Path, record: dict[str, Any]) -> None:
    descriptor = os.open(
        output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical(record) + b"\n")


def publish(output: Path) -> None:
    context = source_context()
    require(os.environ.get("PROVIDER_ARTIFACT_PREPARATION_ENABLED") == "true")
    local = "provider-candidate:" + context["source_sha"]
    inspected = decode(command(["docker", "image", "inspect", local]))
    require(type(inspected) is list and len(inspected) == 1)
    config = image_source(context, inspected[0])
    source_files_in_image(config["Id"])
    require(source_context() == context)
    tag = f"candidate-{context['source_sha']}-{context['run_id']}-{context['run_attempt']}"
    upload = (
        f"upload-{context['run_id']}-{context['run_attempt']}-{context['source_sha']}"
    )
    command(["docker", "tag", config["Id"], IMAGE + ":" + upload])
    require(source_context() == context)
    command(["docker", "push", IMAGE + ":" + upload], timeout=900)
    descriptor = decode(
        command(
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                "--format",
                "{{json .Manifest}}",
                IMAGE + ":" + upload,
            ]
        )
    )
    image_digest = descriptor["digest"]
    require(type(image_digest) is str and DIGEST.fullmatch(image_digest))
    raw = command(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            "--raw",
            IMAGE + "@" + image_digest,
        ]
    )
    manifest_identity(raw, image_digest, config["Id"])
    record = validate_record(
        {
            "schema_version": 1,
            "purpose": "verified-production-provider-candidate",
            "repository": REPOSITORY,
            "workflow_ref": WORKFLOW_REF,
            **context,
            "image": IMAGE + "@" + image_digest,
            "image_config_digest": config["Id"],
            "dark_configuration": dict(DARK_ENV),
            "deployment_authorized": False,
        },
        context,
        context["run_id"],
        context["run_attempt"],
    )
    require(source_context() == context)
    command(
        [
            "az",
            "acr",
            "import",
            "--name",
            REGISTRY,
            "--registry",
            REGISTRY,
            "--source",
            IMAGE_REPOSITORY + "@" + image_digest,
            "--image",
            IMAGE_REPOSITORY + ":" + tag,
            "--only-show-errors",
            "--output",
            "none",
        ]
    )
    for reference in (
        IMAGE_REPOSITORY + ":" + tag,
        IMAGE_REPOSITORY + "@" + image_digest,
    ):
        require(source_context() == context)
        command(
            [
                "az",
                "acr",
                "repository",
                "update",
                "--name",
                REGISTRY,
                "--image",
                reference,
                "--write-enabled",
                "false",
                "--delete-enabled",
                "false",
                "--only-show-errors",
                "--output",
                "none",
            ]
        )
    retention(record)
    require(source_context() == context)
    write_new(output, record)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("context", "build", "publish", "select"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--producer-run-id", type=int)
    parser.add_argument("--producer-attempt", type=int)
    parser.add_argument("--artifact-id", type=int)
    args = parser.parse_args()
    try:
        if args.mode == "context":
            source_context()
        elif args.mode == "build":
            build()
        elif args.mode == "publish":
            require(args.output is not None)
            publish(args.output)
        else:
            run_id, attempt, artifact_id = (
                positive(value)
                for value in (
                    args.producer_run_id,
                    args.producer_attempt,
                    args.artifact_id,
                )
            )
            record, archive_digest = admit(run_id, attempt, artifact_id)
            require(args.output is not None)
            write_new(
                args.output,
                {
                    "purpose": "provider-artifact-provenance-selection-only",
                    "candidate": record,
                    "artifact_id": artifact_id,
                    "artifact_archive_digest": archive_digest,
                    "selection_run_id": positive(int(os.environ["GITHUB_RUN_ID"])),
                    "selection_attempt": positive(
                        int(os.environ["GITHUB_RUN_ATTEMPT"])
                    ),
                    "deployment_authorized": False,
                },
            )
    except (
        CandidateRefused,
        PreparationError,
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
        tarfile.TarError,
        RuntimeError,
    ):
        print("production_provider_candidate_refused", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
