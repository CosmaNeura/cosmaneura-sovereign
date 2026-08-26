# CosmaNeura Sovereign

A single source of healthcare information **guided by Catholic teaching**, built
as a **Hobbesian Sovereign** that distributes each request to one of seven
specialist AI agents and arbitrates the final answer. **Magisterium AI** is the
doctrinal authority and final judge; a BAA-eligible reasoning LLM does the
mechanical orchestration. White-labelled onto MyCatholicDoctor.

> Full design rationale is in [`ARCHITECTURE.md`](./ARCHITECTURE.md).
> The list of accounts, keys, and decisions needed from you and your developer
> is in [`SETUP_AND_HANDOFF.md`](./SETUP_AND_HANDOFF.md). **Read that before going live.**

---

## What's in the box

- **Sovereign orchestrator** — emergency check → route → dispatch agent →
  scrub PHI → doctrinal arbitration → disclaimer → audit.
- **Seven agents** — (1) doctor referral, (2) frontier-medicine concierge,
  (3) non-diagnostic health-engagement, (4) ERD-grounded clinical-ethics
  consult, (5) clinician visit/session prep, (6) care-plan workbench with
  deterministic (code-computed) clinical calculations, (7) EMR lab review
  (athenahealth FHIR; feature-flagged, see `ATHENA_LABS.md`).
- **Two personas, one codebase** — `patient` (SOC 2, **no** HIPAA, no PHI) and
  `provider` (SOC 2 **+** HIPAA, PHI permitted, BAA-covered LLM required).
- **Tiered doctrinal gate** — pure logistics skip Magisterium; moral-weight
  topics get full arbitration against the USCCB Ethical and Religious Directives.
- **Optional inline doctrinal MCP layer** — agents can also consult Magisterium's
  MCP server *while reasoning* (off by default; de-identified text only). See
  `ARCHITECTURE.md` §2 and `SETUP_AND_HANDOFF.md` §9.
- **Compliance scaffolding** — PHI de-identification, a PHI-boundary tripwire,
  platform-owned canonical conversation/usage/audit reporting for provider
  turns, and a legacy local hash logger for offline development only.

## Quickstart (runs with zero credentials)

The whole pipeline ships in **mock mode** (`MOCK_MODE=true`, the default), so you
can see it work end-to-end before creating a single account.

```bash
make install      # pip install -r requirements.txt
make demo         # runs run_demo.py — 8 scenarios through the full pipeline
make test         # runs the test suite
make serve        # starts FastAPI on http://localhost:8000
```

If you run scripts directly instead of via `make`, set `PYTHONPATH`:

```bash
PYTHONPATH=. python run_demo.py
PYTHONPATH=. python tests/test_pipeline.py
PYTHONPATH=. uvicorn app.main:app --reload
```

## Pull request change budget

Pull requests receive a secretless `change-budget` status check from the
trusted default-branch workflow. It rejects more than 2,000 changed lines
(additions plus deletions) or 50 changed files and fails closed when GitHub's
diff metadata is missing, malformed, or negative. There is no label, checkbox,
actor, or administrator waiver; oversized changes must be split into
dependency-ordered pull requests. The workflow does not change human-approval
settings. Repository administration must separately require the
`change-budget` status context for it to prevent a merge.

### Try the API

```bash
curl -s localhost:8000/healthz

TURN_ID=$(python -c 'import uuid; print(uuid.uuid4())')
curl -s localhost:8000/chat \
  -H 'X-Cosmaneura-Embed-Version: 1.0' \
  -H "X-Cosmaneura-Client-Turn-ID: $TURN_ID" \
  -H 'content-type: application/json' \
  -d "{\"client_turn_id\":\"$TURN_ID\",\"message\":\"Find a Catholic OB-GYN near Nashua, New Hampshire\"}" \
  | python -m json.tool
```

The demo exercises the pipeline end-to-end: a patient referral, a concierge
question, a provider engagement request **with PHI** (scrubbed before the
doctrinal gate), an ERD ethics consult, a clinician visit prep, a plan-workbench deep-dive, a doctrinal
question (answered directly by Magisterium), and an emergency message
(short-circuits to 911/988).

## Authentication

Authority depends on the deployment mode, never the request persona:

- `disabled` (default public profile) — the public deployment requires no user
  token. In platform-enabled provider mode the same setting means the sole
  bearer is an opaque platform mount token, not an IdP JWT. A body persona is
  never authority.
- `dev_hs256` — shared-secret tokens for local testing without an identity
  provider. Mint one with `tools/mint_dev_token.py`. **Not for production.**
- `oidc` — legacy platform-disabled provider mode. Verifies RS256/ES256 against
  the identity provider's JWKS plus issuer/audience/expiry.
