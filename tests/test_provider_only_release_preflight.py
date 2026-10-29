"""Execute production preparation checks against synthetic metadata-only CLIs."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUBSCRIPTION = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
OPERATOR = "44444444-4444-4444-8444-444444444444"
DEID = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/cosmaneura-prod/providers/Microsoft.HealthDataAIServices/deidServices/AnonymizerAPI"

AZ_STUB = r"""import json, os, pathlib, sys
args = sys.argv[1:]
state = json.loads(pathlib.Path(os.environ["SYNTHETIC_STATE"]).read_text())
with pathlib.Path(os.environ["SYNTHETIC_CALLS"]).open("a") as f:
    f.write(json.dumps(args) + "\n")
env = state["environment"]
rg = f"cosmaneura-sovereign-{env}"
base = f"/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/{rg}"
acr = base + f"/providers/Microsoft.ContainerRegistry/registries/cosmaneurasovereign{env}registry"
provider_vault = base + f"/providers/Microsoft.KeyVault/vaults/cosmasov{env}provider"
public_vault = base + f"/providers/Microsoft.KeyVault/vaults/cosmasov{env}public"
deid = os.environ["DEID_SERVICE_ID"]
provider_principal = "11111111-1111-4111-8111-111111111111"
public_principal = "22222222-2222-4222-8222-222222222222"

def arg(name, default=""):
    return args[args.index(name)+1] if name in args else default

def emit(value):
    print(value if arg("--output", arg("-o")) == "tsv" else json.dumps(value))

def vault_id(name):
    if name == f"cosmasov{env}provider": return provider_vault
    if name == f"cosmasov{env}public": return public_vault
    raise SystemExit("unexpected vault")

if args[:2] == ["account", "show"]:
    emit("bab2a709-1fcb-4c87-a9fc-802f7477ec13" if arg("--query") == "id" else {"id":"synthetic"})
elif args[:2] == ["acr", "show"]:
    emit(acr)
elif args[:2] == ["identity", "show"]:
    name = arg("--name")
    if name not in {rg+"-runtime", f"cosmaneura-web-{env}-runtime"}:
        raise SystemExit("unexpected identity")
    emit((provider_principal if name == rg+"-runtime" else public_principal)
         if arg("--query") == "principalId" else
         base+"/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"+name)
elif args[:2] == ["keyvault", "show"]:
    name = arg("--name")
    scope = vault_id(name)
    provider = scope == provider_vault
    query = arg("--query")
    if query == "id": emit(scope)
    elif query == "tags.mount": emit("provider_assistant" if provider else "public")
    else: emit({"mount": "provider_assistant" if provider else "public", "rbac": True, "access_policies": []})
elif args[:3] == ["keyvault", "secret", "list"]:
    provider = vault_id(arg("--vault-name")) == provider_vault
    names = (state.get("provider_secrets") if provider else state.get("public_secrets"))
    if names is None:
        names = ["azure-openai-api-key", "magisterium-api-key"]
        if provider:
            names += ["sovereign-record-grant-secret"]
            if env == "staging": names += ["athena-client-id", "athena-client-secret"]
        elif state["scope"] == "provider-only": names = []
    emit([{"name": name, "enabled": True, "expires": None} for name in names])
elif args[:2] == ["rest", "--method"]:
    if "transitiveMemberOf" not in arg("--url"): raise SystemExit("unexpected Graph request")
    emit(["33333333-3333-4333-8333-333333333333"] if state.get("runtime_group_membership") else [])
