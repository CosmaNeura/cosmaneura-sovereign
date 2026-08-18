"""Deployment integrity regressions for the two physical mounts."""
from __future__ import annotations

import hashlib
import importlib.util
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
REHEARSE = ROOT / "infra" / "scripts" / "rehearse-staging-rollback.sh"
VERIFY_IMAGE_REVISION = (
    ROOT / "infra" / "scripts" / "verify-acr-image-revision.py"
)


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
call_log = os.environ.get("FAKE_AZ_CALL_LOG")
if call_log:
    with pathlib.Path(call_log).open("a") as handle:
        handle.write(" ".join(args) + "\\n")
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

def identity_id(identity_name):
    return (
        f"/subscriptions/test/resourceGroups/{{os.environ['RESOURCE_GROUP']}}"
        "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
        f"{{identity_name}}"
    )

def environment_id(environment_name):
    return (
        f"/subscriptions/test/resourceGroups/{{os.environ['RESOURCE_GROUP']}}"
        "/providers/Microsoft.App/managedEnvironments/"
        f"{{environment_name}}"
    )

if args[:2] == ["identity", "show"]:
    print(identity_id(name))
elif args[:3] == ["containerapp", "env", "show"]:
    print(environment_id(name))
elif args[:2] == ["containerapp", "show"]:
    query = args[args.index("--query") + 1]
    if "ingress.fqdn" in query:
        print("provider.example" if name == os.environ["PROVIDER_APP"] else "public.example")
    elif "identity:identity" in query:
        provider = name == os.environ["PROVIDER_APP"]
        environment = os.environ["DEPLOYMENT_ENVIRONMENT"]
        identity_name = (
            f"cosmaneura-sovereign-{{environment}}-runtime"
            if provider else f"cosmaneura-web-{{environment}}-runtime"
        )
        environment_name = (
            f"cosmaneura-sovereign-{{environment}}-app-environment"
            if provider else f"cosmaneura-web-{{environment}}-app-environment"
        )
        vault = (
            f"cosmasov{{environment}}provider"
            if provider else f"cosmasov{{environment}}public"
        )
        runtime_identity_id = identity_id(identity_name)
        secret_names = ["azure-openai-api-key", "magisterium-api-key"]
        if provider:
            secret_names += [
                "athena-client-id",
                "athena-client-secret",
                "sovereign-record-grant-secret",
            ]
        print(json.dumps({{
            "environment_id": environment_id(environment_name),
            "identity": {{
                "type": "UserAssigned",
                "userAssignedIdentities": {{runtime_identity_id: {{}}}},
            }},
            "registries": [{{
                "server": os.environ["REGISTRY_SERVER"],
                "identity": runtime_identity_id,
            }}],
            "secrets": [{{
                "name": secret,
                "key_vault_url": f"https://{{vault}}.vault.azure.net/secrets/{{secret}}",
                "identity": runtime_identity_id,
            }} for secret in secret_names],
            "containers": [{{
                "name": (
                    f"cosmaneura-sovereign-{{environment}}-app-web"
                    if provider else f"cosmaneura-web-{{environment}}-web"
                ),
                "env": [{{
                    "name": secret.upper().replace("-", "_"),
                    "secret_ref": secret,
                }} for secret in secret_names],
            }}],
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
    _make_executable(
        bin_dir / "git",
        f"""#!{sys.executable}
import os
import sys

args = sys.argv[1:]
if args[:1] == ["-C"]:
    args = args[2:]
if args == ["status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"]:
    sys.stdout.write(os.environ.get("FAKE_GIT_STATUS", ""))
elif args == ["rev-parse", "--verify", "HEAD^{{commit}}"]:
    print(os.environ["SOURCE_COMMIT"])
else:
    raise SystemExit(5)
""",
    )
    _make_executable(
        bin_dir / "python3",
        f"""#!{sys.executable}
import os
import pathlib
import sys

args = sys.argv[1:]
if args and pathlib.Path(args[0]).name == "verify-acr-image-revision.py":
    expected = [
        os.environ["REGISTRY_SERVER"],
        "cosmaneura/sovereign",
        os.environ["IMAGE_REF"].split("@", 1)[1],
        os.environ["SOURCE_COMMIT"],
    ]
    pathlib.Path(os.environ["PROVENANCE_LOG"]).write_text(" ".join(args[1:]) + "\\n")
    if args[1:] != expected or os.environ.get("FAIL_PROVENANCE") == "1":
        raise SystemExit(1)
    print("candidate OCI revision label matches the clean checked-out HEAD")
else:
    os.execv({sys.executable!r}, [{sys.executable!r}, *args])
""",
    )
    return bin_dir, log


def _deploy_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_dir, log = _fake_commands(tmp_path)
    digest = "sha256:" + "c" * 64
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "DEPLOYMENT_ENVIRONMENT": "staging",
        "RESOURCE_GROUP": "cosmaneura-sovereign-staging",
        "PROVIDER_APP": "cosmaneura-sovereign-staging-app",
        "PUBLIC_APP": "cosmaneura-web-staging-app",
        "REGISTRY_SERVER": "cosmaneurasovereignstagingregistry.azurecr.io",
        "IMAGE_REPO": "repo",
        "IMAGE_REF": f"cosmaneurasovereignstagingregistry.azurecr.io/repo@{digest}",
        "REVISION_SUFFIX": "r-12345-1",
        "OLD_PROVIDER": "cosmaneurasovereignstagingregistry.azurecr.io/repo@sha256:" + "a" * 64,
        "OLD_PUBLIC": "cosmaneurasovereignstagingregistry.azurecr.io/repo@sha256:" + "b" * 64,
        "FAKE_LOG": str(log),
        "FAKE_AZ_CALL_LOG": str(tmp_path / "az-calls.log"),
        "PROVENANCE_LOG": str(tmp_path / "provenance.log"),
        "SOURCE_COMMIT": "d" * 40,
        "SMOKE_LOG": str(tmp_path / "smoke.log"),
        "SMOKE_PYTHON": str(bin_dir / "smoke-python"),
        "SOVEREIGN_PROVIDER_TOKEN": "provider.header.signature",
        "SOVEREIGN_NON_PROVIDER_TOKEN": "member.header.signature",
        "SOVEREIGN_SYNTHETIC_RECORD_GRANT": "gAAAAA-synthetic-record-grant",
        "SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED": "true",
    }
    return env, log


def _configure_rehearsal(env: dict[str, str], tmp_path: Path) -> Path:
    env["IMAGE_REPO"] = "cosmaneura/sovereign"
    env["IMAGE_REF"] = env["IMAGE_REF"].replace(
        "/repo@", "/cosmaneura/sovereign@"
    )
    env["OLD_PROVIDER"] = env["OLD_PROVIDER"].replace(
        "/repo@", "/cosmaneura/sovereign@"
    )
    env["OLD_PUBLIC"] = env["OLD_PROVIDER"]
    evidence = tmp_path / "evidence" / "paired-rollback.json"
    env["REHEARSAL_EVIDENCE_PATH"] = str(evidence)
    return evidence


def test_dual_deploy_uses_one_digest_for_both_mounts(tmp_path: Path) -> None:
    env, log = _deploy_env(tmp_path)
    result = subprocess.run([DEPLOY], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    lines = log.read_text().splitlines()
    assert lines == [
        f"{env['PROVIDER_APP']} {env['IMAGE_REF']} r-12345-1-p",
        f"{env['PUBLIC_APP']} {env['IMAGE_REF']} r-12345-1-u",
    ]


def test_failure_rolls_both_mounts_back_to_immutable_images(tmp_path: Path) -> None:
    env, log = _deploy_env(tmp_path)
    env["FAIL_PUBLIC"] = "1"
    result = subprocess.run([DEPLOY], env=env, text=True, capture_output=True)
    assert result.returncode == 1
    lines = log.read_text().splitlines()
    assert lines[-2:] == [
        f"{env['PROVIDER_APP']} {env['OLD_PROVIDER']} rb-r-12345-1-p",
        f"{env['PUBLIC_APP']} {env['OLD_PUBLIC']} rb-r-12345-1-u",
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
        f"{env['PROVIDER_APP']} {env['OLD_PROVIDER']} rb-r-12345-1-p",
        f"{env['PUBLIC_APP']} {env['OLD_PUBLIC']} rb-r-12345-1-u",
    ]


def test_staging_rehearsal_forces_rollback_only_after_live_smokes(
    tmp_path: Path,
) -> None:
    env, log = _deploy_env(tmp_path)
    env["OLD_PUBLIC"] = env["OLD_PROVIDER"]
    env["SOVEREIGN_REHEARSAL_FORCE_ROLLBACK"] = "true"
    result = subprocess.run([DEPLOY], env=env, text=True, capture_output=True)
    assert result.returncode == 75, result.stderr
    assert (tmp_path / "smoke.log").read_text().splitlines() == [
        str(ROOT / "tools" / "smoke_live.py"),
        str(ROOT / "tools" / "smoke_labs.py"),
    ]
    assert log.read_text().splitlines()[-2:] == [
        f"{env['PROVIDER_APP']} {env['OLD_PROVIDER']} rb-r-12345-1-p",
        f"{env['PUBLIC_APP']} {env['OLD_PUBLIC']} rb-r-12345-1-u",
    ]


def test_staging_rehearsal_wrapper_retains_secret_free_evidence(
    tmp_path: Path,
) -> None:
    env, log = _deploy_env(tmp_path)
    evidence = _configure_rehearsal(env, tmp_path)
    result = subprocess.run([REHEARSE], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(evidence.read_text())
    assert payload["environment"] == "staging"
    assert payload["result"] == "passed"
    assert payload["source_commit"] == env["SOURCE_COMMIT"]
    assert payload["candidate_image"] == env["IMAGE_REF"]
    assert payload["before"]["paired_image"] == env["OLD_PROVIDER"]
    assert payload["after"]["paired_image"] == env["OLD_PROVIDER"]
    serialized = evidence.read_text()
    assert env["SOVEREIGN_PROVIDER_TOKEN"] not in serialized
    assert env["SOVEREIGN_NON_PROVIDER_TOKEN"] not in serialized
    assert env["SOVEREIGN_SYNTHETIC_RECORD_GRANT"] not in serialized
    assert (tmp_path / "provenance.log").read_text().split() == [
        env["REGISTRY_SERVER"],
        "cosmaneura/sovereign",
        env["IMAGE_REF"].split("@", 1)[1],
        env["SOURCE_COMMIT"],
    ]
    assert log.read_text().splitlines()[-2:] == [
        f"{env['PROVIDER_APP']} {env['OLD_PROVIDER']} rb-r-12345-1-p",
        f"{env['PUBLIC_APP']} {env['OLD_PUBLIC']} rb-r-12345-1-u",
    ]


def test_staging_rehearsal_refuses_dirty_or_untracked_tree_before_cloud_access(
    tmp_path: Path,
) -> None:
    env, log = _deploy_env(tmp_path)
    evidence = _configure_rehearsal(env, tmp_path)
    env["FAKE_GIT_STATUS"] = "?? operator-notes.txt\n"

    result = subprocess.run([REHEARSE], env=env, text=True, capture_output=True)

    assert result.returncode == 2
    assert "requires a clean worktree" in result.stderr
    assert not log.exists()
    assert not Path(env["FAKE_AZ_CALL_LOG"]).exists()
    assert not Path(env["PROVENANCE_LOG"]).exists()
    assert not evidence.exists()


def test_staging_rehearsal_refuses_unproven_candidate_before_mutation_or_evidence(
    tmp_path: Path,
) -> None:
    env, log = _deploy_env(tmp_path)
    evidence = _configure_rehearsal(env, tmp_path)
    env["FAIL_PROVENANCE"] = "1"

    result = subprocess.run([REHEARSE], env=env, text=True, capture_output=True)

    assert result.returncode == 1
    assert not log.exists()
    assert not Path(env["FAKE_AZ_CALL_LOG"]).exists()
    assert Path(env["PROVENANCE_LOG"]).exists()
    assert not evidence.exists()


def _load_image_revision_helper():
    spec = importlib.util.spec_from_file_location(
        "verify_acr_image_revision", VERIFY_IMAGE_REVISION
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _image_documents(revision: str) -> tuple[str, dict[str, bytes]]:
    config = _json_bytes(
        {"config": {"Labels": {"org.opencontainers.image.revision": revision}}}
    )
    config_digest = "sha256:" + hashlib.sha256(config).hexdigest()
    manifest = _json_bytes(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": config_digest,
                "size": len(config),
            },
            "layers": [],
        }
    )
    image_digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
    repository = "cosmaneura/sovereign"
    return image_digest, {
        f"/v2/{repository}/manifests/{image_digest}": manifest,
        f"/v2/{repository}/blobs/{config_digest}": config,
    }


def test_acr_provenance_helper_requires_exact_head_label_and_digests() -> None:
    helper = _load_image_revision_helper()
    source_commit = "d" * 40
    image_digest, documents = _image_documents(source_commit)

    def fetch(path: str, _accept: str, _limit: int) -> bytes:
        return documents[path]

    helper.verify_image_revision(
        "cosmaneura/sovereign", image_digest, source_commit, fetch
    )

    with pytest.raises(
        helper.RevisionVerificationError,
        match="revision label does not equal",
    ):
        helper.verify_image_revision(
            "cosmaneura/sovereign", image_digest, "e" * 40, fetch
        )

    documents[f"/v2/cosmaneura/sovereign/manifests/{image_digest}"] += b"\n"
    with pytest.raises(
        helper.RevisionVerificationError,
        match="manifest content does not match",
    ):
        helper.verify_image_revision(
            "cosmaneura/sovereign", image_digest, source_commit, fetch
        )


def test_forced_rollback_is_refused_outside_staging_before_mutation(
    tmp_path: Path,
) -> None:
    env, log = _deploy_env(tmp_path)
    env["DEPLOYMENT_ENVIRONMENT"] = "prod"
    env["SOVEREIGN_REHEARSAL_FORCE_ROLLBACK"] = "true"
    result = subprocess.run([DEPLOY], env=env, text=True, capture_output=True)
    assert result.returncode == 2
    assert not log.exists()
    assert "allowed only in staging" in result.stderr


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
    assert terraform.count("prevent_destroy = true") == 14
    assert 'resource "terraform_data" "state_environment_guard"' in terraform
    assert "triggers_replace = local.environment" in terraform
    assert "SOVEREIGN_RECORD_GRANT_SECRET" in variables
    assert "SOVEREIGN_RECORD_GRANT_SECRET" not in public_secret_block
    assert "ATHENA_CLIENT_ID" not in public_secret_block
    assert "ATHENA_CLIENT_SECRET" not in public_secret_block


def test_public_config_export_never_resolves_secret_references(tmp_path: Path) -> None:
    source = tmp_path / "mount.env"
    source.write_text("A=one\nB='two words'\nSECRET='op://Vault/item/value'\n")
    script = ROOT / "infra" / "scripts" / "export-public-env.sh"

    ok = subprocess.run(
        [script, "prod", source, "A,B", "public"], text=True, capture_output=True
    )
    assert ok.returncode != 0
    assert "missing deployment keys" in ok.stderr

    complete = tmp_path / "public.env"
    complete.write_text(_deployment_env_text("public"))
    refused = subprocess.run(
        [script, "prod", complete, "MAGISTERIUM_API_KEY", "public"],
        text=True,
        capture_output=True,
    )
    assert refused.returncode != 0
    assert "secret reference forbidden" in refused.stderr

    ok = subprocess.run(
        [script, "prod", complete, "MOCK_MODE,FAST_MODEL", "public"],
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
    assert 'EXPECTED_VAULT="$EXPECTED_PROVIDER_VAULT"' in script
    assert 'EXPECTED_VAULT="$EXPECTED_PUBLIC_VAULT"' in script
    assert "DEPLOYMENT_ENVIRONMENT" in script
    assert "tags.mount" in script
    assert "unexpected secret metadata is present in the selected vault" in script
    assert "validate-deployment-env.py" in script

    boundary = (
        ROOT / "infra" / "scripts" / "verify-key-vault-boundaries.sh"
    ).read_text()
    assert "exact mount allowlists" in boundary
    assert boundary.count("athena-client-") == 2
    assert "az keyvault secret list" in boundary
    assert "properties.enableRbacAuthorization" in boundary
    assert "properties.accessPolicies" in boundary
    assert "RBAC-only authorization" in boundary
    assert "verify-runtime-role-boundaries.sh" in boundary


def test_bootstrap_role_check_covers_operator_and_both_runtime_identities() -> None:
    script = (
        ROOT / "infra" / "scripts" / "verify-bootstrap-role-assignments.sh"
    ).read_text()
    assert script.count("require_role ") == 4
    assert '"AcrPush"' in script
    assert '"Container Registry Tasks Contributor"' in script
    assert script.count('"Key Vault Secrets Officer"') == 2
    assert '--scope "$scope" --all' not in script
    assert "verify-runtime-role-boundaries.sh" in script

    boundaries = (
        ROOT / "infra" / "scripts" / "verify-runtime-role-boundaries.sh"
    ).read_text()
    assert boundaries.count("assert_scoped_roles ") == 8
    assert boundaries.count("assert_direct_inventory ") == 2
    assert "unexpected direct role assignment" in boundaries
    assert boundaries.count("list_direct_roles ") == 2
    assert "--include-inherited" in boundaries
    assert '--scope "$scope" --all' not in boundaries
    assert 'startswith(target + "/")' in boundaries
    assert "for item in inherited" in boundaries
    assert boundaries.count('"Key Vault Secrets User"') >= 2
    assert '"DeID Realtime Data User"' in boundaries
    assert "Any custom role" in boundaries
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


def test_bootstrap_build_resolves_dockerfile_from_generated_context(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    operator_dir = tmp_path / "operator"
    operator_dir.mkdir()
    revision = "a" * 40
    epoch = 1_700_000_000
    digest = "sha256:" + "b" * 64
    acr_log = tmp_path / "acr-build.json"

    nixpacks_payload = tmp_path / "nixpacks"
    _make_executable(
        nixpacks_payload,
        f"#!{sys.executable}\n"
        + """import pathlib
import shlex
import sys

args = sys.argv[1:]
if args == ["--version"]:
    print("nixpacks 1.41.0")
    raise SystemExit(0)
if len(args) < 2 or args[0] != "build":
    raise SystemExit(2)

source = pathlib.Path(args[1])
image = args[args.index("--name") + 1]
revision = args[args.index("--cache-key") + 1]
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
command = [
    "docker", "build", str(source),
    "-f", str(dockerfile),
    "-t", image,
]
for argument in build_args:
    command.extend(["--build-arg", argument])
command.extend([
    "--label", f"org.opencontainers.image.revision={revision}",
    "--platform", "linux/amd64",
])
(generated / "build.sh").write_text(shlex.join(command))
""",
    )

    _make_executable(
        bin_dir / "git",
        f"#!{sys.executable}\n"
        + """import io
import os
import sys
import tarfile

args = sys.argv[1:]
if args[:1] == ["-C"]:
    args = args[2:]
if args == ["status", "--porcelain", "--untracked-files=all"]:
    pass
elif args == ["rev-parse", "HEAD"]:
    print(os.environ["SOURCE_COMMIT"])
elif args == ["show", "-s", "--format=%ct", "HEAD"]:
    print(os.environ["SOURCE_EPOCH"])
elif args == ["archive", "--format=tar", "HEAD"]:
    payload = b"placeholder lock for generated Dockerfile context\\n"
    info = tarfile.TarInfo("requirements.lock")
    info.size = len(payload)
    info.mtime = int(os.environ["SOURCE_EPOCH"])
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
        archive.addfile(info, io.BytesIO(payload))
else:
    raise SystemExit(3)
""",
    )
    _make_executable(
        bin_dir / "curl",
        f"#!{sys.executable}\n"
        + """import os
import pathlib
import sys
import tarfile

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
import sys

if sys.argv[1:2] == ["-"]:
    raise SystemExit(0)
os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])
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
    dockerfile = args[args.index("--file") + 1]
    if pathlib.Path.cwd() == pathlib.Path(os.environ["OPERATOR_CWD"]):
        raise SystemExit("ACR build still runs from the operator directory")
    if dockerfile != ".nixpacks/Dockerfile" or not pathlib.Path(dockerfile).is_file():
        raise SystemExit("Dockerfile does not resolve from the ACR working directory")
    if args[-1] != ".":
        raise SystemExit("ACR source is not the generated working directory")
    content = pathlib.Path(dockerfile).read_text()
    expected_install = (
        "RUN python -m venv --copies /opt/venv && "
        ". /opt/venv/bin/activate && "
        "python -m pip install --require-hashes --only-binary=:all: "
        "-r requirements.lock"
    )
    if (
        "--mount" in content
        or content.splitlines().count(expected_install) != 1
        or "requirements.txt" in content
    ):
        raise SystemExit("ACR received an unsafe normalized install command")
    pathlib.Path(os.environ["FAKE_ACR_LOG"]).write_text(json.dumps({
        "cwd": str(pathlib.Path.cwd()),
        "dockerfile": dockerfile,
        "source": args[-1],
    }))
elif args[:3] == ["acr", "manifest", "show-metadata"]:
    print(os.environ["IMAGE_DIGEST"])
else:
    raise SystemExit(4)
""",
    )

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "DEPLOYMENT_ENVIRONMENT": "staging",
        "REGISTRY": "cosmaneurasovereignstagingregistry.azurecr.io",
        "IMAGE_REPO": "cosmaneura/sovereign",
        "SOURCE_COMMIT": revision,
        "SOURCE_EPOCH": str(epoch),
        "FAKE_NIXPACKS": str(nixpacks_payload),
        "FAKE_ACR_LOG": str(acr_log),
        "OPERATOR_CWD": str(operator_dir),
        "IMAGE_DIGEST": digest,
    }
    result = subprocess.run(
        [ROOT / "infra" / "scripts" / "build-bootstrap-image.sh"],
        cwd=operator_dir,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.strip() == (
        "cosmaneurasovereignstagingregistry.azurecr.io/"
        f"cosmaneura/sovereign@{digest}"
    )
    invocation = json.loads(acr_log.read_text())
    assert invocation["dockerfile"] == ".nixpacks/Dockerfile"
    assert invocation["source"] == "."
    assert Path(invocation["cwd"]).name == "source"


def test_workflow_resolves_digest_and_calls_paired_deployer() -> None:
    workflow = (ROOT / ".github" / "workflows" / "build_and_publish.yml").read_text()
    assert "AZURE_PROVIDER_CONTAINER_APP" in workflow
    assert "AZURE_PUBLIC_CONTAINER_APP" in workflow
    assert "REGISTRY_SERVER: ${{ vars.CONTAINER_REGISTRY_HOST }}" in workflow
    assert "infra/scripts/build-bootstrap-image.sh" in workflow
    assert 'echo "IMAGE_REF=$IMAGE_REF"' in workflow
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
    assert "read -r -s -t 120" in deployer
    assert "read -r -s -t 45" in deployer
    assert '"$RUNNER_TEMP/deploy-venv"' in workflow
    assert "SMOKE_PYTHON: ${{ runner.temp }}/deploy-venv/bin/python" in workflow
    assert "uv venv --python 3.13 .deploy-venv" not in workflow

    terraform = (ROOT / "infra" / "main.tf").read_text()
    assert 'environment:${local.deployment_context.github_environment}' in terraform
    assert "ref:refs/heads" not in terraform
    assert "environment: production" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "docker push" not in workflow
    assert "nixpacks build ." not in workflow

    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert ".deploy-venv" in dockerignore

    live_boundaries = (
        ROOT / "infra" / "scripts" / "verify-live-app-boundaries.sh"
    ).read_text()
    assert "exactly one user-assigned runtime identity" in live_boundaries
    assert "app secret inventory is not the exact mount allowlist" in live_boundaries
    assert "app registry binding does not use its runtime identity" in live_boundaries
    assert "az identity show" in live_boundaries
    assert "az containerapp env show" in live_boundaries
    assert "properties.environmentId" in live_boundaries
    assert "properties.managedEnvironmentId" in live_boundaries
    assert "app is attached to the wrong mount environment" in live_boundaries
    assert "app must have exactly one expected mount container" in live_boundaries


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
            "prod",
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
            'CORS_ALLOW_ORIGINS=["https://cosmaneura.com","https://www.cosmaneura.com"]',
            'CORS_ALLOW_ORIGINS=["https://cosmaneura.com/","https://www.cosmaneura.com"]',
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
        path.read_text()
        .replace("live_apps_enabled       = false", "live_apps_enabled       = true")
        .replace(
            "key_vault_secrets_ready = false",
            "key_vault_secrets_ready = true",
        )
        .replace(
            'initial_image_ref        = ""',
            'initial_image_ref        = "cosmaneurasovereignprodregistry.azurecr.io/'
            f'cosmaneura/sovereign@sha256:{digest}"',
        )
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
    assert 'abspath("${path.module}/scripts/read-procfile.py")' in variables
    assert 'abspath("${path.module}/../Procfile")' in variables
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
        f"RUN --mount=type=cache,id={revision}-/root/cache/pip,"
        "target=/root/.cache/pip "
        "python -m venv --copies /opt/venv && . /opt/venv/bin/activate && "
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
    original_dockerfile = dockerfile.read_text()
    dockerfile.write_text(
        original_dockerfile.replace(
            "nixpacks:ubuntu-1745885067", "nixpacks:ubuntu-unreviewed"
        )
    )
    rejected = subprocess.run(
        [sys.executable, helper, source, image, revision, "1700000000"],
        text=True,
        capture_output=True,
    )
    assert rejected.returncode == 1
    assert "base image drifted" in rejected.stderr
    dockerfile.write_text(original_dockerfile)

    dockerfile.write_text(
        original_dockerfile.replace(
            "target=/root/.cache/pip", "target=/tmp/unreviewed-pip-cache"
        )
    )
    rejected = subprocess.run(
        [sys.executable, helper, source, image, revision, "1700000000"],
        text=True,
        capture_output=True,
    )
    assert rejected.returncode == 1
    assert "pip cache mount or command drifted" in rejected.stderr
    dockerfile.write_text(original_dockerfile)

    dockerfile.write_text(
        original_dockerfile.replace(
            "python -m venv --copies /opt/venv", "python -m venv /opt/venv"
        )
    )
    rejected = subprocess.run(
        [sys.executable, helper, source, image, revision, "1700000000"],
        text=True,
        capture_output=True,
    )
    assert rejected.returncode == 1
    assert "pip cache mount or command drifted" in rejected.stderr
    dockerfile.write_text(original_dockerfile)

    dockerfile.write_text(
        original_dockerfile.replace(
            "-r requirements.lock", "-r requirements.lock --no-deps"
        )
    )
    rejected = subprocess.run(
        [sys.executable, helper, source, image, revision, "1700000000"],
        text=True,
        capture_output=True,
    )
    assert rejected.returncode == 1
    assert "pip cache mount or command drifted" in rejected.stderr
    dockerfile.write_text(original_dockerfile)

    result = subprocess.run(
        [sys.executable, helper, source, image, revision, "1700000000"],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert not (generated / "build.sh").exists()
    normalized = dockerfile.read_text()
    expected_install = (
        "RUN python -m venv --copies /opt/venv && "
        ". /opt/venv/bin/activate && "
        "python -m pip install --require-hashes --only-binary=:all: "
        "-r requirements.lock"
    )
    assert "RUN --mount" not in normalized
    assert "--mount" not in normalized
    assert normalized.splitlines().count(expected_install) == 1
    assert "requirements.txt" not in normalized
    assert (
        f'LABEL org.opencontainers.image.revision="{revision}"'
        in normalized
    )
    assert (
        "FROM ghcr.io/railwayapp/nixpacks:ubuntu-1745885067@sha256:"
        "d45c89d80e13d7ad0fd555b5130f22a866d9dd10e861f589932303ef2314c7de"
        in normalized
    )
    assert int(dockerfile.stat().st_mtime) == 1_700_000_000
    assert int(source.stat().st_mtime) == 1_700_000_000
