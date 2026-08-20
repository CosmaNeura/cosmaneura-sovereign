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
  `provider_assistant` mount. The public deployment profile and the provider's
  platform-resolved context—not a body persona—determine capabilities and PHI posture.
- **Platform authority for the provider mount:** the host authenticates the user
  with Clerk, exchanges that session at the platform for an opaque, short-lived
  mount token, and sends that token as the sole bearer to Sovereign. Sovereign
  never interprets a Clerk JWT in this mode; the platform resolves actor, tenant,
  surface, and capabilities on every turn.
- **Feature-limited first activation:** the provider deployment uses the
  platform Agent API for mount resolution, canonical conversation, model-usage,
  and observational audit. Its initial capability set deliberately omits labs;
  real-PHI and production EHR authorization remain prohibited and out of scope.
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

## 2. The identity contract (Clerk → platform → Sovereign)

This is the integration seam between the two services, and it is the most
important thing to get right. The Sovereign already supports it natively — there
is **no IdP to stand up**.

### How it works

The main app uses Clerk for browser identity. The platform validates that
identity and provider authority during the context-token exchange, then mints
an opaque mount token. The platform-enabled Sovereign process treats that token
as an opaque credential and forwards it to the Agent API for authoritative
resolution. The public deployment does not use this lane.

```
  Browser (web/, Clerk session)
        │  exchange authenticated session at platform
        ▼
  Platform /embed/v1/context-tokens/exchange
        │  opaque mount token
        │
        ▼
  Sovereign /welcome ─bootstrap (read-only)─▶ Platform Agent API
  Sovereign /chat    ─resolve + turn lineage─▶ Platform Agent API
  Authorization: Bearer <opaque-mount-token>
        │
        └─ authoritative actor + tenant + surface + capabilities
```

A token cannot elevate the deployment through persona, capability, surface,
tenant, or actor hints in the body. Platform resolution is authoritative.
The welcome bootstrap carries no body, client-turn ID, interaction reference,
idempotency key, or effect and creates no durable lineage. Its five-field mount
snapshot filters deterministic welcome paths only; it cannot authorize an
action. Chat continues to use the interaction-opening resolver and the
canonical conversation/usage/audit sequence.

### What to configure

Configure the platform exchange to accept only the reviewed Clerk instance and
provider-role mapping. An administrator sets provider authority only for
approved pilot users; never grant it statically to every session. Configure the
platform-enabled Sovereign deployment as follows:

| Sovereign var | Value | Source |
|---|---|---|
| `AUTH_MODE` | `disabled` | direct browser JWT verification is off |
| `PLATFORM_AGENT_API_ENABLED` | `true` | enables platform authority |
| `PLATFORM_AGENT_API_BASE_URL` | exact platform HTTPS origin | environment contract |
| `PLATFORM_AGENT_API_SCOPE` | exact Agent API Entra scope | environment contract |
| `PLATFORM_WORKLOAD_CLIENT_ID` | provider UAI client ID | Terraform injection |
| `ALLOW_PROVIDER_PERSONA` | `false` | legacy body-persona elevation stays disabled |

The staging Clerk issuer and role mapping remain inputs to the platform host's
exchange boundary, not Sovereign's runtime verifier. A legacy deployment with
`PLATFORM_AGENT_API_ENABLED=false` may still use direct Clerk OIDC; that lane
requires `AUTH_MODE=oidc`, exact issuer/JWKS/audience, and a provider-role claim.

The current `Doctor` row carries `clerk_id` but has no canonical organization
spine. Do not infer provider authority from that row alone. The platform
exchange must bind the authenticated Clerk subject to the doctor-scoped pilot
authority, and Agent API resolution must return the same provider actor and
tenant for each turn.

### Clerk checks before activation
- Verify the host requests `getToken({template: "cosmaneura-sovereign"})`.
- Prove an admin-managed `sovereign_role=provider` user succeeds, a valid user
  with a different/missing value is denied, and no caller can self-edit that
  metadata. The deploy transaction repeats correct-role and wrong-role checks.

> Token exchange and mount resolution are necessary but not sufficient. This
> rollout is limited to synthetic traffic; it grants no real-PHI or
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
this subscription; live proof of platform reporting, production EHR
authorization, counsel review, and the remaining production gates below must
also be complete.

---

## 6. The PHI boundary in Azure terms

The design's hard invariant — *PHI must never reach Magisterium, NPPES,
ClinicalTrials.gov, or any non-BAA hop* — translates to a few Azure controls:

- **Platform reporting.** In platform mode Sovereign writes canonical
  conversation phases, model-usage facts, and closed observations to the Agent
  API and retains no production file or chain head. External integrity
  verification and retention assurance remain separate production controls.
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

1. Verify the exact Clerk instance and provider authority at the platform
   exchange; run positive and wrong-role real-token smokes. Restrict the pilot
   authority to the controlled synthetic tenant.
2. Provision the authoritative host-open synthetic Preview record mapping and
   actor-bound, 60-second record-grant issuer.
   Keep the labs capability absent during the first platform activation.
3. For future production claims, choose and ratify the centralized audit
   ledger's external integrity anchor and retention duration. Container logs
   and local JSONL are not substitutes.
4. Seed both Key Vaults, establish immutable baseline revisions, and keep the
   deployment kill switch off until paired rollback is exercised.
5. Real-PHI/multi-tenant EHR authorization remains future work and is not part
   of this Preview-only deployment.

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
