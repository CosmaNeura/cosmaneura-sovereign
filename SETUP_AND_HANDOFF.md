# Setup & Handoff

This is the short list of things I need **from you (CEO)** and **your developer**
to take this from a working mock to a live product. It's ordered so you can do the
cheap, unblocking things first.

Two roles are referenced:
- **You / business** — accounts, contracts, partner relationships, product calls.
- **Dev** — code, infra, security review.

---

## 1. Accounts & API keys (do these first — they unblock everything)

| What | Who | Where | Notes |
|---|---|---|---|
| **Magisterium API key** | You | https://www.magisterium.com/developers/api | Powers the doctrinal authority + arbiter. Free/paid tiers exist; confirm rate limits for your expected volume. |
| **Anthropic API key** | You | https://console.anthropic.com | Powers the reasoning LLM (routing + agents). The **first-party Messages API**, not Claude.ai. |

Once you have them, your dev copies `.env.example` → `.env`, pastes the keys, and
sets `MOCK_MODE=false`. That alone gets you a live patient build.

---

## 2. HIPAA for the provider build (required before any provider/PHI use)

The provider persona accepts PHI, so the inference path **must** be covered by a
Business Associate Agreement. Verified facts as of now:

- Anthropic **will sign a BAA** covering the **first-party Messages API** on
  qualifying commercial tiers, **with Zero Data Retention (ZDR) enabled**.
- The BAA covers **inference only**. Audit logging, storage, identity, and
  de-identification are **our responsibility** (that's what the `compliance/`
  module is scaffolding).
- The BAA does **not** cover Claude Free/Pro/Max/Team, self-serve Enterprise,
  the Workbench/Console, or Cowork. Don't let provider PHI touch those.
- Alternatively, run Claude via **AWS Bedrock** or **Google Vertex** under that
  hyperscaler's BAA — useful if you're already in one of those clouds.

**Action (You):** decide first-party Anthropic BAA+ZDR vs. Bedrock/Vertex, then
execute the agreement. **Action (Dev):** point the provider build at the
BAA-covered endpoint and confirm ZDR is on.

> **Hard invariant baked into the design:** Magisterium and every public data API
> (NPPES, ClinicalTrials.gov, partner feeds) are **not** business associates, so
> **PHI must never reach them.** The Sovereign de-identifies every draft before
> the Magisterium gate, and non-BAA clients carry an `assert_no_phi` tripwire.
> Keep that invariant when you extend the system.

---

## 3. MyCatholicDoctor data feed (currently a fixture — needs a real source)

Agent 1 (referral) reads providers from `app/clients/partner_directory.py`, which
today loads a small local fixture (`partners_fixture.json`). This is the
**integration contract**: each provider has a `booking_url` and a `partner` label
(for attribution / routing-traffic metering).

**Decisions needed (You):**
- How does MyCatholicDoctor expose its provider list? (API? CSV/JSON export?
  Nightly feed?) They match on **state licensure + specialty** and operate under
  the USCCB ERDs, but I found **no public API** — so this is a partnership /
  data-sharing conversation.
- How do **other resource partners** register to receive routed patient traffic,
  and how do you want that attributed/billed?

**Action (Dev):** once a feed exists, set `PARTNER_DIRECTORY_URL` and adapt the
client to its shape. The rest of the agent doesn't change. NPPES is already wired
as the broad/geo fallback for general (non-network) doctor search.

---

## 4. Confirm the Claude model IDs

I left the model strings as **env-overridable config defaults** (a reasoning model
and a fast model) with a comment to verify. Model names change.

**Action (Dev):** confirm current IDs at
https://docs.claude.com/en/docs/about-claude/models and set `LLM_MODEL` /
`LLM_FAST_MODEL` in `.env` accordingly.

---

## 5. Production-hardening checklist (Dev — before real data)

The code is a clean scaffold; these are the things that are deliberately baseline
and must be upgraded:

- [ ] **De-identification.** `compliance/phi.py` is a regex baseline. Replace with
      validated clinical de-id — **AWS Comprehend Medical**, **Microsoft Presidio**,
      or **Philter** — plus human review. Do not rely on the regex for real PHI.
- [ ] **Audit store.** `compliance/audit.py` hashes content (never stores raw) and
      writes to stdout/file. Point it at an **append-only / WORM** store with a
      retention policy (≥6 years is the usual HIPAA expectation; confirm with counsel).
- [ ] **Persona binding.** Today `persona` comes from the request body. Bind it to
      **authenticated identity** (e.g. a provider JWT) so a patient session can't
      claim the provider/PHI path.
- [ ] **Resilience.** Add rate-limiting, retries/backoff on the upstream APIs, and
      observability (structured logs, metrics, tracing).
