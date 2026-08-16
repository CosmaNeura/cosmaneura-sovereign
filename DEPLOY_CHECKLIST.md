# Deploy checklist — CosmaNeura Sovereign

The ordered, do-this-to-go-live list. Architecture/rationale is in
[`DEPLOY_AZURE.md`](./DEPLOY_AZURE.md); Terraform details in
[`infra/README.md`](./infra/README.md). Boxes are roughly in dependency order.

## 0. Prerequisites (local machine)
- [ ] `tofu`, `az`, `op` (1Password CLI), `curl`, `git`, `uv`, Python 3, and `tar`
      installed. Local Docker and `yq` are not required.
      The bootstrap script downloads Nixpacks 1.41.0 and verifies the published
      platform-specific SHA-256; it never trusts a PATH-installed builder.
- [ ] `az login` to the CosmaNeura subscription (`bab2a709-…`).
- [ ] `op` authenticated (or `OP_SERVICE_ACCOUNT_TOKEN` exported).

## 1. Secrets & config
- [ ] `cp .env.production.backend.example .env.production.backend`
- [ ] `cp .env.production.public.example .env.production.public`
- [ ] `cp infra/deployment.auto.tfvars.example infra/deployment.auto.tfvars`
      (leave its three fail-closed values unchanged during foundation bootstrap).
- [ ] Fill every key. Keep the listed Magisterium, CosmasCode, and provider-only
      Athena credentials as `op://` references; never put values in these files.
- [ ] Generate `SOVEREIGN_RECORD_GRANT_SECRET` exactly with
      `cryptography.fernet.Fernet.generate_key()` (44-character urlsafe-base64
      encoding of 32 random bytes), store its source in 1Password, and seed it
      only into the provider vault. The platform host receives the same value
      through its own secret boundary; it must be absent from the public mount.
- [ ] Run preflight: `./infra/scripts/preflight.sh`

## 2. Clerk identity (the cross-service contract — DEPLOY_AZURE.md §2)
- [ ] Create the Clerk JWT template named **`cosmaneura-sovereign`** with
      `aud = "cosmaneura-sovereign"` and template claim
      `roles: ["{{user.public_metadata.sovereign_role}}"]`.
- [ ] Set `AUTH_ROLE_CLAIM=roles` and `AUTH_PROVIDER_ROLE=provider`. An admin
      must set `user.public_metadata.sovereign_role` to `provider` only for the
      controlled pilot users. Never hard-code `provider` into every session.
- [ ] Set `OIDC_ISSUER` / `OIDC_JWKS_URL` to your Clerk instance values.
- [ ] Restrict issuance of the provider-role template to the one controlled Preview
      pilot tenant. The pinned resolver does not prove Clerk org membership;
      it is not authorized for real PHI or a multi-tenant EHR rollout.

## 3. Bootstrap ACR and Key Vaults
- [ ] `cd infra && tofu init`  (uses the existing azurerm state backend)
- [ ] Resolve the Entra object ID of the logged-in bootstrap operator. For a
      human account: `az ad signed-in-user show --query id -o tsv`.
- [ ] Target-apply the four temporary bootstrap assignments (their dependencies
      create the RG, ACR, and both vaults):
      ```bash
      tofu apply \
        -target=azurerm_role_assignment.bootstrap_acr_push \
        -target=azurerm_role_assignment.bootstrap_acr_tasks \
        -target=azurerm_role_assignment.bootstrap_provider_secrets \
        -target=azurerm_role_assignment.bootstrap_public_secrets \
        -target=azurerm_role_assignment.provider_acr_pull \
        -target=azurerm_role_assignment.public_acr_pull \
        -target=azurerm_role_assignment.provider_key_vault_secrets \
        -target=azurerm_role_assignment.public_key_vault_secrets \
        -target=azurerm_role_assignment.sovereign_deid \
        -var=bootstrap_operator_object_id=<entra-object-id>
      ```
- [ ] Run `BOOTSTRAP_OPERATOR_OBJECT_ID=<uuid>
      ./scripts/verify-bootstrap-role-assignments.sh`, then allow Azure
      data-plane propagation before continuing. The
      temporary operator assignments created here are `AcrPush` plus
      `Container Registry Tasks Contributor` on this ACR and the
      `Key Vault Secrets Officer` role on these two vaults; each
      runtime identity has its mount-specific pull/read role and provider alone
      has DeID access.
- [ ] Confirm `tofu plan` creates no Container Apps while
      `live_apps_enabled=false`; foundation bootstrap must not expose ingress.

## 4. First immutable image and vault seed
- [ ] From the repository root, queue the first remote ACR build and capture
      stdout. The script generates a normalized Dockerfile/context with the
      checksum-pinned Nixpacks binary; it never uses a local Docker daemon:
      ```bash
      INITIAL_IMAGE_REF=$(REGISTRY=cosmaneurasovereignprodregistry.azurecr.io \
        infra/scripts/build-bootstrap-image.sh)
      ```
- [ ] Seed each exact, tagged vault:
      ```bash
      infra/scripts/sync-key-vault-secrets.sh provider cosmasovprodprovider
      infra/scripts/sync-key-vault-secrets.sh public cosmasovprodpublic
      infra/scripts/verify-key-vault-boundaries.sh
      ```
- [ ] Review the full plan, including any resource destruction. If an earlier
      stack contains `partner-data`, export `providers.json` before approving
      its removal; this revision deliberately removes the shared Azure Files
      key/state and uses the bundled directory fixture.
- [ ] Verify applicable Azure BAA controls, the exact Clerk issuer/audience/role
      mapping, and vendor-owned synthetic Preview record mapping/grant issuer
      before creating provider ingress. This release is practice `195900`,
      Preview, synthetic-only.
