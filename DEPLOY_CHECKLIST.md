# Deploy checklist — CosmaNeura Sovereign

The ordered, do-this-to-go-live list. Architecture/rationale is in
[`DEPLOY_AZURE.md`](./DEPLOY_AZURE.md); Terraform details in
[`infra/README.md`](./infra/README.md). Boxes are roughly in dependency order.

## 0. Prerequisites (local machine)
- [ ] `tofu`, `az`, `yq`, `op` (1Password CLI), `nixpacks`, `docker` installed.
- [ ] `az login` to the CosmaNeura subscription (`bab2a709-…`).
- [ ] `op` authenticated (or `OP_SERVICE_ACCOUNT_TOKEN` exported).

## 1. Secrets & config
- [ ] `cp .env.production.backend.example .env.production.backend`
- [ ] Fill every key. Secrets are `op://` references; create the 1Password items:
      `MAGISTERIUM_API_KEY`, `ANTHROPIC_API_KEY` (a **BAA-covered** key for the
      provider build).
- [ ] Verify it resolves: `./infra/scripts/export-env.sh .env.production.backend | jq 'keys'`
- [ ] Run preflight: `./infra/scripts/preflight.sh`

## 2. Clerk identity (the cross-service contract — DEPLOY_AZURE.md §2)
- [ ] Create a **JWT template** in Clerk that stamps `aud = OIDC_AUDIENCE`
      (`cosmaneura-sovereign`).
- [ ] Confirm which claim carries the clinician role → set `AUTH_ROLE_CLAIM`
      and `AUTH_PROVIDER_ROLE` in the env file.
- [ ] Set `OIDC_ISSUER` / `OIDC_JWKS_URL` to your Clerk instance values.

## 3. Provision infrastructure
- [ ] `cd infra && tofu init`  (uses the existing azurerm state backend)
- [ ] `tofu plan` → review (new RG, ACR, env, container app, GHA identity)
- [ ] `tofu apply`
- [ ] Capture outputs: `tofu output` → you need `gha_identity_client_id`,
      `tenant_id`, `container_registry`.

## 4. Wire GitHub for CI deploys (.github/workflows/build_and_publish.yml)
Set these as **repository variables** (Settings → Secrets and variables →
Actions → Variables), so no cloud credentials are stored:
- [ ] `AZURE_CLIENT_ID` = `gha_identity_client_id` output
- [ ] `AZURE_TENANT_ID` = `tenant_id` output
- [ ] `AZURE_SUBSCRIPTION_ID` = `bab2a709-1fcb-4c87-a9fc-802f7477ec13`
- [ ] `CONTAINER_REGISTRY_HOST` = `container_registry` output (e.g.
      `cosmaneurasovereignprodregistry.azurecr.io`)
- [ ] (optional, defaults exist) `AZURE_RESOURCE_GROUP`, `AZURE_CONTAINER_APP`
- [ ] Confirm `github_repository` / `github_branch` in `infra/variables.tf`
      match the real repo/branch (federated-credential subject), else OIDC fails.

## 5. First image
The container app boots on `:latest`, which doesn't exist until the first push.
- [ ] Push to `main` → the workflow builds, pushes, and deploys. **Or** seed it
      manually once:
      ```bash
      az acr login --name cosmaneurasovereignprodregistry
      nixpacks build . --name <registry>/cosmaneura/sovereign:bootstrap --platform linux/amd64
      docker tag <registry>/cosmaneura/sovereign:bootstrap <registry>/cosmaneura/sovereign:latest
      docker push <registry>/cosmaneura/sovereign:latest
      ```

## 6. Verify
- [ ] `FQDN=$(cd infra && tofu output -raw ingress_fqdn)`
- [ ] `curl -s https://$FQDN/healthz`
- [ ] Mint/obtain a real Clerk token and POST `/chat` with
      `Authorization: Bearer <token>` — confirm persona derives from the token,
      not the body (see `tools/clerk_oidc_local_demo.py` for the contract).

## 7. Before real PHI (provider build) — non-negotiable
- [ ] Anthropic **BAA + ZDR** executed (or Bedrock/Vertex) → set `BAA_ATTESTED=true`.
- [ ] Production de-identification swapped in (`compliance/phi.py` baseline → Azure
      AI Language / Comprehend Medical) + human review.
- [ ] Audit sink → **WORM** blob store; raise Log Analytics retention.
- [ ] HIPAA counsel + clinical + doctrinal review (`SETUP_AND_HANDOFF.md §6`).
- [ ] Gateway rate limiting; pen test / threat model.

## Still not automated (follow-ups)
- Custom domain `sovereign.cosmaneura.com` (mirror `cosmaneura/infra/domains.tf`).
- A `staging` environment (clone the `prod` root with a `staging` key/RG).
