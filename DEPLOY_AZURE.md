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
- **Two deployments, one image:** a `patient` build (no PHI) and a `provider`
  build (PHI/HIPAA). They differ only by env, not code.
- **Identity is already solved:** the main app authenticates with **Clerk**, and
  Clerk issues JWTs verifiable via JWKS. The Sovereign's `AUTH_MODE=oidc` verifies
  *the same Clerk tokens*. No new IdP. The Sovereign derives `persona` from a
  **verified Clerk role claim**, not the request body.
- **PHI boundary drives the topology:** the `patient` build can share the
  existing Container App Environment; the `provider` build lives in an **isolated
  resource group** so HIPAA scope (BAA LLM, ZDR, WORM audit ≥6 yr) does not engulf
  the main stack's shared Log Analytics / storage.

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

The Sovereign is a fifth HTTP service (`POST /chat`, `GET /healthz`). It has **no
Celery, no Postgres, no Redis** dependency of its own — it is stateless except for
its audit sink. That makes it cheap to run as an isolated app.

### Decision: which environment does it join?

| Build | Accepts PHI | Recommended placement | Why |
|---|---|---|---|
| **patient** | No | A new container app in the **existing** Container App Environment | Reuses ACR, networking, Log Analytics. SOC 2 only; no HIPAA scope added. |
| **provider** | Yes | A **separate resource group** (`cosmaneura-sovereign`) with its **own** environment, Log Analytics, and audit storage | Keeps the HIPAA blast radius (BAA inference, ZDR, WORM audit, ≥6-yr retention) off the main stack's shared, non-HIPAA-scoped logging/storage. |

Start with the **provider build in its own RG** if PHI is in scope at all — it is
far cheaper to merge two RGs later than to retroactively pull the main stack into
HIPAA scope. The `patient` build can ride along in the existing environment, or
share the new RG for operational simplicity.

```
Resource Group: cosmaneura-sovereign           ← NEW, isolated for HIPAA
  Container App Environment (sovereign)
    └─ sovereign-app   FastAPI /chat (ingress :8000 → sovereign.cosmaneura.com)
  Log Analytics (sovereign)        ← separate retention policy
  Storage (WORM, ≥6yr)             ← audit sink, immutability policy
  (ACR: reuse cosmaneura's, repo `cosmaneura/sovereign`, OR a dedicated ACR)
```

---

## 2. The identity contract (Clerk ↔ Sovereign OIDC)

This is the integration seam between the two services, and it is the most
important thing to get right. The Sovereign already supports it natively — there
is **no IdP to stand up**.

### How it works

The main app uses **Clerk** for identity (`cosmaneura/app/configuration/clerk.py`,
`app/routes/dependencies/clerk.py`, which verifies session JWTs against Clerk's
JWKS at `api.clerk.com/v1/jwks`). Clerk is an OIDC-style issuer: it publishes a
JWKS and signs RS256 JWTs. The Sovereign's `AUTH_MODE=oidc` verifies exactly that
(RS256 against a JWKS URL, plus issuer / audience / expiry).

```
  Browser (web/, Clerk session)
        │  fetch sovereign token (Clerk JWT template)
        ▼
  Authorization: Bearer <clerk-jwt>
        │
        ▼
  Sovereign /chat  ──verify──▶ Clerk JWKS (RS256, iss, aud, exp)
        │
        └─ persona = verified role claim   (NOT request body)
              provider-role JWT → provider persona (PHI path)
              everyone else     → patient persona
```

A patient token **cannot** reach the PHI path by sending `"persona":"provider"` in
the body — the verified claim overrides it, and the attempt is audited as
`persona_override` (`app/sovereign/orchestrator.py`).

### What to configure

Create one **JWT template** in Clerk (so the token carries the right `aud` and a
roles claim), then set these Sovereign env vars (names from `.env.example` /
`app/config.py`):

| Sovereign var | Value | Source |
|---|---|---|
| `AUTH_MODE` | `oidc` | — |
| `OIDC_ISSUER` | your Clerk Frontend API / instance issuer URL | Clerk dashboard |
| `OIDC_JWKS_URL` | Clerk instance JWKS URL | Clerk dashboard |
| `OIDC_AUDIENCE` | the `aud` set in the Clerk JWT template | you choose, e.g. `cosmaneura-sovereign` |
| `OIDC_ALGORITHMS` | `["RS256"]` | Clerk signs RS256 |
| `AUTH_ROLE_CLAIM` | claim carrying the clinician role (dotted paths OK) | Clerk org-role / metadata claim |
| `AUTH_PROVIDER_ROLE` | the role value that grants the provider persona | e.g. `provider` |
| `ALLOW_PROVIDER_PERSONA` | `true` (provider build only) | — |

**Role mapping to the existing model:** the main app's `Doctor` carries a
`clerk_id` and a Clerk organization role. That org role is the natural
`AUTH_PROVIDER_ROLE` — a clinician's token resolves to the provider persona; a
patient's token does not.