elif args[:3] == ["role", "assignment", "list"]:
    if "--assignee" in args:
        role = arg("--query").split("roleDefinitionName=='", 1)[1].split("'", 1)[0]
        scope = arg("--scope")
        allowed = [("AcrPush", acr), ("Key Vault Secrets Officer", provider_vault)]
        if state["scope"] == "dual":
            allowed += [("Container Registry Tasks Contributor", acr), ("Key Vault Secrets Officer", public_vault)]
        emit(1 if (role, scope) in allowed and role != state.get("missing_operator_role") else 0)
    else:
        provider = arg("--assignee-object-id") == provider_principal
        roles = [{"role":"AcrPull", "scope":acr},
                 {"role":"Key Vault Secrets User", "scope":provider_vault if provider else public_vault}]
        if provider: roles += [{"role":"DeID Realtime Data User", "scope":deid}]
        if not provider and state.get("public_provider_vault_role"):
            roles += [{"role":"Key Vault Secrets User", "scope":provider_vault}]
        if "--scope" in args:
            roles = [item for item in roles if item["scope"] == arg("--scope")]
            if provider and state.get("inherited_runtime_owner"):
                roles += [{"role":"Owner", "scope":base}]
        emit(roles)
elif args[:2] == ["containerapp", "list"]:
    if state.get("inventory_error"): raise SystemExit("synthetic metadata unavailable")
    emit(state.get("apps", [rg+"-app"] if state["scope"] == "provider-only" else [rg+"-app", f"cosmaneura-web-{env}-app"]))
elif args[:3] == ["containerapp", "env", "show"]:
    emit(base+"/providers/Microsoft.App/managedEnvironments/"+arg("--name"))
elif args[:2] == ["containerapp", "show"]:
    name = arg("--name")
    provider = name == rg+"-app"
    if name not in {rg+"-app", f"cosmaneura-web-{env}-app"}: raise SystemExit("unexpected app")
    identity_name = rg+"-runtime" if provider else f"cosmaneura-web-{env}-runtime"
    identity_id = base+"/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"+identity_name
    environment_name = rg+"-app-environment" if provider else f"cosmaneura-web-{env}-app-environment"
    container_name = rg+"-app-web" if provider else f"cosmaneura-web-{env}-web"
    vault_name = f"cosmasov{env}provider" if provider else f"cosmasov{env}public"
    names = ["azure-openai-api-key", "magisterium-api-key"]
    if provider:
        names += ["sovereign-record-grant-secret"]
        if env == "staging": names += ["athena-client-id", "athena-client-secret"]
    if provider and state.get("cross_vault_reference"): vault_name = f"cosmasov{env}public"
    emit({"environment_id":base+"/providers/Microsoft.App/managedEnvironments/"+environment_name,
          "identity":{"type":"UserAssigned", "userAssignedIdentities":{identity_id:{}}},
          "registries":[{"server":f"cosmaneurasovereign{env}registry.azurecr.io", "identity":identity_id}],
          "secrets":[{"name":n, "key_vault_url":f"https://{vault_name}.vault.azure.net/secrets/{n}", "identity":identity_id} for n in names],
          "containers":[{"name":container_name, "env":[{"name":n.upper().replace("-","_"), "secret_ref":n} for n in names]}]})
else:
    raise SystemExit("unexpected Azure command: " + repr(args))
