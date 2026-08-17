# Pilot Launch Plan — Sovereign Mounts Live for Pilot Users

**Date:** 2026-08-17 · **Owner:** John · **Goal:** demo within the week; both mounts
deployed; Athena provider utility expanded; provider mount embedded in the Cosmas
platform; public mount on cosmaneura.com behind a signup wall.

This plan spans four repos. Per governance (ADR 0014), the "two mounts" ship as two
physically separate Container Apps pinned by `MOUNT_PROFILE` — `public` and
`provider_assistant` — from this repo's existing Terraform. The platform resolver
(`PlatformResolver`) stays dormant until `POST /service/v1/agent/mount-context/resolve`
ships in the platform; flipping to it is configuration, not a rewrite.

**Compliance frame for the pilot:** everything below runs with **synthetic data only**.
Labs stay on the athenahealth **Preview sandbox tier** (ADR 0015 — provider-mount-only,
sandbox-labeled UI, name search disabled, no real patient data typed in). Real PHI is
gated behind the go-live gates in §6 and stays off until they pass.

---

## 1. cosmaneura-sovereign (this repo) — deploy the two mounts

Execute `DEPLOY_CHECKLIST.md` top to bottom (every box is currently unchecked):

1. **Secrets** — fill `.env.production.backend.example` / `.env.production.public.example`;
   create 1Password items (`MAGISTERIUM_API_KEY`, LLM keys); verify with
   `infra/scripts/export-env.sh` + `infra/scripts/preflight.sh`.
2. **Terraform** — `tofu init/plan/apply` in `infra/` (creates both Container Apps, ACR,
   log workspaces, de-id role assignment, partner-directory share + scraper job).
3. **CI wiring** — set GitHub repo variables (`AZURE_CLIENT_ID/TENANT_ID/SUBSCRIPTION_ID`,
   `CONTAINER_REGISTRY_HOST`); confirm `github_repository`/`github_branch` in
   `infra/variables.tf` match or the OIDC federation fails.
4. **First image** — push to main (workflow) or manual nixpacks build/tag/push `:latest`;
   apps boot on `:latest`, which does not exist until the first push.
5. **Verify** — `/healthz` on both FQDNs; `POST /chat` on the provider mount with a real
   Clerk token (`tools/clerk_oidc_local_demo.py` proves the contract).
6. **Custom domain** — `sovereign.cosmaneura.com` (+ a public-mount hostname) and DNS are
   manual, not in Terraform.
7. **CORS** — allowlist `cosmaneura.com` (public mount) and the Cosmas app origin
   (provider mount); deny-by-default stays.

**Athena expansion for provider utility** (code work in this repo):

- `LabsPurpose` already enumerates `VISIT_PREP` and `PLAN_WORKBENCH`; wire governed
  `labs.read` into `visit_prep_agent` and `plan_workbench_agent`. Touches: orchestrator
  `LabsReadAccess` construction, authorization in `app/clients/labs.py`, the config
  validator, and the `provider_assistant` mount capability list — all within the ratified
  `contracts/ehr-labs-tool-v1.md` shape (purpose enum, bounded window, record-ref from the
  host-open chart, two-phase audit).
- Keep sandbox pinned (practice 195900). **Production chart access is refused in code**
  by design; unlocking it later requires the ADR 0013 evidence set (practice BAA,
  athenaOne "Cosmaneura Lab Insights" enablement, real practice ID, `BAA_ATTESTED=true`)
  plus a deliberate code change — do not soften the refusal for the pilot.
- Post-pilot (tracked, not launch-blocking): shared Athena token cache, httpx rewrite.

## 2. cosmaneura (platform) — mount the provider panel in Cosmas

The only platform code needed for the pilot (per ADR 0014):

1. **Clerk JWT template** — stamp `aud=cosmaneura-sovereign`; confirm which claim carries
   the clinician role; hand `OIDC_ISSUER`/`OIDC_JWKS_URL`/role-claim values to the
   sovereign provider deployment env.
2. **Embed `panel.js`** in the provider dashboard (React):
   `<script src="https://sovereign.cosmaneura.com/embed/v1/panel.js">`, then
   `CosmaNeuraPanel.mount({ sovereignUrl, getAuthToken, getContext, onInsert/onSave })`;
   call `panel.setRecord(id)` on record change. `getAuthToken` returns the Clerk session
   JWT minted from the template above.
3. **Context feed** — expose `app/services/patient_context.py::assemble_patient_context()`
   via an authenticated endpoint the dashboard can call, and pass its output through
   `getContext` (record_ref, journey_digest, prior_sessions, terminology). Optionally call
   the sovereign `POST /digest` at note-write time.
