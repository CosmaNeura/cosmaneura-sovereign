"""Dark image-only consumer contracts; external execution is always mocked."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools import production_provider_promotion as promotion

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "producer_fixture", ROOT / "tests/test_production_provider_candidate.py"
)
assert SPEC and SPEC.loader
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
CLIENT = "11111111-1111-4111-8111-111111111111"
RUNTIME_CLIENT = "22222222-2222-4222-8222-222222222222"
ENDPOINT = "https://anonymizerapi.api.cognitive.microsoft.com"
CORE = {"sha": "3" * 40, "tree": "4" * 40}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refused(*_args, **_kwargs):
        raise AssertionError("external execution forbidden")

    for name in ("socket", "create_connection"):
        monkeypatch.setattr(socket, name, refused)
    for name in ("run", "Popen", "check_output", "check_call"):
        monkeypatch.setattr(subprocess, name, refused)


def profile():
    schema = promotion.validator()
    values = {
        key: "synthetic"
        for key in schema.PROVIDER_PUBLIC | schema.PRODUCTION_SESSION_PREP_PUBLIC
    }
    values.update(schema.FIXED["provider"])
    values.update(schema.PRODUCTION_PROVIDER_AUTHORITY)
    values.update(promotion.producer.DARK_ENV)
    values.update(
        {
            key: "https://service.synthetic.test"
            for key in schema.URL_KEYS - {"PLATFORM_AGENT_API_BASE_URL"}
        }
    )
    values.update(
        {
            "OIDC_JWKS_URL": "https://service.synthetic.test/.well-known/jwks.json",
            "OIDC_ALGORITHMS": '["RS256"]',
            "MOUNT_TENANT_ID": "tenant_synthetic",
            "AZURE_CLIENT_ID": RUNTIME_CLIENT,
            "PLATFORM_WORKLOAD_CLIENT_ID": RUNTIME_CLIENT,
            "AZURE_DEID_ENDPOINT": ENDPOINT,
        }
    )
    return values


def env_container():
    entries = [
        {"name": key, "value": value} for key, value in sorted(profile().items())
    ]
    entries += [
        {"name": key, "secretRef": value} for key, value in promotion.SECRETS.items()
    ]
    return {
        "name": promotion.CONTAINER,
        "image": FIXTURE.record()["image"],
        "resources": {"cpu": 0.5, "memory": "1Gi"},
        "probes": [
            {
                "type": name,
                "httpGet": {"scheme": "HTTP", "path": "/healthz", "port": 8000},
            }
            for name in ("Readiness", "Startup", "Liveness")
        ],
        "command": None,
        "args": ["uvicorn app.main:app --host 0.0.0.0 --port 8000"],
        "env": entries,
    }


def foundation():
    revision = promotion.APP + "--prior"
    container = env_container()
    configuration = {
        "activeRevisionsMode": "Single",
        "registries": [
            {
                "server": promotion.producer.REGISTRY + ".azurecr.io",
                "identity": promotion.IDENTITY,
            }
        ],
        "secrets": [
            {
                "name": value,
                "identity": promotion.IDENTITY,
                "keyVaultUrl": f"https://{promotion.VAULT}.vault.azure.net/secrets/{value}",
            }
            for value in promotion.SECRETS.values()
        ],
        "ingress": {
            "external": True,
            "targetPort": 8000,
            "allowInsecure": False,
            "traffic": [{"latestRevision": True, "weight": 100}],
        },
    }
    app = {
        "id": promotion.PREFIX + "/Microsoft.App/containerApps/" + promotion.APP,
        "name": promotion.APP,
        "identity": {
            "type": "UserAssigned",
            "userAssignedIdentities": {promotion.IDENTITY: {}},
        },
        "properties": {
            "environmentId": promotion.ENVIRONMENT,
            "managedEnvironmentId": promotion.ENVIRONMENT,
            "configuration": configuration,
            "provisioningState": "Succeeded",
            "latestRevisionName": revision,
            "latestReadyRevisionName": revision,
            "template": {"scale": {"minReplicas": 1}, "containers": [container]},
        },
    }
    return {
        "account": {
            "id": promotion.SUBSCRIPTION,
            "tenantId": promotion.TENANT,
            "user": {"type": "servicePrincipal", "name": CLIENT},
        },
        "inventory": [promotion.APP],
        "app": app,
        "identity": {"id": promotion.IDENTITY, "clientId": RUNTIME_CLIENT},
        "deid": {"properties": {"serviceUrl": ENDPOINT}},
        "revisions": [
            {"name": revision, "active": True},
            {"name": promotion.APP + "--older", "active": False},
        ],
        "active": {
            "properties": {
                "active": True,
                "healthState": "Healthy",
                "provisioningState": "Provisioned",
                "runningState": "Running",
                "template": {
                    "scale": {"minReplicas": 1},
                    "containers": [copy.deepcopy(container)],
                },
            }
        },
        "replicas": [{"name": promotion.CONTAINER, "ready": True}],
    }


def install_foundation(monkeypatch, value):
    monkeypatch.setenv("PROVIDER_PROMOTION_CLIENT_ID", CLIENT)

    def azure(args, **_kwargs):
        key = {
            ("account", "show"): "account",
            ("containerapp", "list"): "inventory",
            ("containerapp", "show"): "app",
            ("identity", "show"): "identity",
            ("rest", "--method"): "deid",
            ("containerapp", "revision", "list"): "revisions",
            ("containerapp", "revision", "show"): "active",
            ("containerapp", "replica", "list"): "replicas",
        }.get(tuple(args[:3]), None)
        key = (
            key
            or {
                ("account", "show"): "account",
                ("containerapp", "list"): "inventory",
                ("containerapp", "show"): "app",
                ("identity", "show"): "identity",
                ("rest", "--method"): "deid",
            }[tuple(args[:2])]
        )
        return copy.deepcopy(value[key])

    monkeypatch.setattr(promotion, "azure", azure)


def test_actual_tf_profile_and_exact_runtime_refs(monkeypatch):
    value = foundation()
    install_foundation(monkeypatch, value)
    result = promotion.snapshot()
    assert (
        result["image"] == FIXTURE.record()["image"] and result["ready_replicas"] == 1
    )
    assert "MOUNT_PROFILE" in profile()
    tf = (ROOT / "infra/main.tf").read_text()
    assert 'name  = "MOUNT_PROFILE"' in tf and 'value = "provider_assistant"' in tf
    assert len(result["public_reference_digest"]) == 71
    assert not any(key in json.dumps(result) for key in promotion.SECRETS)


@pytest.mark.parametrize(
    "key,value",
    [
        ("MOUNT_PROFILE", "patient_companion"),
        ("FEATURE_SESSION_PREP", "true"),
        ("SESSION_PREP_CONTEXT_SOURCE", "platform"),
        ("PLATFORM_AGENT_API_ENABLED", "false"),
        ("AUTH_MODE", "oidc"),
        ("PLATFORM_AGENT_API_BASE_URL", "https://staging-app.cosmaneura.com"),
        ("PLATFORM_AGENT_API_SCOPE", "api://wrong/.default"),
        ("FEATURE_ATHENA_LABS", "true"),
        ("CORS_ALLOW_ORIGINS", '["https://elsewhere.synthetic.test"]'),
        ("AZURE_CLIENT_ID", CLIENT),
        ("PLATFORM_WORKLOAD_CLIENT_ID", CLIENT),
        ("AZURE_DEID_ENDPOINT", "https://wrong.synthetic.test"),
        ("OIDC_ALGORITHMS", '["HS256"]'),
        ("MOUNT_TENANT_ID", "<private>"),
    ],
)
def test_actual_profile_overrides_refuse(key, value):
    container = env_container()
    next(entry for entry in container["env"] if entry["name"] == key)["value"] = value
    with pytest.raises(ValueError):
        promotion.runtime_environment(container, RUNTIME_CLIENT, ENDPOINT)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "duplicate",
        "literal",
        "wrong_ref",
        "malformed",
        "public_ref",
        "secret_and_value",
    ],
)
def test_runtime_environment_is_complete_unambiguous_and_reference_only(mutation):
    container = env_container()
    if mutation == "missing":
        container["env"].pop()
    elif mutation == "extra":
        container["env"].append({"name": "HIDDEN_WORKFLOW_OVERRIDE", "value": "true"})
    elif mutation == "duplicate":
        container["env"][0] = copy.deepcopy(container["env"][1])
    elif mutation == "malformed":
        container["env"][0] = "private-sentinel"
    else:
        entry = next(
            item
            for item in container["env"]
            if item["name"]
            == ("MOUNT_PROFILE" if mutation == "public_ref" else "AZURE_OPENAI_API_KEY")
        )
        if mutation in ("literal", "secret_and_value"):
            entry["value"] = "private-sentinel"
        if mutation == "literal":
            entry.pop("secretRef")
        if mutation == "wrong_ref":
            entry["secretRef"] = "other-key"
        if mutation == "public_ref":
            entry["secretRef"] = "other-key"
    with pytest.raises(ValueError):
        promotion.runtime_environment(container, RUNTIME_CLIENT, ENDPOINT)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://service.synthetic.test",
        "https://private:sentinel@service.synthetic.test",
        "https://service.synthetic.test?private=sentinel",
        "https://service.synthetic.test#private",
    ],
)
def test_deid_endpoint_is_public_projection_safe(endpoint):
    with pytest.raises(ValueError):
        promotion.runtime_environment(env_container(), RUNTIME_CLIENT, endpoint)


def change(value, path, replacement):
    target = value
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    if isinstance(target, list):
        target[int(parts[-1])] = replacement
    else:
        target[parts[-1]] = replacement


@pytest.mark.parametrize(
    "path,replacement",
    [
        ("account.id", "wrong"),
        ("account.tenantId", "wrong"),
        ("account.user.type", "user"),
        ("account.user.name", RUNTIME_CLIENT),
        ("inventory", []),
        ("inventory", [promotion.APP, "public-app"]),
        ("app.properties.environmentId", "wrong"),
        ("app.properties.managedEnvironmentId", "wrong"),
        ("app.identity.type", "SystemAssigned, UserAssigned"),
        ("app.identity.userAssignedIdentities", {promotion.IDENTITY: {}, "other": {}}),
        ("app.properties.configuration.activeRevisionsMode", "Multiple"),
        ("app.properties.provisioningState", "Failed"),
        ("app.properties.latestReadyRevisionName", "older"),
        ("app.properties.configuration.ingress.allowInsecure", True),
        ("app.properties.configuration.ingress.external", False),
        ("app.properties.configuration.ingress.targetPort", 80),
        ("app.properties.configuration.ingress.traffic.0.weight", True),
        ("app.properties.configuration.ingress.traffic.0.label", "alias"),
        ("app.properties.configuration.registries.0.identity", "wrong"),
        ("app.properties.configuration.registries.0.passwordSecretRef", "private"),
        ("app.properties.configuration.secrets.0.value", "private-sentinel"),
        (
            "app.properties.configuration.secrets.0.keyVaultUrl",
            "https://cosmasovprodprovider.vault.azure.net/secrets/magisterium-api-key/version",
        ),
        ("app.properties.configuration.secrets.0.identity", "wrong"),
        ("revisions.1.active", True),
        ("revisions.0.active", "true"),
        ("active.properties.healthState", "Unhealthy"),
        ("active.properties.runningState", "Stopped"),
        ("active.properties.template.containers.0.image", "wrong:tag"),
        ("replicas", []),
        ("replicas.0.ready", False),
        (
            "app.properties.template.containers.0.image",
            promotion.producer.IMAGE + ":mutable",
        ),
        ("app.properties.template.volumes", [{"name": "hidden"}]),
        ("app.properties.template.containers.0.args", ["other-process"]),
        ("app.properties.template.containers.0.resources.cpu", True),
        ("app.properties.template.containers.0.resources.memory", "2Gi"),
        ("app.properties.template.scale.minReplicas", 0),
        ("app.properties.template.containers.0.probes.0.httpGet.port", 80),
        (
            "app.properties.template.containers.0.probes.0.httpGet.httpHeaders",
            [{"name": "Authorization", "value": "private-sentinel"}],
        ),
        ("active.properties.template.initContainers", [{"name": "hidden"}]),
        ("active.properties.template.volumes", [{"name": "hidden"}]),
    ],
)
def test_missing_or_drifted_foundation_refuses(monkeypatch, path, replacement):
    value = foundation()
    change(value, path, replacement)
    install_foundation(monkeypatch, value)
    with pytest.raises((ValueError, KeyError, TypeError)):
        promotion.snapshot()


def test_healthy_scale_allowed_but_stable_config_drift_is_not(monkeypatch):
    value = foundation()
    install_foundation(monkeypatch, value)
    before = promotion.snapshot()
    value["replicas"].append(copy.deepcopy(value["replicas"][0]))
    after = promotion.snapshot()
    assert after["ready_replicas"] == 2 and promotion.stable(after) == promotion.stable(
        before
    )
    after["public_reference_digest"] = "changed"
    assert promotion.stable(after) != promotion.stable(before)


def own_context(monkeypatch):
    env = {
        "GITHUB_ACTOR": "JohnClem",
        "GITHUB_TRIGGERING_ACTOR": "JohnClem",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": promotion.producer.REPOSITORY,
        "GITHUB_WORKFLOW_REF": promotion.WORKFLOW_REF,
        "GITHUB_SHA": FIXTURE.SHA,
        "GITHUB_RUN_ID": "20",
        "GITHUB_RUN_ATTEMPT": "1",
        "PROVIDER_IMMUTABLE_PROMOTION_ENABLED": "true",
        "PROVIDER_PROMOTION_CLIENT_ID": CLIENT,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        promotion.producer,
        "source_identity",
        lambda sha: {
            "tree": FIXTURE.TREE,
            "archive_sha256": promotion.producer.digest(b"synthetic"),
        },
    )
    run = {
        **FIXTURE.evidence()[0],
        "id": 20,
        "path": promotion.WORKFLOW,
        "status": "in_progress",
        "conclusion": None,
    }

    def api(path):
        return (
            {"object": {"sha": FIXTURE.SHA}}
            if path.endswith("git/ref/heads/main")
            else copy.deepcopy(run)
        )

    monkeypatch.setattr(promotion.producer, "api", api)
    return run


def test_own_context_is_authenticated_without_producer_spoof(monkeypatch):
    own_context(monkeypatch)
    assert promotion.context()["run_id"] == 20
    assert promotion.WORKFLOW_REF != promotion.producer.WORKFLOW_REF


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_ACTOR", "other"),
        ("GITHUB_TRIGGERING_ACTOR", "other"),
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_REF", "refs/heads/other"),
        ("GITHUB_REPOSITORY", "other/repo"),
        ("GITHUB_WORKFLOW_REF", promotion.producer.WORKFLOW_REF),
        ("GITHUB_SHA", "malformed"),
        ("PROVIDER_IMMUTABLE_PROMOTION_ENABLED", "false"),
        ("PROVIDER_PROMOTION_CLIENT_ID", ""),
        ("LEGACY_DEPLOY_CLIENT_ID", CLIENT),
        ("ARTIFACT_PREPARATION_CLIENT_ID", CLIENT),
    ],
)
def test_own_admission_refuses_legacy_context_and_missing_capabilities(
    monkeypatch, key, value
):
    own_context(monkeypatch)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        promotion.context()


@pytest.mark.parametrize(
    "path,value",
    [
        ("id", 21),
        ("run_attempt", 2),
        ("status", "completed"),
        ("conclusion", "success"),
        ("path", promotion.producer.WORKFLOW),
        ("head_sha", "0" * 40),
        ("triggering_actor.login", "other"),
    ],
)
def test_own_authenticated_run_metadata_is_required(monkeypatch, path, value):
    run = own_context(monkeypatch)
    change(run, path, value)
    with pytest.raises(ValueError):
        promotion.context()


def test_candidate_reuses_original_authenticated_verifier(monkeypatch):
    run, workflow, jobs, artifact, archive = FIXTURE.evidence()
    replies = [run, workflow, jobs, artifact]
    monkeypatch.setattr(promotion.producer, "api", lambda _path: replies.pop(0))
    monkeypatch.setattr(
        promotion.producer, "command", lambda *_args, **_kwargs: archive
    )
    assert promotion.candidate(FIXTURE.context(), 10, 1, 100) == FIXTURE.record()


def test_core_token_and_existing_profile_are_scoped_to_trusted_read_child(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GH_TOKEN", "provider-token-sentinel")
    monkeypatch.setenv("CORE_ARTIFACT_READ_TOKEN", "core-token-sentinel")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("AZURE_CONFIG_DIR", "existing-azure-profile")
    monkeypatch.setenv("AZURE_EXTENSION_DIR", "existing-extensions")
    calls = []
    monkeypatch.setattr(
        promotion.producer,
        "command",
        lambda args, **kwargs: calls.append((args, kwargs)) or b"{}",
    )
    promotion.core_api(f"repos/{promotion.CORE_REPOSITORY}/pulls/902")
    promotion.core_command(
        [
            sys.executable,
            "-B",
            str(tmp_path / "core/scripts/production_core_runtime_binding.py"),
            "check",
            "--run-id",
            "10",
            "--run-attempt",
            "1",
            "--artifact-id",
            "100",
        ]
    )
    assert (
        calls[0][1]["extra"] == {"GH_TOKEN": "core-token-sentinel"}
        and calls[0][1]["timeout"] == 60
    )
    assert (
        calls[1][1]["extra"]
        == {
            "GH_TOKEN": "core-token-sentinel",
            "AZURE_CONFIG_DIR": "existing-azure-profile",
            "AZURE_EXTENSION_DIR": "existing-extensions",
        }
        and calls[1][1]["timeout"] == 1800
    )
    assert all("CORE_ARTIFACT_READ_TOKEN" not in kwargs["extra"] for _, kwargs in calls)
    with pytest.raises(ValueError):
        promotion.core_command(["az", "containerapp", "update"])
    with pytest.raises(ValueError):
        promotion.core_api("repos/other/repo/actions/runs/10")
    monkeypatch.delenv("CORE_ARTIFACT_READ_TOKEN")
    with pytest.raises(ValueError):
        promotion.core_api(f"repos/{promotion.CORE_REPOSITORY}/pulls/902")


def core_metadata(monkeypatch):
    pull = {
        "merged": True,
        "base": {"ref": "master", "repo": {"full_name": promotion.CORE_REPOSITORY}},
        "head": {"sha": promotion.CORE_REVIEWED_HEAD},
        "merge_commit_sha": "5" * 40,
    }
    values = {
        "pulls/902": pull,
        "git/ref/heads/master": {"object": {"sha": CORE["sha"]}},
        "git/commits/" + CORE["sha"]: {
            "sha": CORE["sha"],
            "tree": {"sha": CORE["tree"]},
        },
        "compare/" + "5" * 40 + "..." + CORE["sha"]: {
            "status": "ahead",
            "base_commit": {"sha": "5" * 40},
        },
    }
    monkeypatch.setattr(
        promotion,
        "core_api",
        lambda path: copy.deepcopy(
            values[path.split(promotion.CORE_REPOSITORY + "/")[1]]
        ),
    )
    return values


def test_exact_reviewed_merged_core_current_lineage(monkeypatch):
    core_metadata(monkeypatch)
    assert promotion.core_source() == CORE


@pytest.mark.parametrize(
    "path,value",
    [
        ("merged", False),
        ("base.ref", "codex/production-immutable-core-release"),
        ("base.repo.full_name", "other/repo"),
        ("head.sha", "0" * 40),
        ("merge_commit_sha", "malformed"),
    ],
)
def test_missing_or_different_core_foundation_refuses(monkeypatch, path, value):
    values = core_metadata(monkeypatch)
    change(values["pulls/902"], path, value)
    with pytest.raises(ValueError):
        promotion.core_source()


def paired():
    return {
        "source_sha": CORE["sha"],
        "image": "cosmaneuraprodregistry.azurecr.io/cosmaneura/cosmaneura@sha256:"
        + "6" * 64,
        "panel_sha256": hashlib.sha256(
            (ROOT / "app/panel.js").read_bytes()
        ).hexdigest(),
        "run_id": 30,
        "run_attempt": 1,
        "artifact_id": 300,
    }


def test_core_actual_facts_are_pinned_without_beta_claim(monkeypatch, tmp_path):
    monkeypatch.setattr(promotion, "core_checkout", lambda *_args: None)
    value = {
        "purpose": "fresh-authenticated-core-runtime-binding-only",
        "deployment_authorized": False,
        "source_sha": CORE["sha"],
        "image": paired()["image"],
        "promote_run_id": 30,
        "promote_run_attempt": 1,
        "artifact_id": 300,
        "observation": {
            "panel_https": {
                "origin": "https://app.cosmaneura.com",
                "sha256": paired()["panel_sha256"],
            }
        },
    }
    monkeypatch.setattr(
        promotion, "core_command", lambda _args: promotion.producer.canonical(value)
    )
    assert promotion.core_binding(tmp_path, CORE, (30, 1, 300)) == paired()
    for path, replacement in [
        ("deployment_authorized", True),
        ("source_sha", "0" * 40),
        ("image", "mutable:tag"),
        ("promote_run_id", 31),
        ("observation.panel_https.origin", "https://staging-app.cosmaneura.com"),
        ("observation.panel_https.sha256", "0" * 64),
    ]:
        prior = copy.deepcopy(value)
        change(value, path, replacement)
        with pytest.raises(ValueError):
            promotion.core_binding(tmp_path, CORE, (30, 1, 300))
        value.clear()
        value.update(prior)


def install_promotion(monkeypatch):
    current = FIXTURE.context()
    accepted = FIXTURE.record()
    before = {
        "revision": promotion.APP + "--prior",
        "image": promotion.producer.IMAGE + "@sha256:" + "9" * 64,
        "public_reference_digest": "sha256:" + "8" * 64,
        "ready_replicas": 1,
    }
    suffix = "p14a1-" + FIXTURE.SHA[:7]
    after = {
        **before,
        "revision": promotion.APP + "--" + suffix,
        "image": accepted["image"],
        "ready_replicas": 3,
    }
    calls = []
    snapshots = [
        before,
        {**before, "ready_replicas": 2},
        {**before, "ready_replicas": 2},
        after,
    ]
    monkeypatch.setattr(
        promotion, "context", lambda: calls.append("context") or current
    )
    monkeypatch.setattr(
        promotion, "candidate", lambda *_args: calls.append("candidate") or accepted
    )
    monkeypatch.setattr(
        promotion, "core_source", lambda: calls.append("core_source") or CORE
    )
    monkeypatch.setattr(promotion, "core_checkout", lambda *_args: None)
    monkeypatch.setattr(
        promotion,
        "snapshot",
        lambda: calls.append("snapshot") or copy.deepcopy(snapshots.pop(0)),
    )
    monkeypatch.setattr(promotion, "boundaries", lambda: calls.append("boundaries"))
    monkeypatch.setattr(
        promotion, "image_manifest", lambda *_args: calls.append("manifest")
    )
    monkeypatch.setattr(
        promotion, "prior_retention", lambda *_args: calls.append("prior_retention")
    )
    monkeypatch.setattr(
        promotion.producer, "retention", lambda *_args: calls.append("retention")
    )
    monkeypatch.setattr(
        promotion,
        "core_binding",
        lambda *_args: calls.append("core_binding") or paired(),
    )
    monkeypatch.setattr(promotion, "azure", lambda *_args, **_kwargs: after["revision"])
    monkeypatch.setattr(
        promotion.producer, "command", lambda args, **_kwargs: calls.append(args) or b""
    )
    return calls, snapshots, after


def test_only_authenticated_digest_is_updated_with_pre_and_post_core_check(
    monkeypatch, tmp_path
):
    calls, _, _ = install_promotion(monkeypatch)
    output = tmp_path / "receipt.json"
    promotion.promote((10, 1, 100), (30, 1, 300), tmp_path, output)
    writes = [
        args
        for args in calls
        if isinstance(args, list) and args[:3] == ["az", "containerapp", "update"]
    ]
    assert (
        len(writes) == 1
        and writes[0][writes[0].index("--image") + 1] == FIXTURE.record()["image"]
    )
    position = calls.index(writes[0])
    assert (
        calls[position - 3 : position] == ["core_binding", "context", "snapshot"]
        and calls.count("core_binding") == 2
    )
    receipt = json.loads(output.read_text())
    assert receipt["sampled_core_binding"] == paired()
    assert (
        receipt["beta_activation_authorized"] is False
        and receipt["unproved"] == promotion.UNPROVED
    )
    assert receipt["prior_healthy_retained_image"] != receipt["image"]
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "phase", ["pre-foundation", "pre-core", "post-core", "post-config", "post-provider"]
)
def test_drift_never_emits_success_or_automatic_rollback(monkeypatch, tmp_path, phase):
    calls, snapshots, after = install_promotion(monkeypatch)
    if phase == "pre-foundation":
        snapshots[1]["public_reference_digest"] = "changed"
    elif phase == "post-config":
        after["public_reference_digest"] = "changed"
    elif phase in ("pre-core", "post-core"):
        count = 0

        def binding(*_args):
            nonlocal count
            count += 1
            calls.append("core_binding")
            if phase == "pre-core":
                raise ValueError("fixed refusal")
            return paired() if count == 1 else {**paired(), "image": "changed"}

        monkeypatch.setattr(promotion, "core_binding", binding)
    else:
        count = 0

        def selected(*_args):
            nonlocal count
            count += 1
            return (
                FIXTURE.record()
                if count < 3
                else {**FIXTURE.record(), "image": "changed"}
            )

        monkeypatch.setattr(promotion, "candidate", selected)
    with pytest.raises(ValueError):
        promotion.promote(
            (10, 1, 100), (30, 1, 300), tmp_path, tmp_path / "receipt.json"
        )
    assert not (tmp_path / "receipt.json").exists()
    writes = [
        item
        for item in calls
        if isinstance(item, list) and item[:3] == ["az", "containerapp", "update"]
    ]
    assert len(writes) == (0 if phase.startswith("pre-") else 1)


def test_boundary_commands_preserve_existing_profile_without_credentials_or_loader(
    monkeypatch,
):
    calls = []
    monkeypatch.setenv("AZURE_CONFIG_DIR", "existing-profile")
    monkeypatch.setenv("CORE_ARTIFACT_READ_TOKEN", "core-token-sentinel")
    monkeypatch.setattr(
        promotion.producer,
        "command",
        lambda args, **kwargs: calls.append((args, kwargs)) or b"",
    )
    promotion.boundaries()
    assert [Path(args[1]).name for args, _ in calls] == [
        "verify-live-app-boundaries.sh",
        "verify-key-vault-boundaries.sh",
    ]
    assert all(
        kwargs["extra"]["AZURE_CONFIG_DIR"] == "existing-profile"
        and kwargs["extra"]["SOVEREIGN_RELEASE_SCOPE"] == "provider-only"
        and "CORE_ARTIFACT_READ_TOKEN" not in kwargs["extra"]
        for _, kwargs in calls
    )
    assert not any("preflight" in str(args) or "op" in args for args, _ in calls)


def test_new_workflow_and_legacy_paths_are_separate_and_default_off():
    workflow = yaml.safe_load((ROOT / promotion.WORKFLOW).read_text())
    assert (
        list(workflow[True]) == ["workflow_dispatch"]
        and workflow["concurrency"]["cancel-in-progress"] is False
    )
    jobs = workflow["jobs"]
    for job in jobs.values():
        assert (
            job["environment"] == "production"
            and "SOVEREIGN_PROVIDER_IMMUTABLE_PROMOTION_ENABLED == 'true'" in job["if"]
        )
        assert "JohnClem" in job["if"] and "refs/heads/main" in job["if"]
    steps = jobs["promote"]["steps"]
    login = next(
        index
        for index, step in enumerate(steps)
        if step.get("uses", "").startswith("azure/login@")
    )
    assert any("admit" in step.get("run", "") for step in steps[:login])
    assert (
        steps[login]["with"]["client-id"]
        == "${{ vars.AZURE_PROVIDER_PROMOTION_CLIENT_ID }}"
    )
    checkout = next(
        step
        for step in steps
        if step.get("with", {}).get("repository") == promotion.CORE_REPOSITORY
    )
    assert (
        checkout["with"]["persist-credentials"] is False
        and checkout["with"]["token"] == "${{ secrets.CORE_ARTIFACT_READ_TOKEN }}"
    )
    assert "CORE_ARTIFACT_READ_TOKEN" not in jobs["promote"]["env"]
    source = (ROOT / promotion.WORKFLOW).read_text()
    assert "python3 -B -m tools.production_provider_promotion" in source
    assert not any(
        command in source
        for command in (
            "nixpacks",
            "docker build",
            "docker push",
            "tofu",
            "preflight.sh",
            "FEATURE_SESSION_PREP: true",
        )
    )


def test_fixed_diagnostics_suppress_malformed_archive_and_external_error(
    monkeypatch, capsys
):
    monkeypatch.setattr(sys, "argv", ["promotion", "context"])
    for error in (
        ValueError("private-sentinel"),
        promotion.zipfile.BadZipFile("private-sentinel"),
        subprocess.TimeoutExpired("private-sentinel", 60),
    ):
        monkeypatch.setattr(
            promotion, "context", lambda error=error: (_ for _ in ()).throw(error)
        )
        assert promotion.main() == 1
        output = capsys.readouterr()
        assert (
            output.out == "" and output.err == "production_provider_promotion_refused\n"
        )
