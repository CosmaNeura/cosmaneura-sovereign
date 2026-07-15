# Phase 0 Owner Action Items — BAA & Compliance Paperwork

**Owner:** John (CEO). No code knowledge required.
**Opened:** July 15, 2026
**Why this document exists:** The provider (PHI/HIPAA) build is deliberately locked
in code until real legal paperwork is in place — the PHI path refuses to run unless
`BAA_ATTESTED=true`, and that flag may only be set once a Business Associate
Agreement (BAA) is actually signed. The three items below are the paperwork on the
critical path. Each one is written in plain language, ends with a "you're done when"
check, and is followed by a short **Developer follow-up** describing what changes in
this repository once the paperwork lands.

---

## ☐ Action Item 1 — Execute a BAA with Anthropic (with Zero Data Retention)

**Why it matters:** Anthropic's Claude is the default reasoning LLM for this service.
For the provider build to ever see PHI on the Anthropic path, CosmaNeura needs a
signed BAA with Zero Data Retention (ZDR). Anthropic offers this to API customers on
usage-based billing — it does **not** require an Enterprise plan. (The ~50-seat
Enterprise BAA applies only to the Claude.ai chat product, which is not what this
service uses.)

**Steps:**

1. Create an Anthropic Console account at **console.anthropic.com** using
   **john@cosmaneura.com**. Set it up as a company/organization account, not a
   personal one, and enable two-factor authentication.
2. Go to **privacy.anthropic.com** and submit a BAA inquiry. In the description,
   state plainly:
   - CosmaNeura is a healthcare technology company building an API product for
     clinicians (a HIPAA covered-entity context).
   - The product sends protected health information (PHI) to the Claude API in its
     provider build, so you are requesting a **BAA with Zero Data Retention** for
     API usage.
   - You are (or will be) a usage-based API customer.
3. While you wait for a response: it is fine for the developer to keep building and
   testing, but **only with synthetic or de-identified data** — no real patient
   information touches any AI vendor until the BAA is countersigned.
4. When the countersigned BAA arrives, save the PDF in the company records and the
   password manager, and note the effective date.
5. Tell the developer: *"Anthropic BAA with ZDR executed, effective `<date>`."*

**You're done when:** the countersigned BAA PDF is stored, and the developer has the
effective date in writing.

**Developer follow-up (in this repo):**
- Create the production API key **inside the BAA-covered Anthropic organization**
  and store it as the 1Password item referenced in
  `.env.production.backend.example` (`op://Production/anthropic-sovereign-baa/credential`).
- In the production env: `LLM_PROVIDER=anthropic` (if choosing the Anthropic path
  over Azure OpenAI) and only then `BAA_ATTESTED=true`
  (see `app/config.py` — `baa_attested` is the legal attestation switch; the PHI
  path fails closed without it).
- Record the BAA effective date in `SETUP_AND_HANDOFF.md`.
- Run `PYTHONPATH=. python -m pytest tests/ -q` and confirm all tests still pass.

---

## ☐ Action Item 2 — Confirm the Microsoft DPA/BAA covers our Azure subscription

**Why it matters:** The production configuration currently points at **Azure
OpenAI** (`LLM_PROVIDER=azure_openai`), reusing the same Azure resource the main
CosmaNeura app already uses, and the HIPAA-grade de-identification service is also
an Azure product. Microsoft's HIPAA BAA is bundled inside its **Data Protection
Addendum (DPA)** and applies automatically to most commercial subscriptions — but
"most" is not a compliance answer. We need it confirmed in writing for **our**
subscription.

**Steps:**

1. Log in to the Azure portal (**portal.azure.com**) with the account that owns the
   CosmaNeura subscription, and note the **subscription ID** and agreement type
   (usually "Microsoft Customer Agreement" — visible under *Subscriptions →
   Overview*).
2. Open a support request (or email the Microsoft account rep if we have one) and
   ask, in writing: *"Please confirm that the Microsoft Products and Services Data
   Protection Addendum, including the HIPAA Business Associate provisions, applies
   to subscription `<subscription ID>`, and that Azure OpenAI Service and Azure
   Health Data Services de-identification are in scope."*
3. Save the written confirmation with the company's compliance records.
4. Tell the developer which subscription ID is confirmed as covered.

**You're done when:** you have Microsoft's confirmation in writing, filed, and the
developer knows the covered subscription ID.

**Developer follow-up (in this repo):**
- Verify the resources in `infra/` (Azure OpenAI runtime, de-id service in
  `infra/deid.tf`, Container Apps) all live under the confirmed subscription ID,
  and record that subscription ID in `DEPLOY_AZURE.md`.
- If production stays on the Azure OpenAI path, this confirmation (not the
  Anthropic BAA) is what justifies `BAA_ATTESTED=true` — note which one applies in
  `SETUP_AND_HANDOFF.md` so the attestation is traceable.

---

## ☐ Action Item 3 — Submit the xAI BAA questionnaire (Grok, future 4th provider)

**Why it matters:** The Phase 1 multi-model gateway adds xAI/Grok as a fourth model
provider. There is **no xAI code in this repository yet** — that is expected — but
BAA paperwork is always the slowest step, so it starts now. Until a BAA exists,
the gateway will mark the xAI path as *not* BAA-covered, meaning it can only ever
serve the patient persona.

**Steps:**

1. Create an xAI console account at **console.x.ai** using **john@cosmaneura.com**
   (company account, two-factor authentication on).
2. Complete the BAA questionnaire at **x.ai/legal/baa**. Describe the use case the
   same way as in Action Item 1: healthcare API, provider build handles PHI,
   requesting a BAA for API usage.
3. Record the outcome either way — an executed BAA, or a written "not available /
   not yet" — and file it with the compliance records.
4. Tell the developer the outcome, so the Phase 1 model-policy configuration marks
   the xAI path correctly.

**You're done when:** the questionnaire is submitted and the outcome (signed BAA or
a documented "no") is filed and communicated to the developer.

**Developer follow-up (in this repo):**
- In the Phase 1 gateway work, the xAI provider adapter gets
  `baa_covered: false` until an executed xAI BAA is on file — never before.
- If xAI declines or stalls, nothing blocks: Anthropic and Azure OpenAI remain the
  BAA-covered paths for the provider build; xAI stays patient-persona-only.

---

## How these fit the go-live gate

No real patient or provider traffic until **all** of the following are true — each
maps to a switch or setting the developer controls:

| Gate | Where it lives | Unblocked by |
|---|---|---|
| Executed BAA on the inference path | `BAA_ATTESTED` (env → `app/config.py`) | Action Item 1 **or** 2 (whichever path production uses) |
| HIPAA-grade de-identification | `DEID_PROVIDER=azure` (replaces the regex baseline) | Action Item 2 |
| Persona bound to verified identity | `AUTH_MODE=oidc` + production IdP values | Separate deployment task (Phase 5) |

Items not in this document (WORM audit store, rate limiting, pen test, counsel
review) remain tracked as Phase 5 hardening work.