4. **Pilot gating** — extend the existing beta allowlist
   (`web/app/utils/beta-access.ts` + its server-side mirror) with the MCD pilot cohort so
   only pilot providers see the panel.
5. **Later (expires ADR 0014)** — implement `/embed/v1/context-tokens/*` and
   `/service/v1/agent/mount-context/resolve` per `contracts/agent-api.md`; the Entra
   service-auth pattern already used for Scribe
   (`app/routes/dependencies/entra_service.py`) is the template. When shipped, Sovereign
   flips `PinnedResolver` → `PlatformResolver` by config.

## 3. cosmaneura-web (website) — public mount behind a signup wall

Today the site is a static Cloudflare Pages landing page with live Stripe checkout,
webhook handlers that only `console.log`, a decorative demo form, and **no auth, no
persistence, no app link**. Per ADR 0001/0002, Stripe never mints capability — signup
gates *visibility*, the platform/deployment still owns authority.

1. **Persistence** — bind Cloudflare KV or D1; write subscriber records
   (email, Stripe customer/subscription IDs, status) from the existing webhook stubs
   (`functions/api/webhook.js` — activate on `checkout.session.completed`, revoke on
   `customer.subscription.deleted` / `invoice.payment_failed`).
2. **Signup wall** — a login/claim-account flow tied to the Stripe email (Clerk is the
   natural choice since the rest of the stack already uses it), plus
   `functions/_middleware.js` gating an `/app/` subtree: no valid session or inactive
   subscription → redirect to pricing/login.
3. **Mount the public panel** on the gated page:
   `<script src="https://<public-mount-host>/embed/v1/panel.js">` with no auth token
   (anonymous tier — the public deployment physically holds no EHR creds, no BAA switch).
4. **Provisioning email** — replace the manual "we'll email you" flow: on webhook
   activation, send the invite/magic link (e.g. Resend/Mailchannels from the Function).
5. **Hygiene before accounts** — real Privacy/Terms pages (footer links are `#` stubs),
   wire the demo-booking form to an actual backend, add a nav "Log in" link, make
   `success.html` verify `session_id` server-side.

## 4. This week's demo

`DEMO_RUNBOOK.md` is tested and self-contained: `./tools/run_demo_web.sh` →
`http://localhost:8000/demo` (five scripted scenarios; `/demo/dashboard` shows the
provider panel with sandbox labs). For a hosted demo, deploying the two mounts (§1) with
`DEMO_UI_ENABLED=true` on a non-production hostname is enough — no platform or website
work is on the demo's critical path. Cautions from the runbook: avoid states without
directory providers, emergency phrasing short-circuits to 911, Magisterium free tier is
90 req/week.

## 5. Owner (non-code) action items

- **ACTION_ITEMS.md 1–3**: Microsoft DPA/BAA written confirmation + credits/quota answer;
  GPT-5.5 + fast deployments in Foundry with budget alert; xAI BAA questionnaire.
- **Clerk production values** for the JWT template and role claim (with developer).
- **Azure consolidation** (SETUP_AND_HANDOFF §12): move model deployments under
  `CosmasCode`, rotate the exposed key, delete the stray `sovereign-reasoning-resource`.
- **DNS** for `sovereign.cosmaneura.com` and the public mount host.
- **MCD pilot coordination**: pilot provider list for the allowlist; Athena production
  prerequisites (practice BAA, athenaOne app enablement) started now — paperwork is the
  slowest step even though the pilot stays sandbox-tier.
- **Stripe → provisioning decision**: confirm the interim account-provisioning path
  (webhook-driven invite vs. manual) before pointing pilot users at checkout.

## 6. Go-live gates before any real PHI (unchanged, non-negotiable)

`BAA_ATTESTED=true` backed by an executed BAA on the inference path ·
`DEID_PROVIDER=azure` validated · `AUTH_MODE=oidc` with production IdP ·
WORM audit sink · gateway rate limiting (public mount is pinned to 1 replica until then) ·
pen test · HIPAA counsel / clinical / doctrinal review · Athena production tier only with
the full ADR 0013 evidence set.

## 7. Suggested sequence

| Day | Work |
|---|---|
| 1–2 | §1 steps 1–5: secrets, tofu apply, CI vars, first image, healthz + Clerk smoke |
| 2–3 | Clerk JWT template; Cosmas dashboard panel embed + context endpoint (platform repo) |
| 3–4 | Website: KV + webhook writes, login/middleware wall, public panel embed |
| 4–5 | Athena labs → visit-prep/plan-workbench wiring; end-to-end demo dry run per runbook |
| parallel | Owner items §5; DNS; pilot allowlist; paperwork (§6 gates) |
