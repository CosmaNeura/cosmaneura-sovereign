"""Deployment integrity regressions for the two physical mounts."""
from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "infra" / "scripts" / "deploy-dual.sh"


def _make_executable(path: Path, source: str) -> None:
    path.write_text(source)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_commands(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "az.log"
    _make_executable(
        bin_dir / "az",
        f"""#!{sys.executable}
import os
import json
import pathlib
import sys

args = sys.argv[1:]
name = args[args.index("--name") + 1] if "--name" in args else ""
if any(os.environ.get(name) for name in (
    "SOVEREIGN_PROVIDER_TOKEN",
    "SOVEREIGN_NON_PROVIDER_TOKEN",
    "SOVEREIGN_SYNTHETIC_RECORD_GRANT",
)):
    raise SystemExit("short-lived smoke credential leaked to Azure CLI")

def current_deployment(app):
    key = "OLD_PROVIDER" if app == os.environ["PROVIDER_APP"] else "OLD_PUBLIC"
    image = os.environ[key]
    suffix = "old-provider" if app == os.environ["PROVIDER_APP"] else "old-public"
    log = pathlib.Path(os.environ["FAKE_LOG"])
    if log.exists():
        for line in log.read_text().splitlines():
            logged_app, logged_image, logged_suffix = line.split()
            if logged_app == app:
                image, suffix = logged_image, logged_suffix
    return image, suffix

if args[:2] == ["containerapp", "show"]:
    query = args[args.index("--query") + 1]
    if "ingress.fqdn" in query:
        print("provider.example" if name == os.environ["PROVIDER_APP"] else "public.example")
    elif "identity:identity" in query:
        provider = name == os.environ["PROVIDER_APP"]
        identity_name = (
            "cosmaneura-sovereign-prod-runtime"
            if provider else "cosmaneura-web-prod-runtime"
        )
        vault = "cosmasovprodprovider" if provider else "cosmasovprodpublic"
        identity_id = (
            f"/subscriptions/test/resourceGroups/{{os.environ['RESOURCE_GROUP']}}"
            "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
            f"{{identity_name}}"
        )
        secret_names = ["azure-openai-api-key", "magisterium-api-key"]
        if provider:
            secret_names += [
                "athena-client-id",
                "athena-client-secret",
                "sovereign-record-grant-secret",
            ]
        print(json.dumps({{
            "identity": {{
                "type": "UserAssigned",
                "userAssignedIdentities": {{identity_id: {{}}}},
            }},
            "registries": [{{
                "server": os.environ["REGISTRY_SERVER"],
                "identity": identity_id,
            }}],
            "secrets": [{{
                "name": secret,
                "key_vault_url": f"https://{{vault}}.vault.azure.net/secrets/{{secret}}",
                "identity": identity_id,
            }} for secret in secret_names],
            "env": [{{
                "name": secret.upper().replace("-", "_"),
                "secret_ref": secret,
            }} for secret in secret_names],
        }}))
    else:
        raise SystemExit(3)
elif args[:3] == ["containerapp", "revision", "list"]:
    image, suffix = current_deployment(name)
    print(json.dumps([{{
        "active": True,
        "healthState": "Healthy",
        "name": f"{{name}}--{{suffix}}",
        "provisioningState": "Provisioned",
        "trafficWeight": 100,
        "template": {{"containers": [{{"image": image}}]}},
    }}]))
elif args[:3] == ["containerapp", "revision", "show"]:
    image, _ = current_deployment(name)
    print(image)
elif args[:2] == ["containerapp", "update"]:
    image = args[args.index("--image") + 1]
    suffix = args[args.index("--revision-suffix") + 1]
    with pathlib.Path(os.environ["FAKE_LOG"]).open("a") as handle:
        handle.write(f"{{name}} {{image}} {{suffix}}\\n")
    if (os.environ.get("FAIL_PUBLIC") == "1" and
            name == os.environ["PUBLIC_APP"] and
            image == os.environ["IMAGE_REF"]):
        raise SystemExit(42)
else:
    raise SystemExit(4)
""",
    )
    _make_executable(
        bin_dir / "curl",
        f"""#!{sys.executable}
import json
import sys

url = sys.argv[-1]
mount = "provider_assistant" if "provider.example" in url else "public"
print(json.dumps({{"status": "ok", "mock_mode": False, "mount_profile": mount}}))
""",
    )
    _make_executable(
        bin_dir / "smoke-python",
        f"""#!{sys.executable}
import os
import pathlib
import sys

if len(sys.argv) > 1 and sys.argv[1] == "-c":
    raise SystemExit(0)
target = sys.argv[-1]
if target.endswith("smoke_live.py"):
    assert os.environ.get("SOVEREIGN_PROVIDER_TOKEN")
    assert os.environ.get("SOVEREIGN_NON_PROVIDER_TOKEN")
    assert not os.environ.get("SOVEREIGN_SYNTHETIC_RECORD_GRANT")
elif target.endswith("smoke_labs.py"):
    assert os.environ.get("SOVEREIGN_PROVIDER_TOKEN")
    assert os.environ.get("SOVEREIGN_SYNTHETIC_RECORD_GRANT")
    assert not os.environ.get("SOVEREIGN_NON_PROVIDER_TOKEN")
pathlib.Path(os.environ["SMOKE_LOG"]).open("a").write(sys.argv[-1] + "\\n")
if os.environ.get("FAIL_FUNCTIONAL") == "1" and sys.argv[-1].endswith("smoke_labs.py"):
    raise SystemExit(7)
""",
    )
    return bin_dir, log


def _deploy_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_dir, log = _fake_commands(tmp_path)
    digest = "sha256:" + "c" * 64
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RESOURCE_GROUP": "rg-test",
        "PROVIDER_APP": "provider-app",
        "PUBLIC_APP": "public-app",
        "REGISTRY_SERVER": "testregistry.azurecr.io",
        "IMAGE_REPO": "repo",
        "IMAGE_REF": f"testregistry.azurecr.io/repo@{digest}",
        "REVISION_SUFFIX": "r-12345-1",
        "OLD_PROVIDER": "testregistry.azurecr.io/repo@sha256:" + "a" * 64,
        "OLD_PUBLIC": "testregistry.azurecr.io/repo@sha256:" + "b" * 64,
        "FAKE_LOG": str(log),
        "SMOKE_LOG": str(tmp_path / "smoke.log"),
        "SMOKE_PYTHON": str(bin_dir / "smoke-python"),
        "SOVEREIGN_PROVIDER_TOKEN": "provider.header.signature",
        "SOVEREIGN_NON_PROVIDER_TOKEN": "member.header.signature",
        "SOVEREIGN_SYNTHETIC_RECORD_GRANT": "gAAAAA-synthetic-record-grant",
        "SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED": "true",
    }
    return env, log


