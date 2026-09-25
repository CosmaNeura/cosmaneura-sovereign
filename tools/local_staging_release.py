#!/usr/bin/env python3
"""Locally build/publish or deploy one reviewed, merged provider staging image.

This separate path pairs current merged Core and Sovereign revisions. It does
not replace the historical source-pair admission or touch the public mount.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import runpy
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SUBSCRIPTION = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
TENANT = "d51754d0-bba5-4810-820e-afac542844d6"
GROUP = "cosmaneura-sovereign-staging"
APP = "cosmaneura-sovereign-staging-app"
REGISTRY = "cosmaneurasovereignstagingregistry"
IMAGE = REGISTRY + ".azurecr.io/cosmaneura/sovereign"
PUBLIC_APP = "cosmaneura-web-staging-app"
NIXPACKS_URL = "https://github.com/railwayapp/nixpacks/releases/download/v1.41.0/nixpacks-v1.41.0-aarch64-apple-darwin.tar.gz"
NIXPACKS_SHA256 = "0c6799690ad0b7328691fe3e457ec93ddc250ab229b7422fe8d8cb6ecc72a70c"
PREFIX = "<!-- COSMANEURA_LOCAL_CURSOR_REVIEW "


def require(value):
    if not value:
        raise ValueError("local_staging_release_refused")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def bound(path, digest):
    path = Path(path)
    require(path.resolve() == path and path.is_relative_to("/private/tmp"))
    require(re.fullmatch("[0-9a-f]{64}", digest))
    info = path.stat()
    require(
        path.is_file()
        and info.st_uid == os.geteuid()
        and info.st_nlink == 1
        and not info.st_mode & 0o022
    )
    raw = path.read_bytes()
    require(len(raw) <= 65536 and sha(raw) == digest)
    return json.loads(raw, object_pairs_hook=unique)


def command(args, *, env=None, data=None, cwd=ROOT):
    return subprocess.check_output(args, input=data, env=env, cwd=cwd)


def gh(endpoint):
    return json.loads(command(["gh", "api", endpoint]))


def az(*args):
    return json.loads(
        command(
            [
                "az",
                *args,
                "--subscription",
                SUBSCRIPTION,
                "--only-show-errors",
                "-o",
                "json",
            ]
        )
    )


def verify_pair(repo, selected):
    require(
        set(selected)
        == {"sha", "pr", "reviewed_head", "review_comment", "review_sha256"}
    )
    require(re.fullmatch("[0-9a-f]{40}", selected["sha"]))
    pr = gh(f"repos/CosmaNeura/{repo}/pulls/{selected['pr']}")
    branch = "master" if repo == "cosmaneura" else "main"
    require(pr["merged"] is True and pr["base"]["ref"] == branch)
    require(
        pr["merge_commit_sha"] == selected["sha"]
        and pr["head"]["sha"] == selected["reviewed_head"]
    )
    require(
        gh(f"repos/CosmaNeura/{repo}/git/ref/heads/{branch}")["object"]["sha"]
        == selected["sha"]
    )
    merged = gh(f"repos/CosmaNeura/{repo}/git/commits/{selected['sha']}")
    reviewed = gh(f"repos/CosmaNeura/{repo}/git/commits/{selected['reviewed_head']}")
    require(merged["tree"]["sha"] == reviewed["tree"]["sha"])
    review = gh(f"repos/CosmaNeura/{repo}/issues/comments/{selected['review_comment']}")
    require(review["user"]["login"] == "JohnClem" and review["user"]["type"] == "User")
    require(
        review["issue_url"]
        == f"https://api.github.com/repos/CosmaNeura/{repo}/issues/{selected['pr']}"
    )
    body = review["body"]
    require(type(body) is str and 0 < len(body) <= 65536)
    require(sha(body.encode()) == selected["review_sha256"])
    # Keep retained Cursor reviews valid and identify the authorized independent
    # reviewer honestly. Neither format represents GitHub approval.
    envelopes = {
        PREFIX: "Cursor",
        "<!-- COSMANEURA_LOCAL_AGENT_REVIEW ": "Codex",
    }
    prefixes = [prefix for prefix in envelopes if body.startswith(prefix)]
    require(len(prefixes) == 1)
    prefix = prefixes[0]
    metadata, separator, report = body[len(prefix) :].partition(" -->\n")
    require(separator and report.strip())
    metadata = json.loads(metadata, object_pairs_hook=unique)
    require(type(metadata) is dict and metadata.get("after_final_substantive_push") is True)
    require(
        metadata
        == {
            "reviewer": envelopes[prefix],
            "reviewed_head": selected["reviewed_head"],
            "result": "accepted",
            "after_final_substantive_push": True,
            "report_sha256": sha(report.encode()),
        }
    )


def validate_plan(plan):
    require(
        set(plan)
        == {
            "version",
            "core",
            "sovereign",
            "source_archive_sha256",
            "recovery",
            "public_before",
            "expires_at",
        }
    )
    require(
        plan["version"] == 1
        and type(plan["expires_at"]) is int
        and time.time() < plan["expires_at"] <= time.time() + 7200
    )
    require(re.fullmatch("[0-9a-f]{64}", plan["source_archive_sha256"]))
    for key, name in [("recovery", APP), ("public_before", PUBLIC_APP)]:
        value = plan[key]
        require(set(value) == {"image", "revision"})
        require(
            re.fullmatch(re.escape(IMAGE) + r"@sha256:[0-9a-f]{64}", value["image"])
        )
        require(re.fullmatch(re.escape(name) + r"--[a-z0-9-]+", value["revision"]))


def verify_source(plan):
    validate_plan(plan)
    require(gh("user")["login"] == "JohnClem")
    for repo, key in [("cosmaneura", "core"), ("cosmaneura-sovereign", "sovereign")]:
        verify_pair(repo, plan[key])
    require(command(["git", "status", "--porcelain", "--untracked-files=all"]) == b"")
    require(
        command(["git", "rev-parse", "HEAD"]).decode().strip()
        == plan["sovereign"]["sha"]
    )
    require(
        command(["git", "remote", "get-url", "origin"]).decode().strip()
        in {
            "git@github.com:CosmaNeura/cosmaneura-sovereign.git",
            "https://github.com/CosmaNeura/cosmaneura-sovereign.git",
        }
    )
    account = az("account", "show")
    require(
        account["id"] == SUBSCRIPTION
        and account["tenantId"] == TENANT
        and account["user"]["type"] == "user"
    )
    require(
        sha(account["user"]["name"].encode())
        == "acf2c107f43fa6950918e5f089e1b865ba56188295b88d3d72ad342d742e616a"
    )


def container_spec(container):
    """Remove only observed Azure app/revision serialization defaults.

    The revision API omits these fields. Preserve every other field so actual
    image, command, identity reference, probe and resource drift still fails.
    The storage exception is limited to this release's observed resource size.
    """
    result = copy.deepcopy(container)
    if result.get("imageType") == "ContainerImage":
        result.pop("imageType")
    for item in result.get("env", []):
        if item.get("secretRef") and item.get("value") in (None, ""):
            item.pop("value", None)
    resources = result.get("resources", {})
    if (
        resources.get("cpu") == 0.5
        and resources.get("memory") == "1Gi"
        and resources.get("ephemeralStorage") == "2Gi"
    ):
        resources.pop("ephemeralStorage")
    for probe in result.get("probes", []):
        if probe.get("initialDelaySeconds") == 0:
            probe.pop("initialDelaySeconds")
        http = probe.get("httpGet", {})
        if http.get("host") == "":
            http.pop("host")
        if http.get("httpHeaders") == []:
            http.pop("httpHeaders")
    return result


def state(name):
    item = az("containerapp", "show", "-g", GROUP, "-n", name)
    properties = item["properties"]
    containers = properties["template"]["containers"]
    require(
        len(containers) == 1
        and properties["latestRevisionName"] == properties["latestReadyRevisionName"]
    )
    configuration = properties["configuration"]
    require(configuration["activeRevisionsMode"] == "Single")
    traffic = configuration["ingress"]["traffic"]
    require(len(traffic) == 1 and traffic[0]["weight"] == 100)
    require(
        traffic[0].get("latestRevision") is True
        or traffic[0].get("revisionName") == properties["latestReadyRevisionName"]
    )
    observed = az(
        "containerapp",
        "revision",
        "show",
        "-g",
        GROUP,
        "-n",
        name,
        "--revision",
        properties["latestReadyRevisionName"],
    )["properties"]
    require(
        observed["healthState"] == "Healthy"
        and observed["provisioningState"] == "Provisioned"
    )
    require(
        [container_spec(c) for c in observed["template"]["containers"]]
        == [container_spec(c) for c in containers]
    )
    return {
        "image": containers[0]["image"],
        "revision": properties["latestReadyRevisionName"],
    }


def receipt(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
    Path(path).chmod(0o400)


def verify_retained_recovery(recovery):
    """Keep the plan's exact revision available, including its prior template."""
    properties = az("containerapp", "show", "-g", GROUP, "-n", APP)["properties"]
    require(properties["configuration"].get("maxInactiveRevisions") == 10)
    previous = az(
        "containerapp",
        "revision",
        "show",
        "-g",
        GROUP,
        "-n",
        APP,
        "--revision",
        recovery["revision"],
    )["properties"]
    containers = previous["template"]["containers"]
    require(
        previous["provisioningState"] == "Provisioned"
        and len(containers) == 1
        and containers[0]["image"] == recovery["image"]
    )


