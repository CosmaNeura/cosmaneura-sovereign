# infra/ — Sovereign on Azure Container Apps

Standalone, explicitly environment-selected OpenTofu root that deploys the Sovereign as an **isolated**
microservice: its own resource group and registry plus separate provider/public
Container App Environments, identities, Key Vaults, and Log Analytics workspaces.
It is separate from the main app (`../../cosmaneura/infra`). Rationale
and the full design are in [`../DEPLOY_AZURE.md`](../DEPLOY_AZURE.md).

Terraform admits only a closed non-secret app-configuration allowlist. Runtime
application credentials are seeded from 1Password into mount-specific Key
Vaults and never enter Terraform state. The backend remains sensitive because
AzureRM can store provider-computed attributes such as Log Analytics shared
keys. Images are immutable digests and paired deployment owns the live image
after bootstrap.

## What it creates

| Resource | Name |
|---|---|
| Resource group | `cosmaneura-sovereign-<prod\|staging>` |
| Provider/public Log Analytics | `cosmaneura-sovereign-<env>-logs` / `cosmaneura-web-<env>-logs` |
| Container registry (ACR) | `cosmaneurasovereign<env>registry` |
| Provider/public environments | `cosmaneura-sovereign-<env>-app-environment` / `cosmaneura-web-<env>-app-environment` |
| Provider/public apps | `cosmaneura-sovereign-<env>-app` / `cosmaneura-web-<env>-app` |
| Provider/public Key Vaults | `cosmasov<env>provider` / `cosmasov<env>public` |
| GitHub OIDC identity + role assignments | `cosmaneura-sovereign-<env>-gha-identity` |

State lives in the **existing** `cosmaneuratfstate` storage account under an
environment-exact key: `sovereign-prod.tfstate` or
`sovereign-staging.tfstate`. There is no default environment. Always initialize
with `./scripts/init-environment.sh <prod|staging>`; the script and preflight
prove the backend key, and a prevent-destroy state guard rejects switching an
initialized state to the other environment.

## Prerequisites

- `tofu`, `az`, `curl`, `git`, `uv`, Python 3, `tar`, and the 1Password CLI `op`;
  authenticate the operator to Azure, ACR, and 1Password. Bootstrap downloads
  one supported Nixpacks 1.41.0 release asset, verifies its published SHA-256,
  normalizes its Dockerfile/context, and queues `az acr build`. Local Docker and
  `yq` are not required.
- Copy the two env examples for the selected boundary. Non-secret allowlisted values are literals;
  runtime secret sources remain `op://` references for the Key Vault sync.
  The canonical 44-character Fernet record-grant key is provider-only and must
  never appear in the public env, vault, app, Terraform inputs, or command
  arguments.
- Copy `deployment.auto.tfvars.example` (production) or
  `deployment.staging.auto.tfvars.example` to ignored
  `deployment.auto.tfvars` and
  leave its fail-closed values unchanged while bootstrapping foundations.

## Deploy

1. Resolve the logged-in operator's Entra object ID and target-apply the four
   `bootstrap_*` assignments plus `provider_acr_pull`, `public_acr_pull`, both
   `*_key_vault_secrets` assignments, and `sovereign_deid`, with
   `-var=bootstrap_operator_object_id=<uuid>`. Their dependencies create the RG,
   ACR, vaults, and both runtime identities; the apps cannot be created yet.
   Confirm all assignments are visible and allow Azure data-plane propagation
   before continuing.
2. Build the first digest: `DEPLOYMENT_ENVIRONMENT=<prod|staging>
   REGISTRY=<name>.azurecr.io
   ./scripts/build-bootstrap-image.sh`. Capture its single stdout line.
3. Seed the exact provider and public vaults with
   `./scripts/sync-key-vault-secrets.sh <prod|staging> <mount> <vault-name>`, then run
   `./scripts/verify-key-vault-boundaries.sh`. It requires the exact closed
   inventory and proves the public vault has no Athena credential metadata.
4. After BAA, Clerk, exact role/secret boundaries, and synthetic-record gates
   pass, persist `live_apps_enabled=true`, `key_vault_secrets_ready=true`, and
   the immutable `initial_image_ref` in `deployment.auto.tfvars`. Review the full
   plan, then apply without the bootstrap operator variable so temporary roles
   are removed. Run `verify-live-app-boundaries.sh` after creation.

There is no `:latest` bootstrap. Both newly created apps start on the same
reviewed immutable digest, which gives the paired deployer a safe rollback
baseline. The service uses its bundled partner-directory fixture; the former
cross-mount Azure Files refresh path was removed because it put a storage access
key in Terraform state.

Keep `deployment.auto.tfvars` for every later infrastructure plan and refresh
its fallback digest to the current paired release. Both apps have
`prevent_destroy`; an absent/disabled gate cannot silently recreate ingress on
an old image. Image release is owned by the paired deployer, whose functional
smokes run before rollback state is discarded. Until a smoke-time credential
mint exchange exists, the unattended workflow remains disabled and releases
run from a reviewed private operator terminal.

## Still manual

- **GitHub vars.** Wire `AZURE_CLIENT_ID` = `gha_identity_client_id` output,
  `AZURE_TENANT_ID` = `tenant_id` output, registry host = ACR login server.
- **GitHub OIDC subject.** `github_repository` must match the real repo. The
  federated credential is scoped to the `production` GitHub Environment; that
  Environment must exist and restrict deployment branches to `main`.
- **Custom domain.** Bind `sovereign.cosmaneura.com` via
  `azurerm_container_app_custom_domain` + a managed cert (mirror
  `../../cosmaneura/infra/domains.tf`); needs the DNS records created first.
- **Audit integrity.** V7/V8 central durability is deferred and must be reported;
  local JSONL and Container logs are not durable audit evidence. It is not a
  dependency for the controlled synthetic Preview pilot.

These pinned public/provider processes do not call the platform Agent API.
Real-PHI and multi-tenant EHR authority remain prohibited and out of scope.

The full non-production live-smoke and forced paired-rollback procedure is in
[`../STAGING_REHEARSAL.md`](../STAGING_REHEARSAL.md).
