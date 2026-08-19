# Deploy: CosmaNeura Sovereign as a microservice on Azure

How `cosmaneura-sovereign` plugs into the existing CosmaNeura Azure stack as an
**independent microservice**, and the **identity contract** between it and the
main app.

This document is design + decisions, not a runbook. It assumes the system
described in [`ARCHITECTURE.md`](./ARCHITECTURE.md) and the go-live tasks in
[`SETUP_AND_HANDOFF.md`](./SETUP_AND_HANDOFF.md). Where it says "the main app" it
means the `cosmaneura/` repo (FastAPI + Celery + React Router on Azure Container
Apps).

---

## 0. TL;DR

- Ship the Sovereign as its **own Azure Container App**, deployed by its **own
  pipeline** — same nixpacks → ACR → `az containerapp update` pattern the main
  app already uses.
- **Two deployments, one image:** an anonymous `public` mount and an authenticated
  `provider_assistant` mount. Their deployment-pinned mount profiles, not a body
  persona, determine capabilities and PHI posture.
- **The Clerk seam is implemented but not provisioned:** the provider host asks
  Clerk for a named JWT template and Sovereign validates issuer, audience, and a
  provider role claim. The template, exact claim mapping, and real-token smoke
  test remain operator work; do not enable the provider mount before they pass.
- **Pinned resolver for this pilot:** these two processes resolve their fixed
  public/provider mount in server deployment config. The platform Agent API is
  not a dependency for the synthetic Preview rollout; real-PHI/multi-tenant EHR
  authorization remains prohibited and out of scope.
- **PHI boundary drives the topology:** both mounts live in the dedicated
  Sovereign resource group, with separate Container App Environments, Log
  Analytics workspaces, runtime identities, and Key Vaults.

---

## 1. Where it sits in the current stack

The main app today (`cosmaneura/infra/main.tf`) is a single
`azurerm_container_app_environment` with four container apps sharing one ACR,
Postgres Flexible Server, Redis, and Log Analytics workspace:

```
Resource Group: cosmaneura
  Container App Environment (main)
    ├─ <rg>-app        FastAPI api   (ingress :80 → api.cosmaneura.com)
    ├─ <rg>-scheduler  celery beat
    ├─ <rg>-worker     celery worker (no ingress)
    └─ <rg>-job_monitor flower
  ACR · Postgres · Redis · Log Analytics · Storage (audio/video = PHI)
  Custom domains (domains.tf): main (api) + chat (intake)
```

The Sovereign is an HTTP service (`POST /chat`, `GET /healthz`). It has no local
Celery, Postgres, or Redis dependency. Canonical conversation and audit state are
platform-owned; the local file audit sink is not a production ledger.

### Decision: which environment does it join?

| Mount | Accepts PHI | Placement | Why |
|---|---|---|---|
| **public** | No authority to read EHR/PHI | Dedicated public environment, workspace, identity, and vault in the Sovereign RG | Prevents provider credentials and PHI-adjacent logs from crossing into the anonymous surface. |
| **provider_assistant** | Yes, only under its fixed capabilities and verified actor | Dedicated provider environment, workspace, identity, and vault in the Sovereign RG | Keeps the HIPAA blast radius off the main stack and the public mount. |

```
Resource Group: cosmaneura-sovereign           ← NEW, isolated for HIPAA
  Container App Environment (provider) → provider app + provider logs/vault
  Container App Environment (public)   → public app + public logs/vault
  Dedicated ACR with admin credentials disabled
```

---

## 2. The identity contract (Clerk ↔ Sovereign OIDC)

This is the integration seam between the two services, and it is the most
important thing to get right. The Sovereign already supports it natively — there
is **no IdP to stand up**.

### How it works

The main app uses Clerk for identity. Clerk can mint a template-specific JWT;
Sovereign's provider deployment validates its RS256 signature against the
configured JWKS plus issuer, audience, expiry, and provider role. The public
deployment does not use this lane.

```
  Browser (web/, Clerk session)
        │  fetch sovereign token (Clerk JWT template)
        ▼
  Authorization: Bearer <clerk-jwt>
        │
        ▼
  Sovereign /chat  ──verify──▶ Clerk JWKS (RS256, iss, aud, exp)
        │
        └─ verified actor + role must match the pinned provider mount
```

A token cannot elevate the deployment by sending a persona, capability, surface,
tenant, or actor hint in the body. The pinned mount context is authoritative.

### What to configure