- [ ] **Secrets.** Move keys into a managed secrets store; never commit `.env`.
- [ ] **Transport & tenancy.** TLS everywhere; if white-labelling to multiple
      partners, decide tenant isolation early.

### Security review (done this session) — fixed in code

A code-level security pass was run. These are now implemented:

- **Error sanitization.** All upstream HTTP calls catch errors and re-raise a
  fixed, non-leaking message (`compliance/netsec.py: safe_error_message`), so a
  failed provider-mode call can't echo PHI from the request body into logs or
  responses.
- **Input limits.** `ChatRequest` enforces max lengths (message/history/metadata)
  and the HTTP layer rejects bodies >256 KB — bounding DoS and denial-of-wallet.
- **ReDoS hardening.** The de-identifier caps input at 50 K chars and its phone
  regex was rewritten to fixed-shape (no catastrophic backtracking; verified
  <5 ms on adversarial input).
- **SSRF guard.** Operator-set URLs (partner directory, Magisterium MCP/REST) are
  checked against non-http(s) schemes and private/loopback/link-local/metadata
  addresses (`assert_safe_outbound_url`).
- **Provider-persona kill switch.** `ALLOW_PROVIDER_PERSONA` defaults **false**;
  a body-supplied `persona=provider` is rejected before any PHI is accepted.
  (This is a stopgap — still bind persona to authenticated identity, above.)
- **Audit integrity.** Audit records are now hash-chained (tamper-evident) and
  the file sink is created `0600`. Still pair with a WORM store in production.
- **HTTP headers / CORS.** Security headers added; CORS denies cross-origin by
  default (`CORS_ALLOW_ORIGINS` opt-in; never `*`).
- **Dependency pinning.** `requirements.txt` uses compatible-release pins.

### Security review, second pass — fixed in code

A second pass focused on what the first missed. Found and fixed:

- **Literal-mode PHI leak (HIGH).** The `SOVEREIGN_MODE=literal` router sent the
  raw message + metadata to Magisterium (non-BAA) for routing. It now
  de-identifies the routing context first — same invariant as every other
  Magisterium hop. (Hybrid, the production default, was never affected.)
- **Missing client-side guard (HIGH).** `MagisteriumClient` *documented* a
  belt-and-suspenders PHI refusal that was never implemented. `ask()` now runs
  `assert_no_phi` on every call, in every mode.
- **Fail-open doctrinal verdict (MEDIUM).** If the verdict parser returned
  nothing (malformed output or prompt injection upstream), the draft silently
  passed review. The gate now **fails closed**: unparseable verdict ⇒
  non-conforming ⇒ Magisterium's review is surfaced instead.
- **Weak BAA tripwire (MEDIUM).** The PHI path now requires `BAA_ATTESTED=true`
  — an explicit attestation that the BAA + ZDR are actually executed — not just
  `llm_provider=anthropic`.
- **Safety ordering.** The emergency short-circuit now runs **before** the
  provider-persona gate, so an emergency always gets 911/988 guidance even on a
  deployment that would reject the request.
- **Prompt-injection surface (MEDIUM).** The concierge's doctrinal advisory
  (third-party-derived text) moved out of the system prompt into delimited
  user-prompt data, with instructions to treat it as data.
- **Partner-feed link sanitization (MEDIUM).** `booking_url` is now https-only;
  `javascript:`/`data:`/`http:` links from a partner feed are dropped.
- **`session_id` charset (LOW).** Restricted to `[A-Za-z0-9_-]{1,64}` since it
  becomes the audit `trace_id`.

### Still open (deployment — not code)

- [x] **Authn/authz — BUILT.** `/chat` verifies JWTs and derives persona from a
      verified claim (`app/security/auth.py`). What remains is *config* — point
      it at your identity provider (see §10).
- [ ] **Rate limiting** per client/IP at the gateway (the size guard is not a
      rate limit).
- [ ] **WORM audit store** + key rotation + secrets manager (see above).
- [ ] **Pen test / threat model** review before provider go-live.

---

## 6. Legal & clinical review (You — non-negotiable)

I am not a lawyer or a physician, and this code is **not** legal or clinical
sign-off. Before launch:

- [ ] **HIPAA counsel** review of the provider data flow and the BAA chain.
- [ ] **Clinical governance** review of Agent 3's non-diagnostic framing and
      Agent 2's handling of experimental/unproven options.
- [ ] **Regulatory surface** review — FDA (software-as-a-medical-device line),
      FTC (health claims / advertising), and **state medical-practice** rules. The
      design keeps Agent 3 explicitly non-diagnostic to stay clear of the SaMD line,
      but confirm with counsel.
- [ ] **Doctrinal review** — have a trusted authority confirm the ERD enforcement
      and the topic list in the doctrinal gate match your intent.

---

## 7. Sovereign variant — DECIDED: hybrid