"""


def setup(
    tmp_path: Path,
    *,
    environment: str = "prod",
    scope: str = "provider-only",
    **state_values: object,
) -> tuple[Path, dict[str, str], Path]:
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "infra" / "scripts", repo / "infra" / "scripts")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, source in {
        "az": AZ_STUB,
        "op": "import sys; assert sys.argv[1:] == ['whoami']",
        "tofu": "import os,sys; a=sys.argv[1:]; assert not any(x in a for x in ['apply','destroy','import']); print(os.environ['DEID_SERVICE_ID'] if 'output' in a else '')",
        "git": "import sys; a=sys.argv[1:]; raise SystemExit(1 if 'config' in a else 0)",
    }.items():
        path = bin_dir / name
        path.write_text(f"#!{sys.executable}\n" + source + "\n")
        path.chmod(0o700)
    for name in ("uv", "curl"):
        path = bin_dir / name
        path.write_text("#!/bin/sh\nexit 99\n")
        path.chmod(0o700)
    (bin_dir / "python3").symlink_to(sys.executable)
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({"environment": environment, "scope": scope, **state_values})
    )
    calls = tmp_path / "calls.jsonl"
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "DEPLOYMENT_ENVIRONMENT": environment,
        "SOVEREIGN_RELEASE_SCOPE": scope,
        "DEID_SERVICE_ID": DEID,
        "SYNTHETIC_STATE": str(state),
        "SYNTHETIC_CALLS": str(calls),
        "BOOTSTRAP_OPERATOR_OBJECT_ID": OPERATOR,
        "RESOURCE_GROUP": f"cosmaneura-sovereign-{environment}",
        "PROVIDER_APP": f"cosmaneura-sovereign-{environment}-app",
        "PUBLIC_APP": f"cosmaneura-web-{environment}-app",
    }
    backend = repo / "infra" / ".terraform"
    backend.mkdir()
    (backend / "terraform.tfstate").write_text(
        json.dumps(
            {
                "backend": {
                    "type": "azurerm",
                    "config": {"key": f"sovereign-{environment}.tfstate"},
                }
            }
        )
    )
    source = (
        (ROOT / ".env.production.backend.example")
        .read_text()
        .replace("tenant_replace_me", "tenant_synthetic_production")
        .replace("your-instance.clerk.accounts.dev", "synthetic.clerk.accounts.dev")
    )
    (repo / ".env.production.backend").write_text(source)
    inputs = (
        (ROOT / "infra" / "deployment.auto.tfvars.example")
        .read_text()
        .replace("github_deploy_enabled   = true", "github_deploy_enabled   = false")
    )
    (repo / "infra" / "deployment.auto.tfvars").write_text(inputs)
    return repo, env, calls


def run(
    repo: Path, env: dict[str, str], script: str, *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(repo / "infra" / "scripts" / script), *args],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def calls(path: Path) -> list[list[str]]:
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def enable_provider(repo: Path) -> None:
    path = repo / "infra" / "deployment.auto.tfvars"
    source = (
        path.read_text()
        .replace(
            "production_provider_app_enabled = false",
            "production_provider_app_enabled = true",
        )
        .replace(
            "production_provider_key_vault_secrets_ready = false",
            "production_provider_key_vault_secrets_ready = true",
        )
        .replace(
            'initial_image_ref        = ""',
            'initial_image_ref        = "cosmaneurasovereignprodregistry.azurecr.io/cosmaneura/sovereign@sha256:'
            + "a" * 64
            + '"',
        )
    )
    path.write_text(source)


def test_provider_only_vault_check_proves_exact_provider_secrets_empty_public_and_complete_roles(
    tmp_path: Path,
) -> None:
    repo, env, log = setup(tmp_path)
    result = run(repo, env, "verify-key-vault-boundaries.sh")
    assert result.returncode == 0, result.stderr
    recorded = calls(log)
    assert sum(item[:3] == ["keyvault", "secret", "list"] for item in recorded) == 2
    assert sum(item[:3] == ["role", "assignment", "list"] for item in recorded) == 10
    assert not any(item[:3] == ["keyvault", "secret", "show"] for item in recorded)


@pytest.mark.parametrize(
    "state",
    [
        {"public_secrets": ["azure-openai-api-key", "magisterium-api-key"]},
        {
            "provider_secrets": [
                "azure-openai-api-key",
                "magisterium-api-key",
                "sovereign-record-grant-secret",
                "athena-client-secret",
            ]
        },
        {"provider_secrets": ["azure-openai-api-key", "magisterium-api-key"]},
    ],
)
def test_provider_only_vault_check_rejects_public_readiness_or_wrong_provider_inventory(
    tmp_path: Path, state: dict[str, object]
) -> None:
    repo, env, _ = setup(tmp_path, **state)
    result = run(repo, env, "verify-key-vault-boundaries.sh")
    assert result.returncode != 0
    assert "exact mount allowlist" in result.stderr


@pytest.mark.parametrize("environment", ["prod", "staging"])
def test_default_dual_vault_inventories_are_unchanged(
    tmp_path: Path, environment: str
) -> None:
    repo, env, _ = setup(tmp_path, environment=environment, scope="dual")
    env.pop("SOVEREIGN_RELEASE_SCOPE")
    result = run(repo, env, "verify-key-vault-boundaries.sh")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "script",
    [
        "verify-key-vault-boundaries.sh",
        "verify-bootstrap-role-assignments.sh",
        "verify-live-app-boundaries.sh",
    ],
)
@pytest.mark.parametrize(
    "environment,scope",
    [("staging", "provider-only"), ("prod", "provider"), ("prod", "true")],
)
def test_invalid_scope_is_refused_before_any_azure_call(
    tmp_path: Path, script: str, environment: str, scope: str
) -> None:
    repo, env, log = setup(tmp_path, environment=environment, scope=scope)
    result = run(repo, env, script)
    assert result.returncode == 2
    assert calls(log) == []


def test_provider_only_bootstrap_needs_only_scoped_push_and_provider_officer(
    tmp_path: Path,
) -> None:
    repo, env, log = setup(tmp_path)
    result = run(repo, env, "verify-bootstrap-role-assignments.sh")
    assert result.returncode == 0, result.stderr
    operator_calls = [item for item in calls(log) if "--assignee" in item]
    assert len(operator_calls) == 2
    assert not any(
        "Tasks" in " ".join(item) or "cosmasovprodpublic" in " ".join(item)
        for item in operator_calls
    )


@pytest.mark.parametrize(
    "state",
    [
        {"runtime_group_membership": True},
        {"public_provider_vault_role": True},
        {"inherited_runtime_owner": True},
    ],
)
def test_provider_only_checks_preserve_group_direct_and_inherited_role_refusal(
    tmp_path: Path, state: dict[str, object]
) -> None:
    repo, env, _ = setup(tmp_path, **state)
    result = run(repo, env, "verify-key-vault-boundaries.sh")
    assert result.returncode != 0
    assert "role" in result.stderr


@pytest.mark.parametrize("role", ["AcrPush", "Key Vault Secrets Officer"])
def test_provider_only_bootstrap_still_refuses_missing_required_operator_role(
    tmp_path: Path, role: str
) -> None:
    repo, env, _ = setup(tmp_path, missing_operator_role=role)
    result = run(repo, env, "verify-bootstrap-role-assignments.sh")
    assert result.returncode != 0
    assert "missing required scoped role" in result.stderr


def test_default_dual_bootstrap_still_requires_all_four_roles(tmp_path: Path) -> None:
    repo, env, log = setup(tmp_path, scope="dual")
    env.pop("SOVEREIGN_RELEASE_SCOPE")
    result = run(repo, env, "verify-bootstrap-role-assignments.sh")
    assert result.returncode == 0, result.stderr
    assert sum("--assignee" in item for item in calls(log)) == 4


def test_provider_only_live_check_validates_provider_binds_without_reading_public_app(
    tmp_path: Path,
) -> None:
    repo, env, log = setup(tmp_path)
    result = run(repo, env, "verify-live-app-boundaries.sh")
    assert result.returncode == 0, result.stderr
    assert not any(
        item[:2] == ["containerapp", "show"] and "cosmaneura-web-prod-app" in item
        for item in calls(log)
    )


@pytest.mark.parametrize(
    "state",
    [
        {"apps": ["cosmaneura-sovereign-prod-app", "cosmaneura-web-prod-app"]},
        {"apps": ["cosmaneura-sovereign-prod-app", "unknown-app"]},
        {"apps": ["cosmaneura-sovereign-prod-app", "cosmaneura-sovereign-prod-app"]},
        {"apps": []},
        {"inventory_error": True},
        {"cross_vault_reference": True},
    ],
)
def test_provider_only_live_check_fails_closed_for_public_unknown_missing_or_cross_bound_apps(
    tmp_path: Path, state: dict[str, object]
) -> None:
    repo, env, _ = setup(tmp_path, **state)
    result = run(repo, env, "verify-live-app-boundaries.sh")
    assert result.returncode != 0


def test_provider_only_preflight_accepts_dark_provider_creation_without_public_env(
    tmp_path: Path,
) -> None:
    repo, env, log = setup(tmp_path)
    enable_provider(repo)
    assert not (repo / ".env.production.public").exists()
    result = run(repo, env, "preflight.sh", "prod", "provider-only")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "public env is excluded" in result.stdout
    assert (
        "provider app binds are mount-exact and public app is absent" in result.stdout
    )
    assert not any(
        item[:2] == ["containerapp", "show"] and "cosmaneura-web-prod-app" in item
        for item in calls(log)
    )


def test_provider_only_preflight_refuses_dual_readiness_controls(
    tmp_path: Path,
) -> None:
    repo, env, _ = setup(tmp_path)
    path = repo / "infra" / "deployment.auto.tfvars"
    source = (
        path.read_text()
        .replace("live_apps_enabled       = false", "live_apps_enabled       = true")
        .replace(
            "\nkey_vault_secrets_ready = false\n", "\nkey_vault_secrets_ready = true\n"
        )
        .replace(
            'initial_image_ref        = ""',
            'initial_image_ref        = "cosmaneurasovereignprodregistry.azurecr.io/cosmaneura/sovereign@sha256:'
            + "a" * 64
            + '"',
        )
    )
    path.write_text(source)
    result = run(repo, env, "preflight.sh", "prod", "provider-only")
    assert result.returncode == 1
    assert "release scope conflicts" in result.stdout


def test_default_preflight_does_not_skip_the_public_env(tmp_path: Path) -> None:
    repo, env, _ = setup(tmp_path, scope="dual")
    env.pop("SOVEREIGN_RELEASE_SCOPE")
    result = run(repo, env, "preflight.sh", "prod")
    assert result.returncode == 1
    assert ".env.production.public is missing or unsafe" in result.stdout


def test_provider_only_preflight_refuses_metadata_failure_and_public_secret_readiness(
    tmp_path: Path,
) -> None:
    repo, env, _ = setup(tmp_path, public_secrets=["azure-openai-api-key"])
    enable_provider(repo)
    result = run(repo, env, "preflight.sh", "prod", "provider-only")
    assert result.returncode == 1
    assert (
        "vault inventory or runtime role boundary verification failed" in result.stdout
    )


def test_provider_only_scope_cannot_use_legacy_paired_mutator(tmp_path: Path) -> None:
    repo, env, log = setup(tmp_path)
    env.update(
        IMAGE_REF="synthetic",
        REVISION_SUFFIX="synthetic",
        SOVEREIGN_PLATFORM_URL="https://app.cosmaneura.com",
        SOVEREIGN_PROVIDER_TOKEN="synthetic-provider-token",
        SOVEREIGN_NON_PROVIDER_TOKEN="synthetic-non-provider-token",
    )
    result = run(repo, env, "deploy-dual.sh")
    assert result.returncode == 2
    assert "deploy-dual cannot perform a provider-only release" in result.stderr
    assert calls(log) == []


def test_provider_only_scope_cannot_use_remote_acr_bootstrap(tmp_path: Path) -> None:
    repo, env, log = setup(tmp_path)
    env["REGISTRY"] = "cosmaneurasovereignprodregistry.azurecr.io"
    result = run(repo, env, "build-bootstrap-image.sh")
    assert result.returncode == 2, result.stderr
    assert "remote bootstrap is not permitted" in result.stderr
    assert calls(log) == []