- platform-enabled provider mode — set `AUTH_MODE=disabled` and
  `PLATFORM_AGENT_API_ENABLED=true`. The host exchanges its authenticated
  session for an opaque mount token and sends that as the sole bearer plus a
  fresh client-turn UUID for chat. `GET /welcome` sends the same sole bearer
  through the read-only Agent API bootstrap without a client turn or durable
  lineage. The Agent API resolves actor, tenant, surface, and capabilities;
  Sovereign does not verify the browser JWT directly.

Local example with a dev token:

```bash
export AUTH_MODE=dev_hs256
export AUTH_DEV_SECRET=dev-secret-at-least-thirty-two-bytes
export MOUNT_PROFILE=provider_assistant MOUNT_TENANT_ID=tenant_local
# start the server in another shell with the same env, then:
TOKEN=$(python tools/mint_dev_token.py --role provider)
TURN_ID=$(python -c 'import uuid; print(uuid.uuid4())')
curl -s localhost:8000/chat -H "Authorization: Bearer $TOKEN" \
  -H 'X-Cosmaneura-Embed-Version: 1.0' \
  -H "X-Cosmaneura-Client-Turn-ID: $TURN_ID" \
  -H 'content-type: application/json' \
  -d "{\"client_turn_id\":\"$TURN_ID\",\"persona\":\"patient\",\"message\":\"help me prepare questions for a visit\"}"
# The deployment-pinned mount is authoritative. The verified provider role
# binds the actor; neither the body persona nor the JWT role selects a mount.
```

A body `persona` cannot select the provider/PHI path. See
`SETUP_AND_HANDOFF.md` for the platform exchange and legacy OIDC boundaries.

## Going live (the short version)

1. Create a **Magisterium API key** and an **Anthropic API key**.
2. For the **provider** build: execute Anthropic's **BAA** and enable **Zero Data
   Retention** on a qualifying tier (or run Claude via AWS Bedrock / Google Vertex
   under that hyperscaler's BAA).
3. Set `MOCK_MODE=false` and populate `.env` (copy from `.env.example`).
4. Replace the **baseline** de-identifier and complete platform retention and
   integrity controls — see the hardening checklist in `SETUP_AND_HANDOFF.md`.

## Project layout

```
app/
  main.py                 FastAPI app — POST /chat, GET /healthz
  config.py               Settings, Persona enum, ComplianceProfile
  schemas.py              Request/response + intent/sensitivity models
  sovereign/              router · arbiter · orchestrator
  agents/                 referral · concierge · engagement · ethics · visit_prep ·
                          plan_workbench · calculators · labs
  clients/                llm · magisterium · nppes · clinicaltrials · pubmed ·
                          labs + athena/ · partner_directory
  compliance/             phi · audit · safety
tests/test_pipeline.py
run_demo.py
```

## Your Data: How CosmaNeura Handles It

**Who can see your notes and transcripts?**
You, and the CosmaNeura team members who maintain the system — no one else.
Your organization's own IT administration may be granted access as part of its
oversight of your account. We never sell your data, share it with advertisers,
or give any third party access to it.

**What happens to your data when the AI works on it?**
Every visit is processed inside a HIPAA-compliant boundary: encrypted in
storage, encrypted in transit. AI analysis of identifiable patient information
happens **only** on AI services covered by a signed Business Associate
Agreement (BAA). Before anything is sent to any service outside that boundary —
including the Catholic doctrinal review layer (Magisterium AI) and public
medical databases like PubMed — patient identifiers are automatically stripped.
The system enforces this in code: a request carrying identifiers toward an
uncovered service fails rather than sends. The doctrinal layer never sees
patient information, by architecture, not by policy.

**Is your data used to train AI models?**
**No.** Neither your notes nor your patients' information is used to train any
AI model — ours or anyone else's. Our BAA-covered AI providers are
contractually barred from retaining or training on this data. The system
improves through configuration we build, not by learning from your records.

**How long do we keep it?**
Full visit transcripts are automatically deleted from your dashboard **7 days**
after the visit. Generated notes remain available in your dashboard for **up to
12 months**, or until you delete them. Governed audit records use opaque
references and content hashes rather than raw notes, and are still treated as
protected compliance data with controlled access and retention.

**Who decides what enters the medical record?**
You do. Nothing this system produces is written to your EHR automatically. You
review, you edit, you push the button. The AI prepares; the clinician decides.

*Questions? john@cosmaneura.com*

## Important

This repository is an **engineering scaffold**, not a compliance certification or
clinical/legal product. The PHI handling and audit code are baseline
implementations that **must** be reviewed and hardened with qualified HIPAA
counsel and clinical governance before any real patient or provider data flows
through it. See `SETUP_AND_HANDOFF.md`.