Create the Clerk JWT template named **`cosmaneura-sovereign`**. Set
`aud = "cosmaneura-sovereign"` and the template claim to
`roles: ["{{user.public_metadata.sovereign_role}}"]`. An administrator sets
`user.public_metadata.sovereign_role = "provider"` only for approved pilot
users; never stamp a static provider role into every session. Then configure:

| Sovereign var | Value | Source |
|---|---|---|
| `AUTH_MODE` | `oidc` | — |
| `OIDC_ISSUER` | your Clerk Frontend API / instance issuer URL | Clerk dashboard |
| `OIDC_JWKS_URL` | Clerk instance JWKS URL | Clerk dashboard |
| `OIDC_AUDIENCE` | `cosmaneura-sovereign` | canonical template audience |
| `OIDC_ALGORITHMS` | `["RS256"]` | Clerk signs RS256 |
| `AUTH_ROLE_CLAIM` | `roles` | canonical template claim |
| `AUTH_PROVIDER_ROLE` | `provider` | admin-managed `sovereign_role` value |
| `ALLOW_PROVIDER_PERSONA` | `false` | legacy body-persona elevation stays disabled |

For the isolated staging rehearsal, the accepted non-secret authority is
`https://resolved-emu-53.clerk.accounts.dev` and its JWKS URL is
`https://resolved-emu-53.clerk.accounts.dev/.well-known/jwks.json`. Those
staging values do not apply to production; production must use its own reviewed
Clerk instance while preserving the audience/claim/provider values above.

The current `Doctor` row carries `clerk_id` but has no canonical organization
spine. Do not infer a provider role from that row alone. Provision an exact Clerk
template/claim and verify it against the authenticated, doctor-scoped platform
request before enabling the host integration.

The interim pinned resolver verifies a generic provider role and fixes the
tenant from deployment config; it does not prove the Clerk subject belongs to
that tenant. Restrict template issuance to the one controlled synthetic pilot.
Do not use this seam for a multi-tenant or real-PHI deployment.

### Clerk checks before activation
- Verify the host requests `getToken({template: "cosmaneura-sovereign"})`.
- Prove an admin-managed `sovereign_role=provider` user succeeds, a valid user
  with a different/missing value is denied, and no caller can self-edit that
  metadata. The deploy transaction repeats correct-role and wrong-role checks.

> Token verification is necessary but not sufficient. This rollout is limited
> to vendor-owned synthetic Athena Preview practice `195900`. The pinned pilot
> requires no platform mount-context call, but it grants no real-PHI or
> multi-tenant production authority.

---

## 3. Terraform and Key Vault bootstrap

The checked-in root creates physically separate provider/public Container App
environments, Log Analytics workspaces, user-assigned runtime identities, and
Key Vaults. ACR admin credentials are disabled. Each app pulls with its own
managed identity and can read secrets only from its mount-specific vault.

Terraform stores no runtime application/1Password credential values: it keeps
only non-secret app configuration and versionless Key Vault URIs, and never
creates `azurerm_key_vault_secret` resources. The backend must still be treated
as sensitive because AzureRM records provider-computed attributes such as Log
Analytics shared keys. Bootstrap in four explicit steps:

Phase 2 remains blocked until recovery protections for the shared state account
are separately approved. Creating either workspace writes its sensitive shared
keys into state; enable blob versioning and soft delete before that apply. This
deployment root cannot safely change the backend account because other stacks
share it. Staging also caps each `PerGB2018` workspace at 0.1 GB/day, while
production keeps its existing unlimited quota pending a separate retention and
availability decision.

1. Target-plan the four conditional operator assignments plus both runtime
   identities' ACR/Key Vault roles, the provider DeID role, and the staging-only
   `azapi_resource.staging_deid`. Their dependencies create the resource group,
   ACR, vaults, identities, and isolated staging DeID service. Apply only the
   exact reviewed plan. Confirm all expected assignments are visible and allow
   Azure data-plane propagation before continuing; the operator's scoped ACR
   push/task and vault-write roles are bootstrap-only.
2. Run `DEPLOYMENT_ENVIRONMENT=<prod|staging> REGISTRY=<name>.azurecr.io
   infra/scripts/build-bootstrap-image.sh` and capture the returned immutable
   `@sha256` reference. It checksum-verifies Nixpacks, generates and normalizes
   the locked Dockerfile/context, then queues a remote ACR build; no local
   Docker daemon or `:latest` tag is used.