You chose the **hybrid** Sovereign, which is now the locked default
(`SOVEREIGN_MODE=hybrid`): a fast reasoning LLM distributes the task and
Magisterium renders final doctrinal judgment. Faithful to Hobbes (law + supreme
court vs. administration), accurate, and fast — pure logistics never pay for a
Magisterium round-trip.

If you ever want to A/B the **literal** variant (Magisterium consulted on every
request as the task-distributor too), it's a single setting: `SOVEREIGN_MODE=literal`.
Measured cost in mock mode: a pure-logistics referral that makes **zero**
Magisterium calls under hybrid makes **one** under literal, before any
arbitration. No other code changes — the router and arbiter are isolated.
Nothing for you to do here unless you want to run that comparison.

---

## 8. Optional: more data sources for Agents 2 & 3

Free, no-BAA-needed (public, non-PHI) sources you may want to add later:
**OpenFDA** (drugs/devices/adverse events), **PubMed / Europe PMC** (literature),
**MedlinePlus** (patient-friendly explanations), **NIH RePORTER** (funded research).
Each slots in as a new client behind the relevant agent.

---

## 9. Optional: inline doctrinal MCP layer (now built)

You asked about Magisterium's MCP working alongside the agents as another layer.
It's now wired as an **additive** capability, **off by default**, because it has
a real compliance constraint.

How it works: with `MAGISTERIUM_MCP_ENABLED=true`, an agent (today the Concierge)
consults Magisterium's MCP server *inline while it reasons*, via Anthropic's MCP
connector — in addition to the always-on final arbitration gate. Defense in depth.

To turn it on (Dev):
- Create/confirm a **magisterium.com account** and acquire an **OAuth token** for
  the MCP server (the connector does not run the OAuth flow itself).
- Set `MAGISTERIUM_MCP_ENABLED=true` and `MAGISTERIUM_MCP_TOKEN=...`.
- Confirm the current connector **beta header** (`ANTHROPIC_MCP_BETA`) against
  Anthropic's MCP-connector docs, and run a live smoke test (the MCP endpoint
  isn't reachable from the build environment).

⚠️ **Constraint you must respect:** Anthropic's MCP connector is **not
ZDR-eligible** and **not available on Bedrock/Vertex**, and Magisterium is not a
business associate. So this layer only ever sees **de-identified** text — the
code enforces it with a PHI guard, and the provider/PHI engagement path never
uses it. If you standardize provider inference on Bedrock/Vertex, the inline MCP
advisory won't be available there; the REST arbitration gate still applies.

---

## 10. Wire the provider Clerk template

The JWT layer is built and tested; it needs to be pointed at your IdP. For a
**provider/PHI deployment** (required — do not use `disabled` or `dev_hs256` in
production):

1. In Clerk, create the JWT template `cosmaneura-sovereign` with
   `aud="cosmaneura-sovereign"` and
   `roles: ["{{user.public_metadata.sovereign_role}}"]`.
2. An administrator sets `user.public_metadata.sovereign_role="provider"` only
   for approved pilot users. Never stamp a static provider role on every token.
3. Set `AUTH_MODE=oidc`, the Clerk issuer/JWKS values,
   `OIDC_AUDIENCE=cosmaneura-sovereign`, `OIDC_ALGORITHMS=["RS256"]`,
   `AUTH_ROLE_CLAIM=roles`, `AUTH_PROVIDER_ROLE=provider`, and
   `ALLOW_PROVIDER_PERSONA=false`.
4. Run the deployed live smoke with correct-role and wrong-role real tokens.

Once configured: every provider `/chat` call needs a valid token and provider
authority requires the verified role claim; a non-provider token cannot reach
that pinned mount even if the body claims `provider`.
Emergencies still short-circuit before auth.

> Token verification is necessary but **not sufficient** for HIPAA. It is one
> control among several — you still need the executed BAA (§2), validated
> de-identification (§5), a WORM audit store, rate limiting, and an independent
> security review before real PHI flows.

---

### TL;DR for you, the CEO

1. Get a **Magisterium key** and an **Anthropic key**. → live patient build.
2. For providers: **sign Anthropic's BAA + enable ZDR** (or use Bedrock/Vertex).
3. Sort the **MyCatholicDoctor data feed** and the **partner-registration** model.
4. Book **HIPAA counsel + clinical review**.
5. Sovereign variant is **decided: hybrid** — nothing to do unless you want to A/B literal.

Your dev handles model-ID confirmation, the hardening checklist, and pointing
the (already-built) JWT auth at your identity provider (§10).

---

## 12. Sprint status & go-live runbook (July 24, 2026)

> Historical sprint snapshot. For current dual-mount deployment and smoke
> procedure, use `DEPLOY_CHECKLIST.md`; the current smoke tools target deployed
> pinned instances and intentionally do not run direct local provider clients.

