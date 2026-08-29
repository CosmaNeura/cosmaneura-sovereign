# Sovereign Setup & Handoff

Sovereign is a named dark beta for Catholic/MyCatholicDoctor
healthcare-information orchestration. It is an engineering scaffold, not
Cosmas, not a clinical record system, and not a compliance certification.

## Current status and hard stops

- There are no Sovereign production resources.
- A merge to `main` does not deploy.
- The production job is manual and founder-only. It is additionally blocked
  while the repository variable `SOVEREIGN_DEPLOY_ENABLED` is off. Keep that
  latch off.
- Repository production variables and Azure federation are not configured and
  must not be configured as part of beta documentation or local setup.
- The patient path is non-PHI. All current evaluation stays dark and
  synthetic-only.
- The provider path is a gated scaffold, not an authorization to process real
  PHI.
- `PLATFORM_AGENT_API_ENABLED` and `FEATURE_ATHENA_LABS` remain off. There is
  no public signup.

This document covers local evaluation and records prerequisites for a future,
separately authorized production decision. It is not a go-live runbook.

---

## 1. Run the beta locally

The default configuration is secretless and offline:

```bash
make install
make test
make demo
make serve
```

`MOCK_MODE=true` is the default. It exercises routing, seven-agent dispatch,
the Magisterium arbitration shape, safety handling, and audit events without
calling external services.

For direct script execution:

```bash
PYTHONPATH=. python run_demo.py
PYTHONPATH=. python -m pytest tests/ -q
PYTHONPATH=. uvicorn app.main:app --reload --port 8080
```

Do not use real patient data in local or beta testing.

---

## 2. What the beta is

The request pipeline is:

1. Emergency safety short-circuit.
2. Local authority/persona resolution and the provider gate.
3. Intent and doctrinal-sensitivity routing.
4. Dispatch to one of seven agents, or directly to Magisterium for a doctrinal
   request.
5. PHI scrub before Magisterium or another uncovered service.
6. Magisterium arbitration for faith- or morals-weighted answers.
7. Safety disclaimer, hash-only local audit event, and response.

The seven implemented lanes are referral, concierge, engagement, ethics
consult, visit prep, plan workbench, and lab review. Lab review is a disabled
athenahealth adapter scaffold; this handoff does not enable it.

The MyCatholicDoctor partner directory is currently a local fixture. A future
feed can use `PARTNER_DIRECTORY_URL`, but no live feed or partner-registration
system is implied by this repository.

Sovereign accepts text requests and exposes no raw audio, video, or media-upload
path. Provider-oriented fields can carry synthetic visit context for scaffold
testing, but real visit material is not authorized and Sovereign does not
persist it. It has no EHR write-back workflow and no Sovereign production
retention policy. Do not apply Cosmas storage, visit-documentation, or dashboard
behavior to this beta.

---

## 3. Patient and provider boundaries

### Patient path

The patient path is the current beta surface and is non-PHI. Consumer health
questions can still be sensitive, so the same minimization and outbound scrub
remain useful defense in depth. The path does not accept PHI and does not imply
public signup or a public production service.

### Provider path

The provider path exists as code behind fail-closed gates. It is not active for
real data. `ALLOW_PROVIDER_PERSONA=false` and `BAA_ATTESTED=false` remain the
safe defaults.

`BAA_ATTESTED=true` is only an operator's self-attestation that the exact
account, service, agreement, and runtime configuration have been reviewed. A
vendor's general BAA eligibility is not proof that a specific Sovereign path is
covered, and the setting does not establish HIPAA compliance. Before any future
real-PHI use, independently verify at least:

- the signed agreement and exact service scope;
- any required zero-data-retention configuration;
- identity, authorization, tenant, and record scope;
- validated clinical de-identification;
- audit integrity and a counsel-approved retention policy;
- security testing, observability, incident response, and clinical governance.

No provider activation should be inferred from a body `persona`, an NPI match,
or the presence of provider-oriented agent code.

---

## 4. PHI boundary

Magisterium, NPPES, ClinicalTrials.gov, PubMed, and other public services are
not covered destinations for provider PHI. PHI must never reach them.