def test_dual_deploy_uses_one_digest_for_both_mounts(tmp_path: Path) -> None:
    env, log = _deploy_env(tmp_path)
    result = subprocess.run([DEPLOY], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    lines = log.read_text().splitlines()
    assert lines == [
        f"provider-app {env['IMAGE_REF']} r-12345-1-p",
        f"public-app {env['IMAGE_REF']} r-12345-1-u",
    ]


def test_failure_rolls_both_mounts_back_to_immutable_images(tmp_path: Path) -> None:
    env, log = _deploy_env(tmp_path)
    env["FAIL_PUBLIC"] = "1"
    result = subprocess.run([DEPLOY], env=env, text=True, capture_output=True)
    assert result.returncode == 1
    lines = log.read_text().splitlines()
    assert lines[-2:] == [
        f"provider-app {env['OLD_PROVIDER']} rb-r-12345-1-p",
        f"public-app {env['OLD_PUBLIC']} rb-r-12345-1-u",
    ]


def test_functional_smoke_failure_rolls_both_mounts_back(tmp_path: Path) -> None:
    env, log = _deploy_env(tmp_path)
    env["FAIL_FUNCTIONAL"] = "1"
    result = subprocess.run([DEPLOY], env=env, text=True, capture_output=True)
    assert result.returncode == 1
    assert (tmp_path / "smoke.log").read_text().splitlines() == [
        str(ROOT / "tools" / "smoke_live.py"),
        str(ROOT / "tools" / "smoke_labs.py"),
    ]
    assert log.read_text().splitlines()[-2:] == [
        f"provider-app {env['OLD_PROVIDER']} rb-r-12345-1-p",
        f"public-app {env['OLD_PUBLIC']} rb-r-12345-1-u",
    ]


def test_deploy_refuses_a_mutable_previous_image(tmp_path: Path) -> None:
    env, log = _deploy_env(tmp_path)
    env["OLD_PROVIDER"] = "testregistry.azurecr.io/repo:latest"
    result = subprocess.run([DEPLOY], env=env, text=True, capture_output=True)
    assert result.returncode == 1
    assert not log.exists()
    assert "immutable previous image" in result.stderr


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("REGISTRY_SERVER", "registry.example", "REGISTRY_SERVER"),
        ("REGISTRY_SERVER", "TestRegistry.azurecr.io", "REGISTRY_SERVER"),
        ("IMAGE_REPO", "../other/repo", "IMAGE_REPO"),
    ],
)
def test_deploy_refuses_invalid_registry_scope_before_mutation(
    tmp_path: Path, name: str, value: str, message: str
) -> None:
    env, log = _deploy_env(tmp_path)
    env[name] = value
    result = subprocess.run([DEPLOY], env=env, text=True, capture_output=True)
    assert result.returncode == 2
    assert not log.exists()
    assert message in result.stderr