Everything below reflects the `claude/repo-review-8e9d5x` branch (PR #1).
Written so the incoming developer can go from clone to first live output in
under an hour.

### What is DONE (built, tested offline — 43 tests passing)

| Piece | Where |
|---|---|
| Six agents incl. ethics consult (forced-HIGH arbitration), visit prep, plan workbench (deterministic calculators) | `app/agents/` |
| PubMed client (Linacre filter, PHI tripwire) | `app/clients/pubmed.py` |
| 3-way output selector (chat/note/document) across provider lanes | `BaseAgent.resolve_output_format` |
| `GET /welcome` bootstrap, `POST /digest` journey compression, `GET /panel.js` drop-in dashboard component | `app/main.py`, `app/panel.js` |
| History-aware routing; agents consume `metadata.journey_digest` | `app/sovereign/router.py`, agents |
| Azure client speaks BOTH endpoint dialects (classic + Foundry v1, incl. `max_completion_tokens`) | `app/clients/llm.py` |
| Paired deployed mount/OIDC/model smoke | `tools/smoke_live.py` |
| Design docs: `INTERACTION_DESIGN.md`, `PROVIDER_COPILOT_DESIGN.md`, `UI_UX_DESIGN.md`; data governance in `README.md` | repo root |

### Azure facts + consolidation checklist (owner decision, July 24, 2026)

**Decision: consolidate on the `CosmasCode` Foundry resource.** During setup a
stray resource (`sovereign-reasoning-resource`) was auto-created and currently
holds the only copies of the two model deployments. Developer performs the
consolidation — 15 minutes:

1. In ai.azure.com, under the **CosmasCode** resource/project, deploy two
   models with these EXACT deployment names (they're already wired in config):
   `gpt-5.6-sol` (the newest reasoning-tier GPT) and `gpt-5.4-nano`.
   If Foundry rejects those names, accept its defaults and update
   REASONING_MODEL / FAST_MODEL in the env to match verbatim.
2. portal.azure.com → **CosmasCode** → Keys and Endpoint → **Regenerate
   Key 1** (a key was exposed in chat during setup and it is not certain
   which resource it belonged to — rotate to be sure) → store the new key in
   1Password item **`CosmasCode`**.
3. Delete the stray `sovereign-reasoning-resource` (and its
   "Sovereign-Reasoning" project). This removes duplicate spend paths and
   retires any lingering exposed credential on that resource for good.
4. Verify CosmasCode sits in the credits-bearing subscription and a monthly
   budget (~$10k, 50%/90% email alerts) exists on that subscription.

- Endpoint (wired in config): `https://cosmascode.services.ai.azure.com/openai/v1`
- Deployments (wired in config): `gpt-5.6-sol` · `gpt-5.4-nano`
- Key: 1Password item `CosmasCode` — never transmitted outside 1Password
- The current `tools/smoke_live.py` validates deployed public/provider mount
  separation, missing/wrong-role/provider-role OIDC behavior, and live model paths.

### Go-live runbook (first real output, synthetic data)

1. Follow `DEPLOY_CHECKLIST.md` to provision physically separate foundations;
   leave live apps and CI deployment disabled by default.
2. Supply the deployed URLs, short-lived provider-role and non-provider Clerk
   tokens, and a fresh host-issued grant for the confirmed vendor-owned Preview
   record only in the protected transactional smoke step.
3. Run the paired deployer from the reviewed private operator terminal. It runs
   both deployed smoke tools and rolls both image revisions back if any
   functional gate fails. Keep unattended workflow deployment disabled until a
   smoke-time credential mint exchange is implemented.

### Blocked on (not code)

| Item | Blocks | Owner |
|---|---|---|
| Microsoft DPA/BAA written confirmation | `BAA_ATTESTED=true` → any real PHI | John (support ticket filed) |
| Credits-cover-AOAI + quota tier answer | cost planning; 5.6 quota headroom | John (same ticket) |
| Clerk OIDC production values | `AUTH_MODE=oidc` → provider persona in prod | Developer + John |
| Cosmas dashboard mount (panel.js + `getAuthToken`/`getContext` + `/digest` at note-write) | Surface B | Developer (Cosmas repo; contract in `UI_UX_DESIGN.md` §3) |
| SSE streaming on `/chat` | perceived latency polish | Developer (post-launch) |

### First post-launch engineering items (in order)

1. Auth bridge (Cosmas session → provider JWT) — security-critical, small.
2. Panel mount in Cosmas with minimal context (latest note + terminology).
3. Prompt tuning against real material (redacted long note, labs, templates).
4. SSE streaming; per-field size bounds on `prior_sessions`/`labs`/`sessions`.
