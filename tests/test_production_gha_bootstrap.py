"""Safety contracts for the production resource-group/OIDC bootstrap plan."""
from __future__ import annotations

import json
import stat
import subprocess
import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
PLANNER = INFRA / "scripts" / "plan-production-gha-bootstrap.sh"
VERIFIER = INFRA / "scripts" / "verify-production-gha-bootstrap-plan.py"


def _valid_plan() -> dict[str, object]:
    resources = {
        "terraform_data.state_environment_guard": {
            "triggers_replace": "prod",
        },
        "azurerm_resource_group.main": {
            "name": "cosmaneura-sovereign-prod",
            "location": "northcentralus",
        },
        "azurerm_user_assigned_identity.ghactions[0]": {
            "name": "cosmaneura-sovereign-prod-gha-identity",
            "resource_group_name": "cosmaneura-sovereign-prod",
        },
        "azurerm_federated_identity_credential.ghactions[0]": {
            "name": "github-actions",
            "issuer": "https://token.actions.githubusercontent.com",
            "audience": ["api://AzureADTokenExchange"],
            "subject": (
                "repo:CosmaNeura/cosmaneura-sovereign:environment:production"
            ),
            "resource_group_name": "cosmaneura-sovereign-prod",
        },
        "azurerm_role_assignment.github_resource_group_reader[0]": {
            "role_definition_name": "Reader",
            "principal_type": "ServicePrincipal",
        },
    }
    return {
        "resource_changes": [
            {
                "address": address,
                "change": {"actions": ["create"], "after": after},
            }
            for address, after in resources.items()
        ],
        "output_changes": {
            "gha_identity_client_id": {"after_unknown": True},
            "resource_group": {"after": "cosmaneura-sovereign-prod"},
            "tenant_id": {"after_unknown": True},
        },
        "configuration": {
            "root_module": {
                "resources": [
                    {
                        "address": (
                            "azurerm_role_assignment.github_resource_group_reader"
                        ),
                        "expressions": {
                            "scope": {
                                "references": [
                                    "azurerm_resource_group.main.id",
                                    "azurerm_resource_group.main",
                                ]
                            }
                        },
                    }
                ]
            }
        },
    }


def _verify(plan: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, VERIFIER],
        input=json.dumps(plan),
        text=True,
        capture_output=True,
    )


def test_bootstrap_plan_contract_accepts_only_the_exact_create_set() -> None:
    result = _verify(_valid_plan())
    assert result.returncode == 0, result.stderr
    assert "5 creates, 0 changes, 0 destroys" in result.stdout


def test_bootstrap_plan_contract_rejects_scope_expansion() -> None:
    plan = _valid_plan()
    plan["resource_changes"].append(  # type: ignore[union-attr]
        {
            "address": "azurerm_container_registry.main",
            "change": {"actions": ["create"], "after": {}},
        }
    )
    result = _verify(plan)
    assert result.returncode == 1
    assert "resource change allowlist" in result.stderr


def test_bootstrap_plan_contract_rejects_wrong_oidc_subject() -> None:
    plan = deepcopy(_valid_plan())
    changes = plan["resource_changes"]
    federation = next(  # type: ignore[union-attr]
        item
        for item in changes
        if item["address"]
        == "azurerm_federated_identity_credential.ghactions[0]"
    )
    federation["change"]["after"]["subject"] = "repo:other/repo:environment:prod"
    result = _verify(plan)
    assert result.returncode == 1
    assert "federation subject" in result.stderr


def test_bootstrap_plan_contract_rejects_broader_reader_scope() -> None:
    plan = deepcopy(_valid_plan())
    resources = plan["configuration"]["root_module"]["resources"]  # type: ignore[index]
    resources[0]["expressions"]["scope"]["references"] = [  # type: ignore[index]
        "data.azurerm_subscription.current.id"
    ]
    result = _verify(plan)
    assert result.returncode == 1
    assert "Reader scope reference" in result.stderr


def test_planner_is_non_applying_and_targets_only_login_bootstrap() -> None:
    source = PLANNER.read_text()
    assert PLANNER.stat().st_mode & stat.S_IXUSR
    assert "tofu -chdir=\"$INFRA\" apply" not in source
    assert source.count("-target=") == 2
    assert "azurerm_federated_identity_credential.ghactions[0]" in source
    assert "azurerm_role_assignment.github_resource_group_reader[0]" in source
    assert "verify-production-gha-bootstrap-plan.py" in source


def test_gha_identity_has_only_resource_group_reader_for_login_bootstrap() -> None:
    main = (INFRA / "main.tf").read_text()
    block = main.split(
        'resource "azurerm_role_assignment" "github_resource_group_reader" {',
        1,
    )[1].split('\nresource "', 1)[0]
    assert "scope                = azurerm_resource_group.main.id" in block
    assert 'role_definition_name = "Reader"' in block
    assert 'principal_type       = "ServicePrincipal"' in block
    assert "count                = local.github_deploy_enabled ? 1 : 0" in block