3. Copy the two production env examples, retain their `op://` references, and
   run `infra/scripts/sync-key-vault-secrets.sh prod provider <vault>` and then
   `... prod public <vault>`. The script uses temporary mode-0600 files and never
   places a value on the command line or in Terraform state. Run
   `verify-key-vault-boundaries.sh` afterward; it permits only the exact
   mount-specific secret-name inventories and fails if Athena metadata exists in
   the public vault.
4. Copy `deployment.auto.tfvars.example` to the ignored
   `deployment.auto.tfvars`. After BAA, Clerk, boundary, and synthetic-record
   gates pass, set `live_apps_enabled=true`, `key_vault_secrets_ready=true`, and
   persist the captured `initial_image_ref`. Review the full plan and apply;
   omit the bootstrap object ID so Terraform removes temporary operator roles.
   The default plan creates no externally reachable apps. `prevent_destroy`
   stops a missing/stale input file from replacing either app on an old digest;
   refresh the persisted fallback digest before every later infrastructure plan.

The two mounts use HTTP `/healthz` probes. Terraform ignores only the live image
field; image lifecycle belongs to the paired deployer.

---

## 4. CI/CD

The release path builds once, resolves one immutable ACR digest, and passes it
to `infra/scripts/deploy-dual.sh`. The script captures both
previous immutable images, updates provider and public with unique run/attempt
revision suffixes, checks live identity/vault bindings and `/healthz`, then runs
missing/wrong-role/provider-role OIDC, public capability-denial, live model, and one
host-bound synthetic Preview labs check. It restores **both** old images if any
update or functional smoke fails.

Deployment remains default-off behind the repository variable
`SOVEREIGN_DEPLOY_ENABLED=true` and runs only by `workflow_dispatch` from main.
Do not enable that unattended lane yet: Clerk JWTs and the 60-second host grant
cannot safely be persisted before checkout/build/rollout, so the workflow has no
such secrets and fails before mutation. The reviewed operator path runs
`deploy-dual.sh` from a private terminal; it requests the two fresh Clerk JWTs
after both revisions are healthy, then requests the newly minted record grant
immediately before labs. The prompts are silent and remain inside the paired
rollback window; Clerk-token reads time out after 120 seconds and the grant read
after 45 seconds so an abandoned terminal triggers paired rollback. A future
smoke-time mint exchange may replace this manual step.

The Azure GitHub identity is independently controlled by
`github_deploy_enabled`. Its environment default is `true` for backward-
compatible production plans and `false` for staging. When disabled, Terraform
creates neither the identity/federated credential nor any of its ACR,
environment, or app roles.

The isolated non-production proof path, including the intentional post-smoke
failure and paired restoration evidence, is
[`STAGING_REHEARSAL.md`](./STAGING_REHEARSAL.md).

---

## 5. Secrets & env (1Password source → mount-specific Key Vaults)

1Password remains the operator-controlled source during bootstrap. Runtime apps
receive only Key Vault references and authenticate with user-assigned managed
identities; secret values do not enter Terraform state or the deployment job.

| Secret (`*_secret_vars`) | Purpose |
|---|---|
| `MAGISTERIUM_API_KEY` | doctrinal authority + arbiter |
| `AZURE_OPENAI_API_KEY` | CosmasCode inference; separate values may be seeded per mount |
| `ATHENA_CLIENT_ID` / `ATHENA_CLIENT_SECRET` | provider vault only; synthetic Preview pilot |
| `SOVEREIGN_RECORD_GRANT_SECRET` | provider vault only; canonical 44-character Fernet key (`Fernet.generate_key()`) shared with the platform host through its separate secret boundary |

| Public (`*_public_vars`) | public mount | provider mount |
|---|---|---|
| `MOCK_MODE` | `false` | `false` |
| `SOVEREIGN_MODE` | `hybrid` | `hybrid` |
| `AUTH_MODE` | hard-coded `disabled` | `oidc` |
| `OIDC_ISSUER` / `OIDC_JWKS_URL` / `OIDC_AUDIENCE` | absent | Clerk template values |
| `AUTH_ROLE_CLAIM` / `AUTH_PROVIDER_ROLE` | absent | verified `provider` role |
| `ALLOW_PROVIDER_PERSONA` | hard-coded `false` | `false` (mount is authoritative) |
| `BAA_ATTESTED` | hard-coded `false` | **`true`** under the Azure BAA |
| `LLM_PROVIDER` | `azure_openai` | `azure_openai` |
| `REASONING_MODEL` / `FAST_MODEL` | `gpt-5.6-sol` / `gpt-5.4-nano` | same |
| `CORS_ALLOW_ORIGINS` | `["https://cosmaneura.com","https://www.cosmaneura.com"]` | `["https://app.cosmaneura.com"]` |