def test_deploy_refuses_missing_functional_smoke_inputs_before_mutation(
    tmp_path: Path,
) -> None:
    env, log = _deploy_env(tmp_path)
    del env["SOVEREIGN_NON_PROVIDER_TOKEN"]
    result = subprocess.run([DEPLOY], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert not log.exists()
    assert "SOVEREIGN_NON_PROVIDER_TOKEN is required" in result.stderr


def test_terraform_keeps_runtime_application_secrets_out_of_state() -> None:
    terraform = "\n".join(
        path.read_text() for path in sorted((ROOT / "infra").glob("*.tf"))
    )
    key_vault = (ROOT / "infra" / "key_vault.tf").read_text()
    variables = (ROOT / "infra" / "variables.tf").read_text()
    public_secret_block = variables.split(
        'variable "public_secret_vars"', 1
    )[1].split('variable "public_public_vars"', 1)[0]
    assert 'resource "azurerm_key_vault" "provider"' in key_vault
    assert 'resource "azurerm_key_vault" "public"' in key_vault
    assert 'resource "azurerm_key_vault_secret"' not in terraform
    assert "key_vault_secret_id" in terraform
    assert "data.external.secrets" not in terraform
    assert "data.external.public_secrets" not in terraform
    assert "admin_password" not in terraform
    assert "admin_enabled       = false" in terraform
    assert terraform.count('role_definition_name = "AcrPull"') == 2
    assert 'variable "key_vault_secrets_ready"' in terraform
    assert 'variable "initial_image_ref"' in terraform
    assert 'variable "live_apps_enabled"' in terraform
    assert terraform.count("count                        = var.live_apps_enabled ? 1 : 0") == 2
    assert terraform.count("condition     = var.key_vault_secrets_ready") == 2
    assert terraform.count("startswith(var.initial_image_ref") == 2
    assert terraform.count("image  = var.initial_image_ref") == 2
    assert "primary_access_key" not in terraform
    assert "access_key" not in terraform
    assert ":latest" not in terraform
    assert 'name  = "AZURE_CLIENT_ID"' in terraform
    assert "azurerm_role_assignment.sovereign_deid" in terraform
    assert terraform.count('role_definition_name = "Key Vault Secrets Officer"') == 2
    assert 'resource "azurerm_role_assignment" "bootstrap_acr_push"' in terraform
    assert 'resource "azurerm_role_assignment" "bootstrap_acr_tasks"' in terraform
    assert "bootstrap_operator_object_id == null ? 0 : 1" in terraform
    assert terraform.count("prevent_destroy = true") == 4
    assert "SOVEREIGN_RECORD_GRANT_SECRET" in variables
    assert "SOVEREIGN_RECORD_GRANT_SECRET" not in public_secret_block
    assert "ATHENA_CLIENT_ID" not in public_secret_block
    assert "ATHENA_CLIENT_SECRET" not in public_secret_block


def test_public_config_export_never_resolves_secret_references(tmp_path: Path) -> None:
    source = tmp_path / "mount.env"
    source.write_text("A=one\nB='two words'\nSECRET='op://Vault/item/value'\n")
    script = ROOT / "infra" / "scripts" / "export-public-env.sh"

    ok = subprocess.run(
        [script, source, "A,B", "public"], text=True, capture_output=True
    )
    assert ok.returncode != 0
    assert "missing deployment keys" in ok.stderr

    complete = tmp_path / "public.env"
    complete.write_text(_deployment_env_text("public"))
    refused = subprocess.run(
        [script, complete, "MAGISTERIUM_API_KEY", "public"],
        text=True,
        capture_output=True,
    )
    assert refused.returncode != 0
    assert "secret reference forbidden" in refused.stderr

    ok = subprocess.run(
        [script, complete, "MOCK_MODE,FAST_MODEL", "public"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(ok.stdout) == {
        "FAST_MODEL": "gpt-5.4-nano",
        "MOCK_MODE": "false",
    }


def test_secret_sync_uses_files_not_cli_values() -> None:
    script = (ROOT / "infra" / "scripts" / "sync-key-vault-secrets.sh").read_text()
    assert "--file \"$secret_file\"" in script
    assert 'op read --no-newline "$reference"' in script
    assert "--value" not in script
    assert "ATHENA_CLIENT_SECRET" in script
    assert "SOVEREIGN_RECORD_GRANT_SECRET" in script
    assert r"[A-Za-z0-9_-]{43}=" in script
    assert "base64.urlsafe_b64encode(decoded).decode() != value" in script
    assert "public)" in script
    assert 'EXPECTED_VAULT="cosmasovprodprovider"' in script
    assert 'EXPECTED_VAULT="cosmasovprodpublic"' in script
    assert "tags.mount" in script
    assert "unexpected secret metadata is present in the selected vault" in script
    assert "validate-deployment-env.py" in script

    boundary = (
        ROOT / "infra" / "scripts" / "verify-key-vault-boundaries.sh"
    ).read_text()
    assert "exact mount allowlists" in boundary
    assert boundary.count("athena-client-") == 2
    assert "az keyvault secret list" in boundary
    assert "verify-runtime-role-boundaries.sh" in boundary


def test_bootstrap_role_check_covers_operator_and_both_runtime_identities() -> None:
    script = (
        ROOT / "infra" / "scripts" / "verify-bootstrap-role-assignments.sh"
    ).read_text()
    assert script.count("require_role ") == 4
    assert '"AcrPush"' in script
    assert '"Container Registry Tasks Contributor"' in script
    assert script.count('"Key Vault Secrets Officer"') == 2
    assert "verify-runtime-role-boundaries.sh" in script

    boundaries = (
        ROOT / "infra" / "scripts" / "verify-runtime-role-boundaries.sh"
    ).read_text()
    assert boundaries.count("assert_effective_roles ") == 8
    assert "--include-inherited" in boundaries
    assert boundaries.count('"Key Vault Secrets User"') >= 2
    assert '"DeID Realtime Data User"' in boundaries
    assert "custom-role dataAction bypasses" in boundaries
    assert "transitiveMemberOf?%24select=id" in boundaries
    assert boundaries.count("assert_no_group_memberships ") == 2
    assert "provider_principal" in boundaries
    assert "public_principal" in boundaries


def test_bootstrap_build_emits_only_an_immutable_digest() -> None:
    script = (ROOT / "infra" / "scripts" / "build-bootstrap-image.sh").read_text()
    assert "az acr manifest show-metadata" in script
    assert "@\u0024digest" in script
    assert ":latest" not in script
    assert "status --porcelain --untracked-files=all" in script
    assert "Nixpacks release checksum mismatch" in script
    assert "194bcad8c379f78a309eee1a88b2e6b2" in script
    assert "0c6799690ad0b7328691fe3e457ec93d" in script
    assert "archive --format=tar HEAD" in script
    assert '"$nixpacks_bin" build "$source_dir"' in script
    assert '--out "$source_dir"' in script
    assert '--cache-key "$sha"' in script
    assert '--label "org.opencontainers.image.revision=$sha" >&2' in script
    assert "finalize-nixpacks-context.py" in script
    assert "az acr build" in script
    assert "docker push" not in script
    assert "az acr login" not in script


def test_workflow_resolves_digest_and_calls_paired_deployer() -> None:
    workflow = (ROOT / ".github" / "workflows" / "build_and_publish.yml").read_text()
    assert "AZURE_PROVIDER_CONTAINER_APP" in workflow
    assert "AZURE_PUBLIC_CONTAINER_APP" in workflow
    assert "az acr manifest show-metadata" in workflow
    assert 'echo "IMAGE_REF=$IMAGE@$DIGEST"' in workflow
    assert "infra/scripts/deploy-dual.sh" in workflow
    assert 'docker tag "$IMAGE:$SHA" "$IMAGE:latest"' not in workflow
    assert "SOVEREIGN_DEPLOY_ENABLED == 'true'" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "timeout-minutes: 60" in workflow
    deployer = DEPLOY.read_text()
    assert "containerapp revision list" in deployer
    assert "expected exactly one active healthy 100%-traffic revision" in deployer
    assert "trap on_exit EXIT" in deployer
    assert "tools/smoke_live.py" in deployer
    assert "tools/smoke_labs.py" in deployer
    assert "verify-live-app-boundaries.sh" in deployer
    assert "SOVEREIGN_NON_PROVIDER_TOKEN" in deployer
    assert "SOVEREIGN_SYNTHETIC_RECORD_GRANT" in deployer
    assert "SOVEREIGN_INTERACTIVE_SMOKE_CREDENTIALS" in workflow
    assert "secrets.SOVEREIGN_PROVIDER_TOKEN" not in workflow
    assert "secrets.SOVEREIGN_NON_PROVIDER_TOKEN" not in workflow
    assert "secrets.SOVEREIGN_SYNTHETIC_RECORD_GRANT" not in workflow
    assert "/dev/tty" in deployer

    live_boundaries = (
        ROOT / "infra" / "scripts" / "verify-live-app-boundaries.sh"
    ).read_text()
    assert "exactly one user-assigned runtime identity" in live_boundaries
    assert "app secret inventory is not the exact mount allowlist" in live_boundaries
    assert "app registry binding does not use its runtime identity" in live_boundaries


def _deployment_env_text(mount: str) -> str:
    name = (
        ".env.production.backend.example"
        if mount == "provider"
        else ".env.production.public.example"
    )
    return (
        (ROOT / name)
        .read_text()
        .replace("tenant_replace_me", "tenant_preview_195900")
        .replace("your-instance.clerk.accounts.dev", "tenant.clerk.accounts.dev")
    )


def _run_env_validator(mount: str, path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            ROOT / "infra" / "scripts" / "validate-deployment-env.py",
            mount,
            path,
        ],
        text=True,
        capture_output=True,
    )


def test_deployment_env_validator_accepts_exact_examples_after_provisioning(
    tmp_path: Path,
) -> None:
    for mount in ("provider", "public"):
        path = tmp_path / f"{mount}.env"
        path.write_text(_deployment_env_text(mount))
        result = _run_env_validator(mount, path)
        assert result.returncode == 0, result.stderr


def test_deployment_env_validator_rejects_public_ehr_metadata_without_value_leak(
    tmp_path: Path,
) -> None:
    sentinel = "op://NeverPrint/athena/secret-sentinel"
    path = tmp_path / "public.env"
    path.write_text(_deployment_env_text("public") + f"\nATHENA_CLIENT_SECRET={sentinel}\n")
    result = _run_env_validator("public", path)
    assert result.returncode == 1
    assert "unexpected deployment keys: ATHENA_CLIENT_SECRET" in result.stderr
    assert sentinel not in result.stderr


def test_deployment_env_validator_matches_runtime_cors_and_rate_bounds(
    tmp_path: Path,
) -> None:
    path = tmp_path / "public.env"
    source = _deployment_env_text("public")
    path.write_text(
        source.replace(
            'CORS_ALLOW_ORIGINS=["https://cosmaneura.com"]',
            'CORS_ALLOW_ORIGINS=["https://cosmaneura.com/"]',
        )
    )
    assert _run_env_validator("public", path).returncode == 1

    path.write_text(source.replace("PUBLIC_CHAT_RATE_LIMIT_REQUESTS=30", "PUBLIC_CHAT_RATE_LIMIT_REQUESTS=30.5"))
    assert _run_env_validator("public", path).returncode == 1

    path.write_text(source.replace("PUBLIC_CHAT_RATE_LIMIT_WINDOW_SECONDS=60", "PUBLIC_CHAT_RATE_LIMIT_WINDOW_SECONDS=NaN"))
    assert _run_env_validator("public", path).returncode == 1


def test_persistent_deployment_inputs_are_fail_closed(tmp_path: Path) -> None:
    validator = ROOT / "infra" / "scripts" / "validate-deployment-inputs.py"
    path = tmp_path / "deployment.auto.tfvars"
    path.write_text((ROOT / "infra" / "deployment.auto.tfvars.example").read_text())
    result = subprocess.run(
        [sys.executable, validator, path], text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr

    digest = "a" * 64
    path.write_text(
        "live_apps_enabled = true\n"
        "key_vault_secrets_ready = true\n"
        'initial_image_ref = "cosmaneurasovereignprodregistry.azurecr.io/'
        f'cosmaneura/sovereign@sha256:{digest}"\n'
    )
    result = subprocess.run(
        [sys.executable, validator, path], text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr

    path.write_text(path.read_text().replace("key_vault_secrets_ready = true", "key_vault_secrets_ready = false"))
    result = subprocess.run(
        [sys.executable, validator, path], text=True, capture_output=True
    )
    assert result.returncode == 1


def test_procfile_external_parser_preserves_exact_web_command() -> None:
    parser = ROOT / "infra" / "scripts" / "read-procfile.py"
    result = subprocess.run(
        [sys.executable, parser, ROOT / "Procfile"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == {
        "web": "uvicorn app.main:app --host 0.0.0.0 --port 8000"
    }
    variables = (ROOT / "infra" / "variables.tf").read_text()
    preflight = (ROOT / "infra" / "scripts" / "preflight.sh").read_text()
    assert "scripts/read-procfile.py" in variables
    assert '"yq"' not in variables
    assert " yq " not in preflight
    assert " docker " not in preflight


def test_nixpacks_remote_context_is_validated_and_normalized(tmp_path: Path) -> None:
    source = tmp_path / "source"
    generated = source / ".nixpacks"
    generated.mkdir(parents=True)
    revision = "a" * 40
    image = f"registry.azurecr.io/cosmaneura/sovereign:{revision}"
    dockerfile = generated / "Dockerfile"
    dockerfile.write_text(
        "FROM ghcr.io/railwayapp/nixpacks:ubuntu-1745885067\n"
        f"RUN --mount=type=cache,id={revision}-/root/cache/pip "
        "python -m pip install --require-hashes --only-binary=:all: "
        "-r requirements.lock\n"
    )
    arguments = [
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
    command = [
        "docker", "build", str(source),
        "-f", str(dockerfile),
        "-t", image,
    ]
    for argument in arguments:
        command.extend(["--build-arg", argument])
    command.extend([
        "--label", f"org.opencontainers.image.revision={revision}",
        "--platform", "linux/amd64",
    ])
    (generated / "build.sh").write_text(shlex.join(command))

    helper = ROOT / "infra" / "scripts" / "finalize-nixpacks-context.py"
    result = subprocess.run(
        [sys.executable, helper, source, image, revision, "1700000000"],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert not (generated / "build.sh").exists()
    assert (
        f'LABEL org.opencontainers.image.revision="{revision}"'
        in dockerfile.read_text()
    )
    assert int(dockerfile.stat().st_mtime) == 1_700_000_000
    assert int(source.stat().st_mtime) == 1_700_000_000