Sovereign de-identifies drafts before the Magisterium gate and gives uncovered
clients an `assert_no_phi` tripwire. Keep this invariant for every new client.
The built-in regex scrubber is a baseline for development, not validated
clinical de-identification and not sufficient authorization for real PHI.

The inline Magisterium MCP advisory is also off by default. If evaluated later,
it may receive de-identified text only; the REST arbitration gate remains the
authoritative doctrinal check.

---

## 5. Optional local external-service evaluation

External calls are not required for the beta. For an explicitly approved
non-PHI local evaluation, the operator may provide:

| Service | Purpose | Boundary |
|---|---|---|
| Magisterium API | Doctrinal answers and final arbitration | De-identified, non-PHI text only |
| Anthropic Messages API or Azure OpenAI | Routing and agent reasoning | Non-PHI beta input only; BAA eligibility alone grants no provider authority |
| NPPES / ClinicalTrials.gov / PubMed | Public referral, trial, and literature data | De-identified query text only |
| MyCatholicDoctor partner feed | Catholic provider referral data | Fixture unless a separately approved feed exists |

Copy `.env.example` to an untracked local `.env`; never commit keys. Changing
`MOCK_MODE=false` starts external network calls but does not create a live
product, enable provider PHI, or provision production infrastructure.

Model IDs and vendor terms change. Confirm them against current vendor
documentation before any external evaluation.

The checked-in Azure configuration examples currently name
`REASONING_MODEL=gpt-5.6-sol` and `FAST_MODEL=gpt-5.4-nano`. These are expected
configuration identifiers only; they do not show that model deployments or
production resources exist.

---

## 6. Security work still required

The repository includes baseline controls such as request-size limits,
deny-by-default CORS, generic authentication failures, outbound URL checks,
input bounds, PHI tripwires, and emergency handling. They are not production
readiness evidence.

Before a future production proposal, complete and independently review:

- validated de-identification with documented human escalation;
- fail-closed identity and authorization for the exact surface;
- rate limiting, retries, monitoring, and incident response;
- managed secrets and environment isolation;
- durable audit integrity and an approved retention policy;
- threat modeling, penetration testing, clinical governance, and legal review;
- vendor agreements and data-flow evidence for every hop.

Do not claim clinical safety, regulatory approval, independently verified audit
integrity, or production readiness until the corresponding evidence exists.

---

## 7. Deployment containment

`.github/workflows/build_and_publish.yml` runs quality and infrastructure
validation on pull requests and pushes. Its deployment job is a separate manual
path with multiple gates:

- `workflow_dispatch` from the exact current `main`;
- the founder account as both actor and triggering actor;
- successful tests, infrastructure validation, and authorization job;
- `SOVEREIGN_DEPLOY_ENABLED == "true"`;
- production variables, secrets, federation, smoke credentials, and external
  governance that do not currently exist.

Therefore a merge to `main` does not deploy. Keep
`SOVEREIGN_DEPLOY_ENABLED` off. Do not run **Build & Publish**, configure Azure
federation, create production resources, enable the platform Agent API, enable
athenahealth labs, or add public signup as part of this handoff.

Any future activation requires its own reviewed change and explicit human
authorization. It must not be bundled into an ordinary code or documentation
merge.

---

## 8. Handoff checklist

- [ ] Use synthetic, non-PHI fixtures only.
- [ ] Keep `MOCK_MODE=true` unless a non-PHI external evaluation is explicitly
      approved.
- [ ] Keep `ALLOW_PROVIDER_PERSONA=false`, `BAA_ATTESTED=false`,
      `PLATFORM_AGENT_API_ENABLED=false`, and `FEATURE_ATHENA_LABS=false`.
- [ ] Preserve seven-agent routing and the Magisterium doctrinal gate.
- [ ] Preserve the scrub-before-uncovered-service invariant.
- [ ] Treat NPPES data as referral information, never identity or credential
      proof.
- [ ] Make no Sovereign production, retention, EHR, or compliance claim.
- [ ] Leave the production latch off; a human remains the release authority.