def build(plan, plan_digest, output):
    output = Path(output)
    require(
        output.resolve() == output
        and output.parent == Path("/private/tmp")
        and not output.exists()
    )
    output.mkdir(mode=0o700)
    source = output / "source"
    source.mkdir()
    # Exclude local Git info/attributes and credential files from the archive.
    metadata = output / "archive.git"
    template = output / "template"
    template.mkdir()
    clean = {
        "PATH": os.environ["PATH"],
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1",
    }
    command(
        [
            "git",
            "init",
            "--bare",
            "--quiet",
            "--template=" + str(template),
            str(metadata),
        ],
        env=clean,
    )
    objects = (
        command(["git", "rev-parse", "--path-format=absolute", "--git-path", "objects"])
        .decode()
        .strip()
    )
    archive = command(
        [
            "git",
            "--no-replace-objects",
            "--git-dir=" + str(metadata),
            "-c",
            "core.attributesFile=/dev/null",
            "archive",
            "--format=tar",
            plan["sovereign"]["sha"],
        ],
        env={**clean, "GIT_ALTERNATE_OBJECT_DIRECTORIES": objects},
    )
    require(sha(archive) == plan["source_archive_sha256"])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(source, filter="data")
    packed = urllib.request.urlopen(NIXPACKS_URL, timeout=60).read(4 * 1024 * 1024)
    require(sha(packed) == NIXPACKS_SHA256)
    native = output / "native"
    native.mkdir()
    with tarfile.open(fileobj=io.BytesIO(packed), mode="r:gz") as tar:
        tar.extractall(native, filter="data")
    revision = plan["sovereign"]["sha"]
    tag = revision[:12] + "-" + uuid4().hex[:12]
    image = IMAGE + ":" + tag
    subprocess.run(
        [
            str(native / "nixpacks"),
            "build",
            str(source),
            "--name",
            image,
            "--out",
            str(source),
            "--cache-key",
            revision,
            "--platform",
            "linux/amd64",
            "--label",
            "org.opencontainers.image.revision=" + revision,
            "--label",
            "org.cosmaneura.session-prep.build-admission=sha256:" + plan_digest,
        ],
        env=clean,
        check=True,
    )
    finalizer = runpy.run_path(str(ROOT / "infra/scripts/finalize-nixpacks-context.py"))
    epoch = int(command(["git", "show", "-s", "--format=%ct", revision]))
    finalizer["finalize"](source, image, revision, epoch, "sha256:" + plan_digest)
    docker_config = output / "docker"
    docker_config.mkdir(mode=0o700)
    socket = (
        command(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"]
        )
        .decode()
        .strip()
    )
    require(socket.startswith("unix://"))
    environment = {**clean, "DOCKER_HOST": socket, "DOCKER_CONFIG": str(docker_config)}
    args = [
        "docker",
        "build",
        "--platform",
        "linux/amd64",
        "-t",
        image,
        "-f",
        str(source / ".nixpacks/Dockerfile"),
    ]
    for key, value in finalizer["EXPECTED_BUILD_ARGS"].items():
        args += ["--build-arg", key + "=" + value]
    subprocess.run([*args, str(source)], env=environment, check=True)
    inspect = json.loads(
        command(["docker", "image", "inspect", image], env=environment)
    )[0]
    require(
        inspect["Architecture"] == "amd64"
        and inspect["Os"] == "linux"
        and inspect["Config"]["Labels"]["org.opencontainers.image.revision"] == revision
    )
    verify_source(plan)
    token = az("acr", "login", "--name", REGISTRY, "--expose-token")
    # An auth entry prevents Docker's automatic native credential-store
    # discovery. Use only this private file, never the user's macOS keychain.
    credential_file = docker_config / "config.json"
    credential_file.write_text(json.dumps({"auths": {REGISTRY + ".azurecr.io": {}}}))
    credential_file.chmod(0o600)
    try:
        command(
            [
                "docker",
                "login",
                REGISTRY + ".azurecr.io",
                "--username",
                "00000000-0000-0000-0000-000000000000",
                "--password-stdin",
            ],
            env=environment,
            data=token["accessToken"].encode(),
        )
        subprocess.run(["docker", "push", image], env=environment, check=True)
    finally:
        try:
            command(["docker", "logout", REGISTRY + ".azurecr.io"], env=environment)
        finally:
            credential_file.unlink(missing_ok=True)
    manifest = az(
        "acr",
        "manifest",
        "show-metadata",
        "--registry",
        REGISTRY,
        "--name",
        "cosmaneura/sovereign:" + tag,
    )
    digest = manifest["digest"]
    require(re.fullmatch("sha256:[0-9a-f]{64}", digest))
    local = json.loads(command(["docker", "image", "inspect", image], env=environment))[
        0
    ]
    require(
        IMAGE + "@" + digest in local["RepoDigests"] and local["Id"] == inspect["Id"]
    )
    az(
        "acr",
        "repository",
        "update",
        "--name",
        REGISTRY,
        "--image",
        "cosmaneura/sovereign:" + tag,
        "--write-enabled",
        "false",
        "--delete-enabled",
        "false",
    )
    receipt(
        output / "published.json",
        {
            "complete": True,
            "plan_sha256": plan_digest,
            "source_sha": revision,
            "image": IMAGE + "@" + digest,
            "image_id": inspect["Id"],
            "core_sha": plan["core"]["sha"],
        },
    )