- [ ] In ignored `infra/deployment.auto.tfvars`, set `live_apps_enabled=true`,
      `key_vault_secrets_ready=true`, and `initial_image_ref` to the captured
      immutable digest. Keep this file for every later plan and refresh its
      fallback digest to the current paired deployment before infrastructure
      changes. Losing it fails against `prevent_destroy`; it must never be
      reconstructed from an old bootstrap value.
- [ ] Create both apps on the same rollback baseline with a reviewed full
      `tofu plan`, then `tofu apply`. Omit `bootstrap_operator_object_id` so the
      temporary operator roles are removed in the same apply.
- [ ] Confirm the final plan/state contains no bootstrap role assignments.
- [ ] Run `./scripts/verify-live-app-boundaries.sh` and confirm each app has
      exactly its own UAI, vault references, and managed-identity registry bind.
- [ ] Capture outputs: `tofu output` → `gha_identity_client_id`, `tenant_id`,
      `container_registry`, both app names, and both vault names.

## 5. Wire the GitHub deployment foundation (keep deployment disabled)
Set these as **repository variables** (Settings → Secrets and variables →
Actions → Variables), so no cloud credentials are stored:
- [ ] `AZURE_CLIENT_ID` = `gha_identity_client_id` output
- [ ] `AZURE_TENANT_ID` = `tenant_id` output
- [ ] `AZURE_SUBSCRIPTION_ID` = `bab2a709-1fcb-4c87-a9fc-802f7477ec13`
- [ ] `CONTAINER_REGISTRY_HOST` = `container_registry` output (e.g.
      `cosmaneurasovereignprodregistry.azurecr.io`)
- [ ] (optional, defaults exist) `AZURE_RESOURCE_GROUP`,
      `AZURE_PROVIDER_CONTAINER_APP`, `AZURE_PUBLIC_CONTAINER_APP`
- [ ] Confirm `github_repository` / `github_branch` in `infra/variables.tf`
      match the real repo/branch (federated-credential subject), else OIDC fails.
- [ ] Do **not** store Clerk JWTs or record grants in GitHub. They are too
      short-lived to survive checkout/build/rollout and exceed the minimum
      secret scope. Keep `SOVEREIGN_DEPLOY_ENABLED=false`; the unattended job
      is intentionally unable to mutate either app until a reviewed smoke-time
      mint exchange exists.

## 6. Verify
- [ ] Build a local smoke environment from the hash locks:
      `uv venv --python 3.13 .deploy-venv && uv pip install --python
      .deploy-venv --require-hashes --only-binary=:all: -r requirements.lock`.
- [ ] For the reviewed operator rollout, export only the resource group, two app
      names, immutable `IMAGE_REF`, revision suffix, `SMOKE_PYTHON`, and
      `SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED=true`; then run
      `SOVEREIGN_INTERACTIVE_SMOKE_CREDENTIALS=true
      ./infra/scripts/deploy-dual.sh` from a private controlling terminal. It
      asks silently for fresh correct-role/wrong-role Clerk JWTs after both
      revisions are healthy, runs the live smoke, then asks for the newly minted
      60-second host grant immediately before labs. Any read/smoke failure rolls
      both apps back; no credential enters argv, history, or logs.
- [ ] Check both `/healthz` endpoints: public must report mount `public`, provider
      must report `provider_assistant`, and both must report `mock_mode=false`.
- [ ] Keep `SOVEREIGN_DEPLOY_ENABLED` false until the Azure/BAA, exact secret and
      identity boundaries, controlled synthetic mapping, Clerk claims/tokens,
      paired rollback gates, and smoke-time mint exchange pass. A main push
      cannot deploy; until that exchange exists, use only the reviewed operator
      transaction below.
- [ ] The operator deploy transaction runs `tools/smoke_live.py` and
      `tools/smoke_labs.py` before commit. Missing/expired/wrong-role tokens,
      public capability leakage, live model failure, or Preview labs failure
      rolls **both** mounts back to their captured immutable digests.
- [ ] In a non-production rehearsal, deploy a second immutable digest, force one
      mount's smoke to fail, and retain evidence that `deploy-dual.sh` restored
      both previous active digest revisions.
- [ ] Preserve evidence of provider-role success plus missing/wrong-role rejection.
      Body persona/capability hints must not change the pinned mount.

## 7. Before real PHI (provider mount) — non-negotiable
- [ ] Applicable Azure BAA controls verified before asserting `BAA_ATTESTED=true`.
- [ ] Azure de-identification behavior validated with human review.
- [ ] Platform central audit/reporting and its integrity/retention policy are a
      deferred V7/V8 production control. Local JSONL and Container logs are not
      durable; this does **not** block the tightly controlled synthetic Preview
      pilot, but it does block any claim of production audit durability.
- [ ] Production `ehr.labs.read` adapter/registry binding ratified and deployed;
      this release remains synthetic Athena Preview only.
- [ ] HIPAA counsel + clinical + doctrinal review (`SETUP_AND_HANDOFF.md §6`).
- [ ] Gateway rate limiting; pen test / threat model.

## Still not automated (follow-ups)
- Distinct public/provider custom domains (mirror `cosmaneura/infra/domains.tf`);
  never point both mount URLs at the same hostname.
- A `staging` environment (clone the `prod` root with a `staging` key/RG).

The pinned public and synthetic Preview provider mounts do not require the
platform Agent API or `PlatformResolver`. Real-PHI/multi-tenant EHR authority is
out of scope and remains prohibited until that future integration is complete.
