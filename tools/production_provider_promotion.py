#!/usr/bin/env python3
"""Image-only dark promotion of an already established sole provider app.

No bootstrap, Terraform, build, publication, environment/secret loading, identity
grants or Session Prep activation. A completed producer is authenticated without
spoofing its workflow context. Core's separately authenticated runtime check is
sampled image/revision/panel evidence, never beta qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from tools import production_provider_candidate as producer

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/promote_established_provider_candidate.yml"
WORKFLOW_REF = f"{producer.REPOSITORY}/{WORKFLOW}@refs/heads/main"
CORE_REPOSITORY = "CosmaNeura/cosmaneura"
# Exact locally integrated PR902 candidate; publication and merge are still required.
CORE_REVIEWED_HEAD = "418c8a55f836dbf793a71f6e6d43481b66ce8aa4"
CORE_BINDING_PR = 902
SUBSCRIPTION = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
TENANT = "d51754d0-bba5-4810-820e-afac542844d6"
GROUP = "cosmaneura-sovereign-prod"
APP = GROUP + "-app"
CONTAINER = GROUP + "-app-web"
PREFIX = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers"
IDENTITY = (
    PREFIX + "/Microsoft.ManagedIdentity/userAssignedIdentities/" + GROUP + "-runtime"
)
ENVIRONMENT = (
    PREFIX + "/Microsoft.App/managedEnvironments/" + GROUP + "-app-environment"
)
DEID_ID = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/cosmaneura-prod/providers/Microsoft.HealthDataAIServices/deidServices/AnonymizerAPI"
VAULT = "cosmasovprodprovider"
SECRETS = {
    "AZURE_OPENAI_API_KEY": "azure-openai-api-key",
    "MAGISTERIUM_API_KEY": "magisterium-api-key",
    "SOVEREIGN_RECORD_GRANT_SECRET": "sovereign-record-grant-secret",
}
GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
UNPROVED = [
    "configuration-qualification",
    "schema-and-database-tls",
    "entra-caller-and-role",
    "provider-origin-and-tls",
    "compatible-recovery-pair",
    "live-clinical-acceptance",
]
require = producer.require


def object_value(value: Any) -> dict[str, Any]:
    require(type(value) is dict)
    return value


def object_list(value: Any) -> list[dict[str, Any]]:
    if type(value) is not list:
        raise producer.CandidateRefused()
    return [object_value(item) for item in value]


def same_resource(value: Any, expected: str) -> bool:
    return type(value) is str and value.casefold() == expected.casefold()


def context() -> dict[str, Any]:
    expected = {
        "GITHUB_ACTOR": "JohnClem",
        "GITHUB_TRIGGERING_ACTOR": "JohnClem",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": producer.REPOSITORY,
        "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
    }
    require(all(os.environ.get(key) == value for key, value in expected.items()))
    require(os.environ.get("PROVIDER_IMMUTABLE_PROMOTION_ENABLED") == "true")
    client = os.environ.get("PROVIDER_PROMOTION_CLIENT_ID", "")
    require(GUID.fullmatch(client))
    require(
        all(
            not os.environ.get(key) or os.environ[key].casefold() != client.casefold()
            for key in ("LEGACY_DEPLOY_CLIENT_ID", "ARTIFACT_PREPARATION_CLIENT_ID")
        )
    )
    sha = os.environ.get("GITHUB_SHA", "")
    require(producer.SHA.fullmatch(sha))
    identity = producer.source_identity(sha)
    require(
        producer.api(f"repos/{producer.REPOSITORY}/git/ref/heads/main")["object"]["sha"]
        == sha
    )
    run_id = producer.positive(int(os.environ["GITHUB_RUN_ID"]))
    attempt = producer.positive(int(os.environ["GITHUB_RUN_ATTEMPT"]))
    run = object_value(
        producer.api(f"repos/{producer.REPOSITORY}/actions/runs/{run_id}")
    )
    require(
        producer.positive(run["id"]) == run_id
        and producer.positive(run["run_attempt"]) == attempt
    )
    require(
        run["event"] == "workflow_dispatch"
        and run["head_branch"] == "main"
        and run["head_sha"] == sha
        and run["path"] == WORKFLOW
    )
    require(run["status"] == "in_progress" and run["conclusion"] is None)
    require(
        run["repository"]["full_name"] == producer.REPOSITORY
        and run["actor"]["login"] == run["triggering_actor"]["login"] == "JohnClem"
    )
    contract = {
        path: producer.digest((ROOT / path).read_bytes())
        for path in producer.CONTRACT_FILES
    }
    return {
        "source_sha": sha,
        "source_tree": identity["tree"],
        "source_archive_digest": identity["archive_sha256"],
        "build_contract_digest": producer.digest(producer.canonical(contract)),
        "run_id": run_id,
        "run_attempt": attempt,
    }


def candidate(
    context_value: dict[str, Any], run_id: int, attempt: int, artifact_id: int
) -> dict[str, Any]:
    run_id, attempt, artifact_id = (
        producer.positive(value) for value in (run_id, attempt, artifact_id)
    )
    prefix = f"repos/{producer.REPOSITORY}/actions"
    run = producer.api(f"{prefix}/runs/{run_id}")
    workflow = producer.api(f"{prefix}/workflows/prepare_provider_candidate.yml")
    jobs = producer.api(f"{prefix}/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    artifact = object_value(producer.api(f"{prefix}/artifacts/{artifact_id}"))
    require(
        artifact.get("expired") is False
        and type(artifact.get("size_in_bytes")) is int
        and 0 < artifact["size_in_bytes"] <= producer.MAX_ARCHIVE
    )
    raw = producer.command(
        [
            "gh",
            "api",
            "--hostname",
            "github.com",
            f"{prefix}/artifacts/{artifact_id}/zip",
        ],
        github=True,
        maximum=producer.MAX_ARCHIVE,
    )
    return producer.verify_evidence(
        context_value, run_id, attempt, artifact_id, run, workflow, jobs, artifact, raw
    )


def azure_profile() -> dict[str, str]:
    # Preserve only the existing Azure CLI profile, never select a new path.
    return {
        key: os.environ[key]
        for key in ("AZURE_CONFIG_DIR", "AZURE_EXTENSION_DIR")
        if key in os.environ
    }


def core_command(arguments: list[str]) -> bytes:
    metadata = (
        arguments[:4] == ["gh", "api", "--hostname", "github.com"]
        and len(arguments) == 5
        and arguments[4].startswith(f"repos/{CORE_REPOSITORY}/")
        and ".." not in arguments[4]
    )
    check = (
        len(arguments) == 10
        and arguments[:2] == [sys.executable, "-B"]
        and arguments[3:5] == ["check", "--run-id"]
        and arguments[6] == "--run-attempt"
        and arguments[8] == "--artifact-id"
    )
    if check:
        expected = (
            Path(os.environ["GITHUB_WORKSPACE"])
            / "core/scripts/production_core_runtime_binding.py"
        )
        require(
            arguments[2] == str(expected)
            and all(
                re.fullmatch(r"[1-9][0-9]*", arguments[index]) for index in (5, 7, 9)
            )
        )
    require(metadata or check)
    token = os.environ.get("CORE_ARTIFACT_READ_TOKEN", "")
    require(bool(token) and "\x00" not in token)
    return producer.command(
        arguments,
        github=True,
        extra={"GH_TOKEN": token, **(azure_profile() if check else {})},
        maximum=producer.MAX_METADATA,
        timeout=1800 if check else 60,
    )


def core_api(path: str) -> Any:
    require(path.startswith(f"repos/{CORE_REPOSITORY}/") and ".." not in path)
    return producer.decode(
        core_command(["gh", "api", "--hostname", "github.com", path]),
        producer.MAX_METADATA,
    )


def core_source() -> dict[str, str]:
    pull = object_value(core_api(f"repos/{CORE_REPOSITORY}/pulls/{CORE_BINDING_PR}"))
    require(
        pull.get("merged") is True
        and pull["base"]["ref"] == "master"
        and pull["base"]["repo"]["full_name"] == CORE_REPOSITORY
        and pull["head"]["sha"] == CORE_REVIEWED_HEAD
    )
    merge = pull["merge_commit_sha"]
    require(type(merge) is str and producer.SHA.fullmatch(merge))
    sha = core_api(f"repos/{CORE_REPOSITORY}/git/ref/heads/master")["object"]["sha"]
    require(type(sha) is str and producer.SHA.fullmatch(sha))
    commit = core_api(f"repos/{CORE_REPOSITORY}/git/commits/{sha}")
    require(commit["sha"] == sha and producer.SHA.fullmatch(commit["tree"]["sha"]))
    comparison = core_api(f"repos/{CORE_REPOSITORY}/compare/{merge}...{sha}")
    require(
        comparison["status"] in ("ahead", "identical")
        and comparison["base_commit"]["sha"] == merge
    )
    return {"sha": sha, "tree": commit["tree"]["sha"]}


def core_checkout(path: Path, source: dict[str, str]) -> None:
    expected = Path(os.environ["GITHUB_WORKSPACE"]) / "core"
    require(
        not path.is_symlink()
        and path.resolve() == expected.resolve()
        and ROOT.resolve() != path.resolve()
    )
    git = ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(path)]
    require(
        producer.command([*git, "rev-parse", "HEAD^{commit}"]).decode().strip()
        == source["sha"]
    )
    require(
        producer.command([*git, "rev-parse", "HEAD^{tree}"]).decode().strip()
        == source["tree"]
    )
    producer.command([*git, "diff", "--quiet", "HEAD", "--"])
    require(
        not producer.command(
            [*git, "ls-files", "--others", "--exclude-standard"]
        ).strip()
    )
    require(
        producer._git(
            path, "config", "--includes", "--get-regexp", r"^filter\.", check=False
        ).returncode
        == 1
    )


def core_binding(
    path: Path, source: dict[str, str], ids: tuple[int, int, int]
) -> dict[str, Any]:
    core_checkout(path, source)
    value = object_value(
        producer.decode(
            core_command(
                [
                    sys.executable,
                    "-B",
                    str(path / "scripts/production_core_runtime_binding.py"),
                    "check",
                    "--run-id",
                    str(producer.positive(ids[0])),
                    "--run-attempt",
                    str(producer.positive(ids[1])),
                    "--artifact-id",
                    str(producer.positive(ids[2])),
                ]
            ),
            producer.MAX_METADATA,
        )
    )
    require(
        value["purpose"] == "fresh-authenticated-core-runtime-binding-only"
        and value["deployment_authorized"] is False
        and value["source_sha"] == source["sha"]
    )
    require(
        value["promote_run_id"] == ids[0]
        and value["promote_run_attempt"] == ids[1]
        and value["artifact_id"] == ids[2]
    )
    require(
        type(value["image"]) is str
        and re.fullmatch(
            r"cosmaneuraprodregistry\.azurecr\.io/cosmaneura/cosmaneura@sha256:[0-9a-f]{64}",
            value["image"],
        )
    )
    panel = value["observation"]["panel_https"]
    require(
        panel["origin"] == "https://app.cosmaneura.com"
        and panel["sha256"]
        == hashlib.sha256((ROOT / "app/panel.js").read_bytes()).hexdigest()
    )
    return {
        "source_sha": source["sha"],
        "image": value["image"],
        "panel_sha256": panel["sha256"],
        "run_id": ids[0],
        "run_attempt": ids[1],
        "artifact_id": ids[2],
    }


def azure(arguments: list[str], *, timeout: int = 60) -> Any:
    require(
        any(
            arguments[: len(prefix)] == prefix
            for prefix in (
                ["account", "show"],
                ["containerapp", "list"],
                ["containerapp", "show"],
                ["containerapp", "revision", "list"],
                ["containerapp", "revision", "show"],
                ["containerapp", "replica", "list"],
                ["identity", "show"],
                ["rest", "--method", "get"],
                ["acr", "repository", "show"],
            )
        )
    )
    return producer.decode(
        producer.command(
            [
                "az",
                *arguments,
                "--subscription",
                SUBSCRIPTION,
                "--only-show-errors",
                "--output",
                "json",
            ],
            timeout=timeout,
        ),
        producer.MAX_METADATA,
    )


def validator() -> Any:
    spec = importlib.util.spec_from_file_location(
        "provider_public_profile", ROOT / "infra/scripts/validate-deployment-env.py"
    )
    if spec is None or spec.loader is None:
        raise producer.CandidateRefused()
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_environment(
    container: dict[str, Any], client_id: str, endpoint: str
) -> dict[str, str]:
    schema = validator()
    schema._https_url("AZURE_DEID_ENDPOINT", endpoint)
    public_keys = schema.PROVIDER_PUBLIC | schema.PRODUCTION_SESSION_PREP_PUBLIC
    expected_keys = (
        public_keys
        | set(SECRETS)
        | {
            "AZURE_CLIENT_ID",
            "PLATFORM_WORKLOAD_CLIENT_ID",
            "AZURE_DEID_ENDPOINT",
            "MOUNT_PROFILE",
        }
    )
    entries = object_list(container.get("env"))
    require(len(entries) == len(expected_keys))
    values: dict[str, str] = {}
    references = {}
    for entry in entries:
        entry = object_value(entry)
        name = entry.get("name")
        if (
            type(name) is not str
            or name not in expected_keys
            or name in values
            or name in references
        ):
            raise producer.CandidateRefused()
        if name in SECRETS:
            require(
                entry.get("value") is None and entry.get("secretRef") == SECRETS[name]
            )
            references[name] = SECRETS[name]
        else:
            require(entry.get("secretRef") is None and type(entry.get("value")) is str)
            values[name] = entry["value"]
    require(set(references) == set(SECRETS))
    require(
        values["AZURE_CLIENT_ID"] == values["PLATFORM_WORKLOAD_CLIENT_ID"] == client_id
        and values["AZURE_DEID_ENDPOINT"] == endpoint
    )
    require(all(values[key] == value for key, value in producer.DARK_ENV.items()))
    # Pure public validation only: source-defined op-reference placeholders are
    # NOT evidence of live credentials. Live exact versionless refs are checked
    # independently without requesting any secret value.
    public = {key: values[key] for key in public_keys}
    source_references = {
        name: f"op://Production/{schema.SECRET_ITEMS['provider'][name]}"
        for name in SECRETS
    }
    schema.validate("prod", "provider", {**public, **source_references})
    return values


def process_profile(
    template: dict[str, Any], container: dict[str, Any]
) -> dict[str, Any]:
    require(not template.get("initContainers") and not template.get("volumes"))
    require(object_value(template.get("scale"))["minReplicas"] == 1)
    resources = object_value(container.get("resources"))
    require(
        type(resources.get("cpu")) in (int, float)
        and resources["cpu"] == 0.5
        and resources.get("memory") == "1Gi"
    )
    probes = object_list(container.get("probes"))
    require(len(probes) == 3)
    require(
        {value.get("type") for value in probes} == {"Readiness", "Startup", "Liveness"}
    )
    for probe in probes:
        http = object_value(probe.get("httpGet"))
        require(
            http.get("scheme") == "HTTP"
            and http.get("path") == "/healthz"
            and type(http.get("port")) is int
            and http["port"] == 8000
            and not http.get("httpHeaders")
            and not probe.get("tcpSocket")
        )
    return {
        "cpu": 0.5,
        "memory": "1Gi",
        "min_replicas": 1,
        "probes": sorted(value["type"] for value in probes),
        "probe_transport": "HTTP",
        "probe_path": "/healthz",
        "probe_port": 8000,
    }


def snapshot() -> dict[str, Any]:
    account = azure(["account", "show"])
    require(
        account["id"] == SUBSCRIPTION
        and account["tenantId"] == TENANT
        and account["user"]["type"] == "servicePrincipal"
        and same_resource(
            account["user"]["name"], os.environ["PROVIDER_PROMOTION_CLIENT_ID"]
        )
    )
    inventory = azure(
        ["containerapp", "list", "--resource-group", GROUP, "--query", "[].name"]
    )
    require(inventory == [APP])
    app = object_value(
        azure(["containerapp", "show", "--resource-group", GROUP, "--name", APP])
    )
    require(
        app["id"] == PREFIX + "/Microsoft.App/containerApps/" + APP
        and app["name"] == APP
    )
    properties = object_value(app["properties"])
    environments = [
        properties[key]
        for key in ("environmentId", "managedEnvironmentId")
        if properties.get(key) is not None
    ]
    require(
        environments
        and all(same_resource(value, ENVIRONMENT) for value in environments)
    )
    identity = object_value(app["identity"])
    identity_ids = object_value(identity.get("userAssignedIdentities"))
    require(
        identity["type"] == "UserAssigned"
        and len(identity_ids) == 1
        and same_resource(next(iter(identity_ids)), IDENTITY)
    )
    runtime = azure(
        ["identity", "show", "--resource-group", GROUP, "--name", GROUP + "-runtime"]
    )
    require(
        same_resource(runtime["id"], IDENTITY)
        and type(runtime.get("clientId")) is str
        and GUID.fullmatch(runtime["clientId"])
    )
    endpoint = azure(
        [
            "rest",
            "--method",
            "get",
            "--url",
            "https://management.azure.com" + DEID_ID + "?api-version=2024-09-20",
        ]
    )["properties"]["serviceUrl"]
    require(type(endpoint) is str)
    configuration = object_value(properties["configuration"])
    require(
        configuration["activeRevisionsMode"] == "Single"
        and properties["provisioningState"] == "Succeeded"
    )
    registries = object_list(configuration["registries"])
    require(
        type(registries) is list
        and len(registries) == 1
        and registries[0]["server"] == producer.REGISTRY + ".azurecr.io"
        and same_resource(registries[0].get("identity"), IDENTITY)
        and not registries[0].get("passwordSecretRef")
    )
    secret_entries = object_list(configuration["secrets"])
    require(
        type(secret_entries) is list
        and len(secret_entries) == len(SECRETS)
        and {item.get("name") for item in secret_entries} == set(SECRETS.values())
    )
    for secret in secret_entries:
        require(
            secret.get("value") is None
            and secret.get("keyVaultUrl")
            == f"https://{VAULT}.vault.azure.net/secrets/{secret['name']}"
            and same_resource(secret.get("identity"), IDENTITY)
        )
    ingress = object_value(configuration["ingress"])
    require(
        ingress["external"] is True
        and ingress["targetPort"] == 8000
        and ingress.get("allowInsecure") is False
    )
    revision = properties["latestRevisionName"]
    require(type(revision) is str and properties["latestReadyRevisionName"] == revision)
    routes = object_list(ingress["traffic"])
    require(
        type(routes) is list
        and len(routes) == 1
        and type(routes[0].get("weight")) is int
        and routes[0]["weight"] == 100
        and not routes[0].get("label")
    )
    require(
        (
            routes[0].get("latestRevision") is True
            and routes[0].get("revisionName") in (None, "", revision)
        )
        or (
            routes[0].get("latestRevision") in (None, False)
            and routes[0].get("revisionName") == revision
        )
    )
    revisions = azure(
        [
            "containerapp",
            "revision",
            "list",
            "--resource-group",
            GROUP,
            "--name",
            APP,
            "--query",
            "[].{name:name,active:properties.active}",
        ]
    )
    revisions = object_list(revisions)
    require(
        type(revisions) is list
        and 0 < len(revisions) <= 100
        and len({item["name"] for item in revisions}) == len(revisions)
        and all(type(item.get("active")) is bool for item in revisions)
    )
    require([item["name"] for item in revisions if item["active"]] == [revision])
    active = azure(
        [
            "containerapp",
            "revision",
            "show",
            "--resource-group",
            GROUP,
            "--name",
            APP,
            "--revision",
            revision,
        ]
    )["properties"]
    require(
        active["active"] is True
        and active["healthState"] == "Healthy"
        and active["provisioningState"] == "Provisioned"
        and active["runningState"] in ("Running", "RunningAtMaxScale")
    )
    template = object_value(properties["template"])
    containers = object_list(template["containers"])
    require(
        type(containers) is list
        and len(containers) == 1
        and containers[0]["name"] == CONTAINER
        and not template.get("initContainers")
        and not template.get("volumes")
    )
    container = object_value(containers[0])
    image = container["image"]
    require(
        type(image) is str
        and re.fullmatch(re.escape(producer.IMAGE) + r"@sha256:[0-9a-f]{64}", image)
    )
    require(
        container.get("command") in (None, [])
        and container.get("args")
        == re.findall(
            r"^web: ([^\r\n]+)$", (ROOT / "Procfile").read_text(), re.MULTILINE
        )
    )
    process = process_profile(template, container)
    public = runtime_environment(container, runtime["clientId"], endpoint)
    active_containers = object_list(active["template"]["containers"])
    require(
        type(active_containers) is list
        and len(active_containers) == 1
        and active_containers[0]["name"] == CONTAINER
        and active_containers[0]["image"] == image
        and active_containers[0].get("command") == container.get("command")
        and active_containers[0].get("args") == container.get("args")
    )
    require(
        process_profile(
            object_value(active["template"]), object_value(active_containers[0])
        )
        == process
        and runtime_environment(active_containers[0], runtime["clientId"], endpoint)
        == public
    )
    replicas = azure(
        [
            "containerapp",
            "replica",
            "list",
            "--resource-group",
            GROUP,
            "--name",
            APP,
            "--revision",
            revision,
            "--query",
            "[].properties.containers[].{name:name,ready:ready}",
        ]
    )
    require(
        type(replicas) is list
        and replicas
        and all(
            type(item) is dict
            and item.get("name") == CONTAINER
            and item.get("ready") is True
            for item in replicas
        )
    )
    # Hash ONLY the explicit public values and reference/identity facts above.
    # Raw templates/configuration and erased secret/private values are not hashed.
    projection = {
        "public_environment": public,
        "identity": IDENTITY,
        "environment": ENVIRONMENT,
        "registry": registries[0]["server"],
        "secret_references": sorted(SECRETS.items()),
        "process": process,
        "ingress_port": 8000,
    }
    return {
        "revision": revision,
        "image": image,
        "public_reference_digest": producer.digest(producer.canonical(projection)),
        "ready_replicas": len(replicas),
    }


def boundaries() -> None:
    environment = {
        "DEPLOYMENT_ENVIRONMENT": "prod",
        "SOVEREIGN_RELEASE_SCOPE": "provider-only",
        "RESOURCE_GROUP": GROUP,
        "PROVIDER_APP": APP,
        "PUBLIC_APP": "cosmaneura-web-prod-app",
        "REGISTRY_SERVER": producer.REGISTRY + ".azurecr.io",
        "DEID_SERVICE_ID": DEID_ID,
    }
    for name in ("verify-live-app-boundaries.sh", "verify-key-vault-boundaries.sh"):
        producer.command(
            ["bash", str(ROOT / "infra/scripts" / name)],
            extra={**azure_profile(), **environment},
            timeout=180,
        )


def image_manifest(image: str, config_digest: str | None = None) -> None:
    require(
        type(image) is str
        and re.fullmatch(re.escape(producer.IMAGE) + r"@sha256:[0-9a-f]{64}", image)
    )
    raw = producer.command(
        ["docker", "buildx", "imagetools", "inspect", "--raw", image]
    )
    descriptor = (
        producer.decode(raw)["config"]["digest"]
        if config_digest is None
        else config_digest
    )
    require(type(descriptor) is str and producer.DIGEST.fullmatch(descriptor))
    producer.manifest_identity(raw, image.split("@")[1], descriptor)


def prior_retention(image: str) -> None:
    value = azure(
        [
            "acr",
            "repository",
            "show",
            "--name",
            producer.REGISTRY,
            "--image",
            image.split(".azurecr.io/")[1],
        ]
    )
    require(
        value["digest"] == image.split("@")[1]
        and value["changeableAttributes"]
        == {
            "writeEnabled": False,
            "deleteEnabled": False,
            "readEnabled": True,
            "listEnabled": True,
        }
    )
    image_manifest(image)


def stable(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "ready_replicas"}


def promote(
    provider_ids: tuple[int, int, int],
    core_ids: tuple[int, int, int],
    core_path: Path,
    output: Path,
) -> None:
    current = context()
    accepted = candidate(current, *provider_ids)
    core = core_source()
    core_checkout(core_path, core)
    before = snapshot()
    boundaries()
    producer.command(
        [
            "az",
            "acr",
            "login",
            "--name",
            producer.REGISTRY,
            "--subscription",
            SUBSCRIPTION,
            "--only-show-errors",
            "--output",
            "none",
        ]
    )
    producer.retention(accepted)
    image_manifest(accepted["image"], accepted["image_config_digest"])
    prior_retention(before["image"])
    require(
        stable(snapshot()) == stable(before)
        and context() == current
        and candidate(current, *provider_ids) == accepted
        and core_source() == core
    )
    paired = core_binding(core_path, core, core_ids)
    require(context() == current)
    require(stable(snapshot()) == stable(before))
    suffix = (
        f"p{current['run_id']:x}a{current['run_attempt']:x}-{current['source_sha'][:7]}"
    )
    require(len(APP + "--" + suffix) <= 54)
    producer.command(
        [
            "az",
            "containerapp",
            "update",
            "--resource-group",
            GROUP,
            "--name",
            APP,
            "--image",
            accepted["image"],
            "--revision-suffix",
            suffix,
            "--subscription",
            SUBSCRIPTION,
            "--only-show-errors",
            "--output",
            "none",
        ]
    )
    deadline = time.monotonic() + 300
    while True:
        remaining = int(deadline - time.monotonic())
        require(remaining > 0)
        ready = azure(
            [
                "containerapp",
                "show",
                "--resource-group",
                GROUP,
                "--name",
                APP,
                "--query",
                "properties.latestReadyRevisionName",
            ],
            timeout=min(60, remaining),
        )
        if ready == APP + "--" + suffix:
            break
        require(time.monotonic() < deadline)
        time.sleep(5)
    after = snapshot()
    require(
        after["revision"] == APP + "--" + suffix
        and after["image"] == accepted["image"]
        and after["public_reference_digest"] == before["public_reference_digest"]
    )
    boundaries()
    producer.retention(accepted)
    require(
        context() == current
        and candidate(current, *provider_ids) == accepted
        and core_source() == core
    )
    require(core_binding(core_path, core, core_ids) == paired)
    producer.write_new(
        output,
        {
            "purpose": "established-dark-provider-image-update-only",
            "source_sha": current["source_sha"],
            "run_id": current["run_id"],
            "run_attempt": current["run_attempt"],
            "image": accepted["image"],
            "prior_healthy_retained_image": before["image"],
            "after": after,
            "sampled_core_binding": paired,
            "unproved": UNPROVED,
            "beta_activation_authorized": False,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("context", "admit", "core-source", "promote"))
    for name in (
        "provider-run-id",
        "provider-attempt",
        "provider-artifact-id",
        "core-run-id",
        "core-attempt",
        "core-artifact-id",
    ):
        parser.add_argument("--" + name, type=int)
    parser.add_argument("--core-checkout", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        current = context()
        if args.mode == "core-source":
            source = core_source()
            if args.core_checkout is not None:
                core_checkout(args.core_checkout, source)
            print(source["sha"])
        elif args.mode != "context":
            provider_ids = (
                producer.positive(args.provider_run_id),
                producer.positive(args.provider_attempt),
                producer.positive(args.provider_artifact_id),
            )
            if args.mode == "admit":
                print(candidate(current, *provider_ids)["image"])
            else:
                core_ids = (
                    producer.positive(args.core_run_id),
                    producer.positive(args.core_attempt),
                    producer.positive(args.core_artifact_id),
                )
                require(args.core_checkout is not None and args.output is not None)
                promote(provider_ids, core_ids, args.core_checkout, args.output)
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
        AttributeError,
    ):
        print("production_provider_promotion_refused", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
