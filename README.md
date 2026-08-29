# Sovereign

Sovereign is a named dark beta for healthcare-information orchestration guided
by Catholic teaching and designed for MyCatholicDoctor. Its **Hobbesian
Sovereign** routes requests to seven specialist AI agents and arbitrates the
final answer. **Magisterium AI** is the doctrinal authority and final judge; a
BAA-eligible reasoning LLM can perform the mechanical orchestration.

There are no Sovereign production resources. The beta is synthetic-only and
does not authorize real patient or provider data.

> Full design rationale is in [`ARCHITECTURE.md`](./ARCHITECTURE.md).
> Local evaluation guidance, beta boundaries, and future prerequisites are in
> [`SETUP_AND_HANDOFF.md`](./SETUP_AND_HANDOFF.md). Read that before enabling
> any external service.

---

## What's in the box

- **Sovereign orchestrator** — emergency check → route → dispatch agent →
  scrub PHI → doctrinal arbitration → disclaimer → audit.
- **Seven agents** — (1) doctor referral, (2) frontier-medicine concierge,
  (3) non-diagnostic health-engagement, (4) ERD-grounded clinical-ethics
  consult, (5) clinician visit/session prep, (6) care-plan workbench with
  deterministic (code-computed) clinical calculations, (7) EMR lab review
  (athenahealth FHIR scaffold; disabled by default, see `ATHENA_LABS.md`).
- **Two personas, one codebase** — `patient` is the current non-PHI path;
  `provider` is a gated scaffold only. Any future provider/PHI use requires a
  verified identity, explicit `BAA_ATTESTED` operator attestation, a
  BAA-eligible inference service under an applicable agreement, validated
  de-identification, and independent legal/security review. Those controls are
  prerequisites, not a certification of Sovereign.
- **Tiered doctrinal gate** — pure logistics skip Magisterium; moral-weight
  topics get full arbitration against the USCCB Ethical and Religious Directives.
- **Optional inline doctrinal MCP layer** — agents can also consult Magisterium's
  MCP server *while reasoning* (off by default; de-identified text only). See
  `ARCHITECTURE.md` §2 and `SETUP_AND_HANDOFF.md` §4.
- **Compliance scaffolding** — PHI de-identification, a PHI-boundary tripwire,
  and a hash-only local audit logger for development. Optional platform clients
  are inactive beta code paths, not a production data or retention system.

Sovereign has no public signup and does not verify professional credentials.
It exposes no raw audio, video, or media-upload path and does not persist visit
transcripts or clinical records. Provider-oriented context fields are
synthetic-only scaffolding, not authorization to send real records. Sovereign
is not the Cosmas PHI hub, and no Cosmas record-store or retention policy
should be attributed to it.

## Quickstart (runs with zero credentials)

The whole pipeline ships in **mock mode** (`MOCK_MODE=true`, the default), so you
can see it work end-to-end before creating a single account.

```bash
make install      # pip install -r requirements.txt
make demo         # runs run_demo.py — 8 scenarios through the full pipeline
make test         # runs the test suite
make serve        # starts FastAPI on http://localhost:8080
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
curl -s localhost:8080/healthz

TURN_ID=$(python -c 'import uuid; print(uuid.uuid4())')
curl -s localhost:8080/chat \
  -H 'X-Cosmaneura-Embed-Version: 1.0' \
  -H "X-Cosmaneura-Client-Turn-ID: $TURN_ID" \
  -H 'content-type: application/json' \
  -d "{\"client_turn_id\":\"$TURN_ID\",\"message\":\"Find a Catholic OB-GYN near Nashua, New Hampshire\"}" \
  | python -m json.tool
```

The demo exercises the pipeline end-to-end with synthetic fixtures: a patient
referral, a concierge question, a provider-boundary scrub scenario, an ERD
ethics consult, a clinician visit prep, a plan-workbench deep-dive, a doctrinal
question (answered directly by Magisterium), and an emergency message
(short-circuits to 911/988).

## Authentication

Authority depends on configured runtime mode, never the request persona:

- `disabled` (default local/public profile) — no user token is required; a body
  persona still never grants provider authority.
- `dev_hs256` — shared-secret tokens for local testing without an identity
  provider. Mint one with `tools/mint_dev_token.py`. **Not for production.**
- `oidc` — verifies RS256/ES256 against an identity provider's JWKS plus
  issuer/audience/expiry. It is a future provider-path prerequisite, not
  evidence of a production deployment.

The optional platform Agent API path is outside the named beta and remains
disabled (`PLATFORM_AGENT_API_ENABLED=false`). Do not treat it as an active
Cosmas integration or enable it from this documentation.

Synthetic local example with a dev token:

```bash
export AUTH_MODE=dev_hs256
export AUTH_DEV_SECRET=dev-secret-at-least-thirty-two-bytes
export MOUNT_PROFILE=provider_assistant MOUNT_TENANT_ID=tenant_local
# start the server in another shell with the same env, then:
TOKEN=$(python tools/mint_dev_token.py --role provider)
TURN_ID=$(python -c 'import uuid; print(uuid.uuid4())')
curl -s localhost:8080/chat -H "Authorization: Bearer $TOKEN" \
  -H 'X-Cosmaneura-Embed-Version: 1.0' \
  -H "X-Cosmaneura-Client-Turn-ID: $TURN_ID" \
  -H 'content-type: application/json' \
  -d "{\"client_turn_id\":\"$TURN_ID\",\"persona\":\"patient\",\"message\":\"help me prepare questions for a visit\"}"
# The configured local mount is authoritative; the body persona grants nothing.
```

A body `persona` cannot select the provider/PHI path. See
`SETUP_AND_HANDOFF.md` for the beta boundary and future prerequisites.

## Production remains blocked

This repository has staging resources, but production resources do not yet
exist. A merge to `main` does not deploy. The tracked production job is manual,
founder-only, and additionally disabled by `SOVEREIGN_DEPLOY_ENABLED`; required
repository variables and Azure federation are not configured. Do not treat
these source gates as production readiness or enable them from this README.

Use [`DEPLOY_CHECKLIST.md`](./DEPLOY_CHECKLIST.md),
[`STAGING_REHEARSAL.md`](./STAGING_REHEARSAL.md), and the
[pre-activation containment plan](./docs/flight-plans/sovereign-preactivation-deploy-containment.md)
as the operational sequence. That sequence requires exact-source review,
immutable image identity, live-state reconciliation, paired rollback proof,
applicable vendor/BAA verification, and explicit production authorization.
Until those gates pass, keep all work dark and synthetic-only with no PHI.

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

## Beta data boundary

The current patient path is non-PHI. Sovereign is not a live
visit-documentation, transcription, medical-record, or EHR write-back product.
Provider-oriented request fields can exercise those shapes with synthetic text,
but Sovereign does not persist them or make a retention promise. It has no
production persistence or production resources.

The provider path is only a gated engineering scaffold. Its configuration
includes an operator self-attestation and BAA-eligibility checks, but those do
not make Sovereign HIPAA compliant or authorize real PHI. Before any text can
reach Magisterium or another uncovered service, identifiers are removed and a
PHI tripwire fails closed on detected identifiers. Keep all beta use synthetic
and leave provider, platform, and athenahealth capabilities off.

## Important

This repository is an **engineering scaffold**, not a compliance certification
or clinical/legal product. Its PHI handling and audit code are baseline
implementations only. See `SETUP_AND_HANDOFF.md`.
