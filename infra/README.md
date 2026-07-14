# infra/ — Sovereign on Azure Container Apps

Standalone OpenTofu root that deploys the Sovereign as an **isolated**
microservice: its own resource group, Container App Environment, registry, and
Log Analytics — separate from the main app (`../../cosmaneura/infra`). Rationale
and the full design are in [`../DEPLOY_AZURE.md`](../DEPLOY_AZURE.md).

It deliberately mirrors the main app's conventions (env split into
`*_public_vars` / `*_secret_vars`, secrets resolved via `data.external`,
SHA-pinned images deployed by CI with Terraform ignoring the live image).

## What it creates

| Resource | Name |
|---|---|
| Resource group | `cosmaneura-sovereign-prod` |
| Log Analytics workspace | `cosmaneura-sovereign-prod-logs` |
| Container registry (ACR) | `cosmaneurasovereignprodregistry` |
| Container App Environment | `cosmaneura-sovereign-prod-app-environment` |
| Container App (FastAPI) | `cosmaneura-sovereign-prod-app` |
| GitHub OIDC identity + role assignments | `cosmaneura-sovereign-prod-gha-identity` |
| Storage account + Azure Files share (partner data) | `cosmasovproddata` / `partner-data` |
| Scheduled scraper Job (weekly) | `cosmaneura-sovereign-prod-scraper` |

State lives in the **existing** `cosmaneuratfstate` storage account under key
`sovereign-prod.tfstate`, so no new backend bootstrap is needed.

## Prerequisites

- `tofu`, `az` (logged in: `az login`), `yq`, and the 1Password CLI `op`
  (authenticated via `OP_SERVICE_ACCOUNT_TOKEN` in CI, or interactively locally).
- `cp ../.env.production.backend.example ../.env.production.backend` and fill it
  in. Every key in `variables.tf` (`sovereign_public_vars` +
  `sovereign_secret_vars`) must be present; secrets are `op://` references.

## Deploy

```bash
cd infra
tofu init                       # uses the azurerm backend (needs az login)
tofu apply                      # creates the infra; the app starts on :latest
```

The container starts on the registry's `:latest` tag. Build & push the first
image, then deploy a SHA-pinned revision (same shape as the main app's
`build_and_publish.yml`):

```bash
# build via nixpacks (Python auto-detected from requirements.txt; Procfile -> web)
# push to ACR as cosmaneura/sovereign:<sha>, then:
az containerapp update -n cosmaneura-sovereign-prod-app \
  -g cosmaneura-sovereign-prod \
  --set-image <registry>/cosmaneura/sovereign:<sha>
```

Inspect the resolved env locally (does not touch Azure):

```bash
./scripts/export-env.sh ../.env.production.backend | jq .
```

## Partner-directory data (no DB)

`data_refresh.tf` provisions a weekly Container Apps **Job** that runs
`tools/scrape_mycatholicdoctor.py` (MyCatholicDoctor has no API) and writes
`providers.json` to an Azure Files share. The service mounts the **same** share
read-only at `/data` and serves it via `PARTNER_DIRECTORY_PATH=/data/providers.json`
— so it stays stateless and picks up refreshes live. Until the first job run the
share is empty and the service falls back to the bundled fixture.

Trigger a refresh on demand (instead of waiting for the schedule):

```bash
az containerapp job start -n cosmaneura-sovereign-prod-scraper -g cosmaneura-sovereign-prod
```

## Still manual (next steps, not in this root yet)

- **CI workflow.** Clone the main app's `build_and_publish.yml` into this repo;
  point it at image repo `cosmaneura/sovereign` and this RG/app. Wire the GitHub
  repo `vars`: `AZURE_CLIENT_ID` = `gha_identity_client_id` output,
  `AZURE_TENANT_ID` = `tenant_id` output, registry host = ACR login server.
- **GitHub repo subject.** `github_repository` / `github_branch` (variables.tf)
  must match the real repo and branch, or OIDC token exchange fails.
- **Custom domain.** Bind `sovereign.cosmaneura.com` via
  `azurerm_container_app_custom_domain` + a managed cert (mirror
  `../../cosmaneura/infra/domains.tf`); needs the DNS records created first.
- **HIPAA hardening (provider build).** Raise Log Analytics retention, point the
  audit sink at a WORM blob store, swap the de-id baseline — see
  `../SETUP_AND_HANDOFF.md §5`.