def deploy(plan, plan_digest, published):
    require(published["complete"] is True and published["plan_sha256"] == plan_digest)
    require(
        published["source_sha"] == plan["sovereign"]["sha"]
        and published["core_sha"] == plan["core"]["sha"]
    )
    image = published["image"]
    require(re.fullmatch(re.escape(IMAGE) + r"@sha256:[0-9a-f]{64}", image))
    require(
        state(APP) == plan["recovery"] and state(PUBLIC_APP) == plan["public_before"]
    )
    for value in [plan["recovery"]["image"], image]:
        require(
            az(
                "acr",
                "manifest",
                "show-metadata",
                "--registry",
                REGISTRY,
                "--name",
                value.split(".io/")[1],
            )["digest"]
            == value.split("@")[1]
        )
    suffix = "sp" + plan["sovereign"]["sha"][:7] + uuid4().hex[:4]
    az(
        "containerapp",
        "update",
        "-g",
        GROUP,
        "-n",
        APP,
        "--image",
        image,
        "--revision-suffix",
        suffix,
        "--max-inactive-revisions",
        "10",
        "--set-env-vars",
        "FEATURE_SESSION_PREP=true",
        "SESSION_PREP_CONTEXT_SOURCE=platform",
        "FEATURE_ATHENA_LABS=false",
        "--remove-env-vars",
        "SESSION_PREP_SYNTHETIC_CONTEXT_REF",
    )
    for _ in range(60):
        item = az("containerapp", "show", "-g", GROUP, "-n", APP)
        if item["properties"]["latestReadyRevisionName"] == APP + "--" + suffix:
            break
        time.sleep(5)
    require(state(APP) == {"image": image, "revision": APP + "--" + suffix})
    require(state(PUBLIC_APP) == plan["public_before"])
    verify_retained_recovery(plan["recovery"])
    print(
        json.dumps(
            {
                "deployment_observed": True,
                "user_workflow_verified": False,
                "image": image,
                "revision": APP + "--" + suffix,
                "recovery": plan["recovery"],
                "recovery_revision_retained": True,
            }
        )
    )


def main(args):
    require(len(args) in {4, 5} and args[0] in {"build", "deploy"})
    plan = bound(args[1], args[2])
    verify_source(plan)
    if args[0] == "build":
        require(len(args) == 4)
        build(plan, args[2], args[3])
    else:
        require(len(args) == 5)
        deploy(plan, args[2], bound(args[3], args[4]))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError):
        raise SystemExit(
            "local_staging_release_refused; preserve attempt evidence"
        ) from None
