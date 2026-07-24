# CosmaNeura Sovereign

A single source of healthcare information **guided by Catholic teaching**, built
as a **Hobbesian Sovereign** that distributes each request to one of six
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
- **Six agents** — (1) doctor referral, (2) frontier-medicine concierge,
  (3) non-diagnostic health-engagement, (4) ERD-grounded clinical-ethics
  consult, (5) clinician visit/session prep, (6) care-plan workbench with
  deterministic (code-computed) clinical calculations.
- **Two personas, one codebase** — `patient` (SOC 2, **no** HIPAA, no PHI) and
  `provider` (SOC 2 **+** HIPAA, PHI permitted, BAA-covered LLM required).
- **Tiered doctrinal gate** — pure logistics skip Magisterium; moral-weight
  topics get full arbitration against the USCCB Ethical and Religious Directives.
- **Optional inline doctrinal MCP layer** — agents can also consult Magisterium's
  MCP server *while reasoning* (off by default; de-identified text only). See
  `ARCHITECTURE.md` §2 and `SETUP_AND_HANDOFF.md` §9.
- **Compliance scaffolding** — PHI de-identification, a PHI-boundary tripwire,
  and a content-hashing audit logger that never stores raw text.

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

### Try the API

```bash
curl -s localhost:8000/healthz

curl -s localhost:8000/chat -H 'content-type: application/json' -d '{
  "message": "I need to find a Catholic OB-GYN near me",
  "persona": "patient",
  "metadata": {"state": "NH", "specialty": "obstetrics"}
}' | python -m json.tool
```

The demo exercises the pipeline end-to-end: a patient referral, a concierge
question, a provider engagement request **with PHI** (scrubbed before the
doctrinal gate), an ERD ethics consult, a clinician visit prep, a plan-workbench deep-dive, a doctrinal
question (answered directly by Magisterium), and an emergency message
(short-circuits to 911/988).

## Authentication

`/chat` supports JWT auth so the **persona is derived from a verified identity**,
not from the request body. Controlled by `AUTH_MODE`:

- `disabled` (default) — no token required; persona comes from the body (still
  gated by `ALLOW_PROVIDER_PERSONA`). Local dev / patient-only, no PHI.
- `dev_hs256` — shared-secret tokens for local testing without an identity
  provider. Mint one with `tools/mint_dev_token.py`. **Not for production.**
- `oidc` — production. Verifies RS256/ES256 against your identity provider's
  JWKS, plus issuer/audience/expiry. Works with Auth0, Cognito, Entra, Okta,
  Keycloak, etc. **Provider/PHI deployments must use this.**

Local example with a dev token:

```bash
export AUTH_MODE=dev_hs256 AUTH_DEV_SECRET=devsecret
# start the server in another shell with the same env, then:
TOKEN=$(AUTH_DEV_SECRET=devsecret python tools/mint_dev_token.py --role provider)
curl -s localhost:8000/chat -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"persona":"patient","message":"help me understand these labs"}'
# The token's role wins: this is treated as a provider request regardless of body.
```

A patient token cannot reach the PHI path by putting `"persona":"provider"` in
the body — the verified claim overrides it. See `SETUP_AND_HANDOFF.md` for the
OIDC settings your developer wires to your identity provider.

## Going live (the short version)

1. Create a **Magisterium API key** and an **Anthropic API key**.
2. For the **provider** build: execute Anthropic's **BAA** and enable **Zero Data
   Retention** on a qualifying tier (or run Claude via AWS Bedrock / Google Vertex
   under that hyperscaler's BAA).
3. Set `MOCK_MODE=false` and populate `.env` (copy from `.env.example`).
4. Replace the **baseline** de-identifier and audit sink with production-grade
   equivalents — see the hardening checklist in `SETUP_AND_HANDOFF.md`.

## Project layout

```
app/
  main.py                 FastAPI app — POST /chat, GET /healthz
  config.py               Settings, Persona enum, ComplianceProfile
  schemas.py              Request/response + intent/sensitivity models
  sovereign/              router · arbiter · orchestrator
  agents/                 referral · concierge · engagement · ethics · visit_prep ·
                          plan_workbench · calculators
  clients/                llm · magisterium · nppes · clinicaltrials · pubmed · partner_directory
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
12 months**, or until you delete them. Our audit log — kept for compliance —
stores cryptographic fingerprints of content, never the content itself, so even
our own compliance records contain no patient information.

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