> The provider deployment refuses to run in non-mock mode unless
> `BAA_ATTESTED=true` and the inference path is BAA-covered. Do not assert it
> until the applicable agreement and deployment controls are verified.

### Staging-only BAA attestation record (August 19, 2026)

The owner reports that Microsoft's standard BAA is stored in CosmaNeura's
compliance records. Azure support ticket `2607240040011671` stated on August 17,
2026 that the Microsoft DPA includes the HIPAA BAA and normally requires no
separate form, conditional on the customer's applicable agreement. Live billing
metadata reports `MicrosoftOnlineServicesProgram` for subscription
`bab2a709-1fcb-4c87-a9fc-802f7477ec13`. Microsoft Learn describes Azure
licensing as incorporating the Product Terms/DPA and the HIPAA BAA as available
by default for in-scope services.

That is the recorded operational basis for asserting `BAA_ATTESTED=true` only
on this synthetic Preview staging rehearsal. It is not a legal opinion, proof of
full HIPAA compliance, or permission to process real PHI. Before real-PHI use,
the applicable agreement and every PHI-bearing service must be validated for
this subscription; V7/V8 durable audit, production EHR authorization, counsel
review, and the remaining production gates below must also be complete.

---

## 6. The PHI boundary in Azure terms

The design's hard invariant — *PHI must never reach Magisterium, NPPES,
ClinicalTrials.gov, or any non-BAA hop* — translates to a few Azure controls:

- **Audit durability is deferred.** The local sink records value-free pre/post
  operation events but is not durable across replicas. That V7/V8 limitation
  must be reported and fixed before claiming production audit durability; it is
  not a platform dependency for this tightly controlled synthetic Preview pilot.
- **Separate Log Analytics.** The main environment's Log Analytics isn't under a
  HIPAA retention policy; give the Sovereign environment its own workspace so PHI-
  adjacent operational logs aren't commingled.
- **De-identification.** Non-mock provider startup requires Azure Health Data
  Services de-identification. Production is read through an explicit AzAPI data
  resource; staging creates its own `cosmaneura-staging-deid` in East US with
  no managed identity and public access enabled. Terraform derives and injects
  the returned endpoint. The regex path is development-only.
- **Egress.** Magisterium / ClinicalTrials / NPPES are public, non-BAA. The
  `assert_no_phi` tripwire + de-id gate are the enforcement; no Azure networking
  change is required, but if you want belt-and-suspenders, restrict the provider
  app's egress and document that the BAA LLM endpoint is the only PHI-bearing hop.

---

## 7. Remaining production gates

1. Provision and verify the exact Clerk JWT template, audience, and provider
   claim; run positive and wrong-role real-token smokes. Restrict the pilot
   template to the controlled synthetic tenant.
2. Provision the authoritative host-open synthetic Preview record mapping and
   actor-bound, 60-second record-grant issuer.
   Platform Agent API mode is not used by these pinned deployments.
3. For future production claims, choose and ratify the centralized audit
   ledger's external integrity anchor and retention duration. Container logs
   and local JSONL are not substitutes.
4. Seed both Key Vaults, establish immutable baseline revisions, and keep the
   deployment kill switch off until paired rollback is exercised.
5. Real-PHI/multi-tenant EHR authorization and Agent API wrapper mapping remain
   future work and are not part of this Preview-only deployment.

---

## 8. Suggested rollout order

1. Apply identities/vaults while live apps remain absent, seed secrets, then
   explicitly create both mounts on one reviewed immutable image.
2. Smoke the anonymous public mount without EHR authority.
3. Provision Clerk claims and synthetic mapping, then smoke the provider mount
   only against Athena Preview synthetic records.
4. Exercise a forced paired rollback and preserve the evidence before allowing
   later reviewed operator releases.
5. Keep real provider/PHI traffic disabled pending production EHR authorization,
   durable audit integrity, counsel/clinical review, and threat testing.

> This document covers *deployment topology and the identity seam*. It does not
> replace the legal/clinical sign-off or the production-hardening checklist in
> `SETUP_AND_HANDOFF.md`, which remain prerequisites for provider go-live.
