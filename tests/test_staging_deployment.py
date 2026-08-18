"""Safety contracts for the isolated non-production rehearsal boundary."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
SCRIPTS = INFRA / "scripts"


def _run_input_validator(path: Path, environment: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            SCRIPTS / "validate-deployment-inputs.py",
            path,
            environment,
        ],
        text=True,
        capture_output=True,
    )


def test_staging_persistent_inputs_select_only_the_staging_registry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "deployment.auto.tfvars"
    source = (INFRA / "deployment.staging.auto.tfvars.example").read_text()
    assert "cosmaneura-prod" not in source
    assert "azure_deid_service_id" not in source
    path.write_text(source)
    disabled = _run_input_validator(path, "staging")
    assert disabled.returncode == 0, disabled.stderr
    assert "staging disabled" in disabled.stdout

    path.write_text(
        source
        + "\nazure_deid_service_id = \"/subscriptions/"
        + "bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/cosmaneura-prod/"
        + "providers/Microsoft.HealthDataAIServices/deidServices/AnonymizerAPI\"\n"
    )
    shared_deid = _run_input_validator(path, "staging")
    assert shared_deid.returncode == 1
    assert (
        "staging forbids deployment inputs: azure_deid_service_id"
        in shared_deid.stderr
    )

    path.write_text(
        source.replace(
            "github_deploy_enabled   = false",
            "github_deploy_enabled   = null",
        )
    )
    invalid_github_gate = _run_input_validator(path, "staging")
    assert invalid_github_gate.returncode == 1
    assert "github_deploy_enabled must be literal true or false" in invalid_github_gate.stderr

    digest = "a" * 64
    enabled = (
        source.replace("live_apps_enabled       = false", "live_apps_enabled       = true")
        .replace(
            "key_vault_secrets_ready = false",
            "key_vault_secrets_ready = true",
        )
        .replace(
            'initial_image_ref        = ""',
            'initial_image_ref        = "cosmaneurasovereignstagingregistry.azurecr.io/'
            f'cosmaneura/sovereign@sha256:{digest}"',
        )
    )
    path.write_text(enabled)
    accepted = _run_input_validator(path, "staging")
    assert accepted.returncode == 0, accepted.stderr
    assert "staging enabled" in accepted.stdout

    path.write_text(enabled.replace("sovereignstagingregistry", "sovereignprodregistry"))
    refused = _run_input_validator(path, "staging")
    assert refused.returncode == 1
    assert "exact staging ACR" in refused.stderr

    path.write_text(enabled)
    wrong_boundary = _run_input_validator(path, "prod")
    assert wrong_boundary.returncode == 1
    assert "does not match requested prod boundary" in wrong_boundary.stderr


def test_staging_env_examples_use_only_separate_staging_secret_references(
    tmp_path: Path,
) -> None:
    for mount, name in (
        ("provider", ".env.staging.backend.example"),
        ("public", ".env.staging.public.example"),
    ):
        source = (ROOT / name).read_text()
        assert "op://Staging/" in source
        assert "op://Production/" not in source
        path = tmp_path / f"{mount}.env"
        path.write_text(
            source.replace(
                "your-staging-instance.clerk.accounts.dev",
                "staging-tenant.clerk.accounts.dev",
            )
        )
        result = subprocess.run(
            [
                sys.executable,
                SCRIPTS / "validate-deployment-env.py",
                "staging",
                mount,
                path,
            ],
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr

    provider = (ROOT / ".env.staging.backend.example").read_text()
    public = (ROOT / ".env.staging.public.example").read_text()
    assert "AZURE_DEID_ENDPOINT=" not in provider
    assert "op://Staging/sovereign-record-grant/credential" in provider
    assert "SOVEREIGN_RECORD_GRANT_SECRET" not in public
    assert "ATHENA_CLIENT_" not in public


def test_staging_env_rejects_production_and_cross_mount_secret_references(
    tmp_path: Path,
) -> None:
    validator = SCRIPTS / "validate-deployment-env.py"
    source = (ROOT / ".env.staging.public.example").read_text()
    path = tmp_path / "public.env"
    path.write_text(
        source.replace(
            "op://Staging/magisterium-public/credential",
            "op://Production/magisterium-public/credential",
        )
    )
    production = subprocess.run(
        [sys.executable, validator, "staging", "public", path],
        text=True,
        capture_output=True,
    )
    assert production.returncode == 1
    assert "exact staging public 1Password reference" in production.stderr
    assert "op://" not in production.stderr

    path.write_text(
        source.replace(
            "op://Staging/magisterium-public/credential",
            "op://Staging/magisterium-api/credential",
        )
    )
    crossed = subprocess.run(
        [sys.executable, validator, "staging", "public", path],
        text=True,
        capture_output=True,
    )
    assert crossed.returncode == 1
    assert "exact staging public 1Password reference" in crossed.stderr


def test_context_resolver_names_every_staging_resource_distinctly() -> None:
    script = (
        'source infra/scripts/deployment-context.sh; '
        'DEPLOYMENT_ENVIRONMENT=staging; load_deployment_context; '
        'printf "%s\\n" "$EXPECTED_TFSTATE_KEY" "$EXPECTED_RESOURCE_GROUP" '
        '"$EXPECTED_ACR_NAME" "$EXPECTED_PROVIDER_VAULT" "$EXPECTED_PUBLIC_VAULT" '
        '"$EXPECTED_PROVIDER_IDENTITY" "$EXPECTED_PUBLIC_IDENTITY" '
        '"$EXPECTED_PROVIDER_ENVIRONMENT" "$EXPECTED_PUBLIC_ENVIRONMENT" '
        '"$EXPECTED_PROVIDER_APP" "$EXPECTED_PUBLIC_APP"'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.splitlines() == [
        "sovereign-staging.tfstate",
        "cosmaneura-sovereign-staging",
        "cosmaneurasovereignstagingregistry",
        "cosmasovstagingprovider",
        "cosmasovstagingpublic",
        "cosmaneura-sovereign-staging-runtime",
        "cosmaneura-web-staging-runtime",
        "cosmaneura-sovereign-staging-app-environment",
        "cosmaneura-web-staging-app-environment",
        "cosmaneura-sovereign-staging-app",
        "cosmaneura-web-staging-app",
    ]


def test_backend_verifier_rejects_cross_environment_state(tmp_path: Path) -> None:
    metadata = tmp_path / ".terraform" / "terraform.tfstate"
    metadata.parent.mkdir()
    payload = {
        "backend": {
            "type": "azurerm",
            "config": {"key": "sovereign-staging.tfstate"},
        }
    }
    metadata.write_text(json.dumps(payload))
    accepted = subprocess.run(
        [sys.executable, SCRIPTS / "verify-backend-context.py", "staging", tmp_path],
        text=True,
        capture_output=True,
    )
    assert accepted.returncode == 0, accepted.stderr

    refused = subprocess.run(
        [sys.executable, SCRIPTS / "verify-backend-context.py", "prod", tmp_path],
        text=True,
        capture_output=True,
    )
    assert refused.returncode == 1
    assert "does not match the prod state boundary" in refused.stderr


def test_terraform_root_has_no_implicit_environment_and_pins_state_guard() -> None:
    providers = (INFRA / "providers.tf").read_text()
    variables = (INFRA / "variables.tf").read_text()
    main = (INFRA / "main.tf").read_text()
    assert 'environment           = var.deployment_environment' in providers
    assert 'key             = local.deployment_context.tfstate_key' in providers
    deployment_variable = variables.split(
        'variable "deployment_environment"', 1
    )[1].split('variable "github_repository"', 1)[0]
    assert "\n  default" not in deployment_variable
    assert 'contains(["prod", "staging"]' in deployment_variable
    assert 'resource "terraform_data" "state_environment_guard"' in main
    assert "triggers_replace = local.environment" in main
    assert "prevent_destroy = true" in main
    assert "depends_on = [terraform_data.state_environment_guard]" in main


def test_staging_owns_deid_and_production_is_data_only() -> None:
    providers = (INFRA / "providers.tf").read_text()
    deid = (INFRA / "deid.tf").read_text()
    variables = (INFRA / "variables.tf").read_text()
    main = (INFRA / "main.tf").read_text()

    assert 'source  = "Azure/azapi"' in providers
    assert 'version = "~> 2.12.0"' in providers
    assert 'data "azapi_resource" "production_deid"' in deid
    assert 'count = local.environment == "prod" ? 1 : 0' in deid
    assert 'resource "azapi_resource" "staging_deid"' in deid
    staging = deid.split(
        'resource "azapi_resource" "staging_deid"', 1
    )[1].split('locals {', 1)[0]
    assert 'count = local.environment == "staging" ? 1 : 0' in staging
    assert 'type      = "Microsoft.HealthDataAIServices/deidServices@2024-09-20"' in staging
    assert 'name      = "cosmaneura-staging-deid"' in staging
    assert 'parent_id = azurerm_resource_group.main.id' in staging
    assert 'location  = "eastus"' in staging
    assert 'type = "None"' in staging
    assert 'publicNetworkAccess = "Enabled"' in staging
    assert "prevent_destroy = true" in staging

    role = deid.split(
        'resource "azurerm_role_assignment" "sovereign_deid"', 1
    )[1]
    assert "scope                = local.azure_deid_service_id" in role
    assert 'role_definition_name = "DeID Realtime Data User"' in role
    assert "azurerm_user_assigned_identity.provider_runtime.principal_id" in role

    assert '"DEID_PROVIDER",' in variables
    assert '"DEID_PROVIDER", "AZURE_DEID_ENDPOINT"' not in variables
    assert 'name  = "AZURE_DEID_ENDPOINT"' in main
    assert "value = local.azure_deid_endpoint" in main


def test_foundation_parent_and_rollback_registry_refuse_targeted_destroy() -> None:
    main = (INFRA / "main.tf").read_text()
    resource_group = main.split(
        'resource "azurerm_resource_group" "main" {', 1
    )[1].split('resource "terraform_data" "state_environment_guard"', 1)[0]
    registry = main.split(
        'resource "azurerm_container_registry" "main" {', 1
    )[1].split('resource "azurerm_container_app_environment" "main"', 1)[0]

    # The state guard is a dependency of the resource group. OpenTofu can omit
    # dependencies from a targeted destroy, so each destructive root needs its
    # own lifecycle protection in the selected plan graph.
    for block in (resource_group, registry):
        assert "lifecycle {" in block
        assert "prevent_destroy = true" in block


def test_phase_two_runtime_roots_refuse_targeted_destroy_and_bound_staging_logs() -> None:
    main = (INFRA / "main.tf").read_text()
    key_vault = (INFRA / "key_vault.tf").read_text()
    providers = (INFRA / "providers.tf").read_text()

    for resource_type, name in (
        ("azurerm_log_analytics_workspace", "main"),
        ("azurerm_log_analytics_workspace", "public"),
        ("azurerm_container_app_environment", "main"),
        ("azurerm_container_app_environment", "public"),
    ):
        block = main.split(f'resource "{resource_type}" "{name}" {{', 1)[1].split(
            '\nresource "', 1
        )[0]
        assert "lifecycle {" in block
        assert "prevent_destroy = true" in block

    for name in ("provider_runtime", "public_runtime"):
        block = key_vault.split(
            f'resource "azurerm_user_assigned_identity" "{name}" {{', 1
        )[1].split('\nresource "', 1)[0]
        assert "lifecycle {" in block
        assert "prevent_destroy = true" in block

    workspace_prefix = 'resource "azurerm_log_analytics_workspace"'
    for name in ("main", "public"):
        block = main.split(f'{workspace_prefix} "{name}" {{', 1)[1].split(
            '\nresource "', 1
        )[0]
        assert 'sku                 = "PerGB2018"' in block
        assert "daily_quota_gb      = local.log_analytics_daily_quota_gb" in block

    assert (
        'log_analytics_daily_quota_gb = local.environment == "staging" ? 0.1 : -1'
        in providers
    )


def test_staging_github_deploy_identity_is_default_off_as_one_complete_unit() -> None:
    main = (INFRA / "main.tf").read_text()
    providers = (INFRA / "providers.tf").read_text()
    variables = (INFRA / "variables.tf").read_text()
    outputs = (INFRA / "outputs.tf").read_text()
    prod_inputs = (INFRA / "deployment.auto.tfvars.example").read_text()
    staging_inputs = (INFRA / "deployment.staging.auto.tfvars.example").read_text()

    assert "github_deploy_enabled_default = true" in providers
    assert "github_deploy_enabled_default = false" in providers
    assert "var.github_deploy_enabled == null" in providers
    github_variable = variables.split('variable "github_deploy_enabled"', 1)[1].split(
        'variable "key_vault_secrets_ready"', 1
    )[0]
    assert "default     = null" in github_variable
    assert "nullable    = true" in github_variable
    assert "github_deploy_enabled   = true" in prod_inputs
    assert "github_deploy_enabled   = false" in staging_inputs

    for resource_type, name in (
        ("azurerm_user_assigned_identity", "ghactions"),
        ("azurerm_federated_identity_credential", "ghactions"),
        ("azurerm_role_assignment", "github_acr_push"),
        ("azurerm_role_assignment", "github_acr_tasks"),
        ("azurerm_role_assignment", "github_env_operator"),
        ("azurerm_role_assignment", "github_public_env_operator"),
    ):
        block = main.split(f'resource "{resource_type}" "{name}" {{', 1)[1].split(
            '\nresource "', 1
        )[0]
        assert "count" in block
        assert "local.github_deploy_enabled ? 1 : 0" in block

    for name in ("github_app_operator", "github_public_app_operator"):
        block = main.split(f'resource "azurerm_role_assignment" "{name}" {{', 1)[
            1
        ].split('\nresource "', 1)[0]
        assert (
            "var.live_apps_enabled && local.github_deploy_enabled ? 1 : 0" in block
        )

    assert main.count("to   = azurerm_") == 6
    assert "azurerm_user_assigned_identity.ghactions[0]" in outputs
    assert "try(" in outputs


def test_phase_two_docs_block_stateful_logging_until_backend_recovery_is_approved() -> None:
    infra_readme = (INFRA / "README.md").read_text()
    rehearsal = (ROOT / "STAGING_REHEARSAL.md").read_text()
    for document in (infra_readme, rehearsal):
        assert "shared keys" in document
        assert "blob versioning" in document
        assert "soft delete" in document or "soft-delete" in document
    assert "github_deploy_enabled=false" in rehearsal


def test_all_new_staging_operator_scripts_are_executable_and_syntax_clean() -> None:
    scripts = [
        SCRIPTS / "init-environment.sh",
        SCRIPTS / "rehearse-staging-rollback.sh",
    ]
    for script in scripts:
        assert os.access(script, os.X_OK)
        result = subprocess.run(
            ["bash", "-n", script], text=True, capture_output=True
        )
        assert result.returncode == 0, result.stderr
    initializer = (SCRIPTS / "init-environment.sh").read_text()
    assert "validate-deployment-inputs.py" in initializer
    assert '"$DEPLOYMENT_ENVIRONMENT" >/dev/null' in initializer
    assert "init -reconfigure" in initializer


def test_deploy_checklist_commands_run_from_repository_root() -> None:
    checklist = (ROOT / "DEPLOY_CHECKLIST.md").read_text()
    assert "tofu -chdir=infra apply" in checklist
    assert "tofu -chdir=infra plan" in checklist
    assert "tofu -chdir=infra output" in checklist
    assert "./infra/scripts/verify-bootstrap-role-assignments.sh" in checklist
    assert "./infra/scripts/verify-live-app-boundaries.sh" in checklist
    assert "\n      tofu apply" not in checklist
    assert "`tofu plan`" not in checklist
    assert "`tofu output`" not in checklist
    assert "./scripts/" not in checklist
    assert "clone the `prod` root with a `staging` key/RG" not in checklist