### Two things to confirm with Clerk (open items)
- **Which claim** carries the clinician role in your Clerk setup (org role vs.
  public metadata) → sets `AUTH_ROLE_CLAIM` / `AUTH_PROVIDER_ROLE`.
- **Audience**: add a JWT template that stamps `aud = <OIDC_AUDIENCE>` so the
  Sovereign's audience check passes. Run a **live smoke test** with a real token —
  the JWKS endpoint isn't reachable from CI (see `SETUP_AND_HANDOFF.md §10`).

> Token verification is necessary but **not sufficient** for HIPAA. Still required
> before real PHI: executed Anthropic BAA + ZDR, validated de-identification, WORM
> audit, rate limiting, independent security review.

---

## 3. Terraform additions

Mirror the existing patterns in `cosmaneura/infra/main.tf` rather than inventing
new ones. The Sovereign needs **less** than the main app (no Postgres/Redis).

**Reuse, verbatim, the conventions already in the repo:**
- The `azurerm_container_app` block for `app` — copy it, drop the Celery process
  types, keep one ingress container.
- The `var.python_public_vars` / `var.python_secret_vars` split and the
  `data.external.secrets` lookup — the Sovereign's env (§5) flows through the same
  mechanism, so secrets stay in 1Password (`op://`) and never land in state.
- The `lifecycle { ignore_changes = [template[0].container[0].image] }` block —
  image lifecycle is owned by the deploy pipeline, not Terraform.
- The custom-domain pattern from `domains.tf` (`azurerm_container_app_custom_domain`
  + managed certificate) — you already do this twice (`main`, `chat`); the
  Sovereign is a third (`sovereign.cosmaneura.com`).

Sketch (illustrative — match the real resource names/locals when implementing):

```hcl
resource "azurerm_container_app" "sovereign" {
  resource_group_name          = azurerm_resource_group.sovereign.name
  name                         = "${local.base_name}-sovereign"
  container_app_environment_id = azurerm_container_app_environment.sovereign.id
  revision_mode                = "Single"

  ingress {
    external_enabled = true
    target_port      = 8000              # uvicorn app.main:app
    traffic_weight { percentage = 100; latest_revision = true }
  }

  template {
    min_replicas = 1                     # >0 so a crash self-heals (cf. app)
    container {
      name   = "${local.base_name}-sovereign-web"
      image  = "${azurerm_container_registry.main.login_server}/cosmaneura/sovereign:latest"
      cpu    = 0.5
      memory = "1Gi"

      # same public/secret env split as the main app
      dynamic "env" { for_each = var.sovereign_public_vars
        content { name = env.key; value = data.external.secrets.result[env.key] } }
      dynamic "env" { for_each = var.sovereign_secret_vars
        content { name = env.key; secret_name = replace(lower(env.key), "_", "-") } }

      readiness_probe { port = 8000; transport = "HTTP"; path = "/healthz" }
      liveness_probe  { port = 8000; transport = "HTTP"; path = "/healthz" }
    }
  }

  lifecycle { ignore_changes = [template[0].container[0].image] }
  tags = var.tags
}
```

> Unlike the main app (TCP probes on :80), the Sovereign exposes `GET /healthz`, so
> use **HTTP** probes against it.

---

## 4. CI/CD

The main app's `build_and_publish.yml` builds a nixpacks image, pushes a
SHA-tagged image to ACR, and runs `az containerapp update --set-image <SHA>`,
with Terraform ignoring the live image. The Sovereign follows the **same shape**:

- **Separate repo (recommended to start):** clone `build_and_publish.yml` into the
  Sovereign repo. Build context is this project; image repo `cosmaneura/sovereign`;
  `az containerapp update` targets `*-sovereign`. Independent deploy cadence and
  isolated secrets — exactly the "separate repo for now" posture in
  `ARCHITECTURE.md §6`.
- **Monorepo (fold-in later):** add a parallel build+deploy job to the existing
  workflow that targets the Sovereign app. Simpler ops, coupled cadence. Do this
  once the PHI boundary is proven.

The Sovereign has no migrations and no Celery, so its pipeline is **strictly
simpler** than the main app's (no startup migrations, no scheduler/worker
revision coordination — cf. the startup crash-loop notes for the main app).

---

## 5. Secrets & env (1Password + Container App secrets)

The Sovereign's runtime config (full list in `.env.example`) maps onto the
existing `python_public_vars` / `python_secret_vars` convention. Add to the
1Password vault used by `data.external.secrets`:

| Secret (`*_secret_vars`) | Purpose |
|---|---|
| `MAGISTERIUM_API_KEY` | doctrinal authority + arbiter |
| `ANTHROPIC_API_KEY` | reasoning LLM — **BAA-covered** key for the provider build |
| `MAGISTERIUM_MCP_TOKEN` | only if `MAGISTERIUM_MCP_ENABLED=true` (off by default) |
| `AUTH_DEV_SECRET` | local only — never set in production |
| `PARTNER_DIRECTORY_API_KEY` | once MyCatholicDoctor exposes a feed |

| Public (`*_public_vars`) | patient build | provider build |
|---|---|---|
| `MOCK_MODE` | `false` | `false` |
| `SOVEREIGN_MODE` | `hybrid` | `hybrid` |
| `AUTH_MODE` | `oidc` | `oidc` |
| `OIDC_ISSUER` / `OIDC_JWKS_URL` / `OIDC_AUDIENCE` | Clerk values | Clerk values |
| `AUTH_ROLE_CLAIM` / `AUTH_PROVIDER_ROLE` | Clerk role | Clerk role |
| `ALLOW_PROVIDER_PERSONA` | `false` | `true` |
| `BAA_ATTESTED` | `false` | **`true`** (only after BAA+ZDR signed) |
| `LLM_PROVIDER` | `anthropic` | `anthropic` (1P+ZDR) or Bedrock/Vertex |
| `REASONING_MODEL` / `FAST_MODEL` | confirm current IDs | confirm current IDs |
| `AUDIT_SINK` | `file` (or Azure sink) | WORM-backed sink |
| `CORS_ALLOW_ORIGINS` | `["https://<web-app-origin>"]` | same; never `*` |

> The provider build refuses to run in non-mock mode unless `BAA_ATTESTED=true`
> **and** the inference path is BAA-covered — a deliberate tripwire. Don't set it
> until the agreement is executed.

---

## 6. The PHI boundary in Azure terms

The design's hard invariant — *PHI must never reach Magisterium, NPPES,
ClinicalTrials.gov, or any non-BAA hop* — translates to a few Azure controls:

- **Audit = WORM.** `compliance/audit.py` hashes content (never stores raw text)
  and writes to a sink. In Azure, point it at an **immutable (WORM) blob container**
  with a time-based retention policy (≥6 yr per the HIPAA expectation noted in
  `SETUP_AND_HANDOFF.md §5`). This is why the provider build wants its **own
  storage account**, not the main app's audio/video storage.
- **Separate Log Analytics.** The main environment's Log Analytics isn't under a
  HIPAA retention policy; give the Sovereign environment its own workspace so PHI-
  adjacent operational logs aren't commingled.
- **De-identification.** `compliance/phi.py` is a regex baseline — replace with
  **Azure AI Language PII / de-identification** (or Comprehend Medical / Presidio)
  + human review before real PHI. Fits naturally since you're already on Azure.
- **Egress.** Magisterium / ClinicalTrials / NPPES are public, non-BAA. The
  `assert_no_phi` tripwire + de-id gate are the enforcement; no Azure networking
  change is required, but if you want belt-and-suspenders, restrict the provider
  app's egress and document that the BAA LLM endpoint is the only PHI-bearing hop.

---

## 7. Open decisions (what to settle before building infra)

1. **Repo: separate vs monorepo.** Recommend **separate** while the PHI boundary
   is unproven; fold into `cosmaneura/` later behind the same module seams.
2. **Resource group: isolated vs shared.** Recommend **isolated RG** for the
   provider/PHI build; patient build may share the existing environment.
3. **ACR: reuse vs dedicated.** Reusing `cosmaneura`'s ACR (repo
   `cosmaneura/sovereign`) is simplest; a dedicated ACR tightens isolation.
4. **Clerk JWT template + role claim** (§2) — the one thing that needs a change in
   the *main app's* identity provider, not in this service.
5. **Provider inference path:** Anthropic 1P API + ZDR + BAA, **or** Claude via
   AWS Bedrock / Google Vertex under that cloud's BAA. (Note: the optional inline
   Magisterium MCP advisory is unavailable on Bedrock/Vertex — REST gate still
   applies. See `ARCHITECTURE.md §2`.)
6. **Audit store:** which Azure storage account + immutability policy + retention.

---

## 8. Suggested rollout order

1. **Patient build, mock off, Clerk OIDC wired** — live, no PHI, no BAA needed.
   Validates the identity contract end-to-end against real Clerk tokens.
2. **Provider build in isolated RG** — only after BAA+ZDR signed (`BAA_ATTESTED`),
   production de-id swapped in, and the WORM audit store stood up.
3. **HIPAA counsel + clinical + doctrinal review** (`SETUP_AND_HANDOFF.md §6`)
   before any real provider/PHI traffic.
4. **Harden:** gateway rate limiting, secrets rotation, pen test / threat model.

> This document covers *deployment topology and the identity seam*. It does not
> replace the legal/clinical sign-off or the production-hardening checklist in
> `SETUP_AND_HANDOFF.md`, which remain prerequisites for provider go-live.
