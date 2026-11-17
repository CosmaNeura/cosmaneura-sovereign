#!/usr/bin/env python3
"""Fail closed unless a plan is exactly the production GHA login bootstrap."""
from __future__ import annotations

import json
import sys
from typing import Any


RESOURCE_GROUP = "cosmaneura-sovereign-prod"
GHA_IDENTITY = "cosmaneura-sovereign-prod-gha-identity"
GITHUB_SUBJECT = (
    "repo:CosmaNeura/cosmaneura-sovereign:environment:production"
)
EXPECTED_RESOURCES = {
    "terraform_data.state_environment_guard",
    "azurerm_resource_group.main",
    "azurerm_user_assigned_identity.ghactions[0]",
    "azurerm_federated_identity_credential.ghactions[0]",
    "azurerm_role_assignment.github_resource_group_reader[0]",
}
EXPECTED_OUTPUTS = {
    "gha_identity_client_id",
    "resource_group",
    "tenant_id",
}


class PlanError(ValueError):
    """The saved plan exceeds or contradicts the bootstrap contract."""


def _expect(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise PlanError(f"{label}: expected {expected!r}, got {actual!r}")


def verify(plan: dict[str, Any]) -> None:
    if plan.get("errored") is True:
        raise PlanError("OpenTofu marked the plan as errored")
    if plan.get("resource_drift"):
        raise PlanError("the targeted bootstrap plan must contain no resource drift")

    changes = {
        item["address"]: item["change"]
        for item in plan.get("resource_changes", [])
        if item.get("change", {}).get("actions") != ["no-op"]
    }
    _expect(set(changes), EXPECTED_RESOURCES, "resource change allowlist")
    for address, change in changes.items():
        _expect(change.get("actions"), ["create"], f"{address} actions")

    after = {address: change["after"] for address, change in changes.items()}
    _expect(
        after["terraform_data.state_environment_guard"].get("triggers_replace"),
        "prod",
        "state guard environment",
    )

    resource_group = after["azurerm_resource_group.main"]
    _expect(resource_group.get("name"), RESOURCE_GROUP, "resource group name")
    _expect(resource_group.get("location"), "northcentralus", "resource group location")

    identity = after["azurerm_user_assigned_identity.ghactions[0]"]
    _expect(identity.get("name"), GHA_IDENTITY, "GHA identity name")
    _expect(identity.get("resource_group_name"), RESOURCE_GROUP, "GHA identity RG")

    federation = after["azurerm_federated_identity_credential.ghactions[0]"]
    _expect(federation.get("name"), "github-actions", "federation name")
    _expect(
        federation.get("issuer"),
        "https://token.actions.githubusercontent.com",
        "federation issuer",
    )
    _expect(
        federation.get("audience"),
        ["api://AzureADTokenExchange"],
        "federation audience",
    )
    _expect(federation.get("subject"), GITHUB_SUBJECT, "federation subject")
    _expect(
        federation.get("resource_group_name"),
        RESOURCE_GROUP,
        "federation RG",
    )

    reader = after["azurerm_role_assignment.github_resource_group_reader[0]"]
    _expect(reader.get("role_definition_name"), "Reader", "bootstrap role")
    _expect(reader.get("principal_type"), "ServicePrincipal", "principal type")

    configured_resources = {
        item["address"]: item
        for item in plan.get("configuration", {})
        .get("root_module", {})
        .get("resources", [])
    }
    reader_config = configured_resources.get(
        "azurerm_role_assignment.github_resource_group_reader"
    )
    if reader_config is None:
        raise PlanError("Reader configuration is absent from the saved plan")
    _expect(
        reader_config.get("expressions", {}).get("scope", {}).get("references"),
        ["azurerm_resource_group.main.id", "azurerm_resource_group.main"],
        "Reader scope reference",
    )

    output_changes = plan.get("output_changes", {})
    _expect(set(output_changes), EXPECTED_OUTPUTS, "output change allowlist")
    _expect(
        output_changes["resource_group"].get("after"),
        RESOURCE_GROUP,
        "resource_group output",
    )
    for output in ("gha_identity_client_id", "tenant_id"):
        _expect(
            output_changes[output].get("after_unknown"),
            True,
            f"{output} must be computed by Azure",
        )


def main() -> int:
    try:
        plan = json.load(sys.stdin)
        verify(plan)
    except (AttributeError, json.JSONDecodeError, KeyError, PlanError, TypeError) as error:
        print(f"production GHA bootstrap plan rejected: {error}", file=sys.stderr)
        return 1
    print("verified production GHA bootstrap plan: 5 creates, 0 changes, 0 destroys")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
