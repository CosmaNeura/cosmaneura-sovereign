# Phase 0 Owner Action Items — BAA & Compliance Paperwork

**Owner:** John (CEO). No code knowledge required.
**Opened:** July 15, 2026
**Revised:** July 15, 2026 — production inference switched from Anthropic-first to
**Azure OpenAI–first (GPT-5.5)** to use CosmaNeura's $125k Azure credits for build
and test. The Anthropic BAA is **deferred, not dropped** (see the end of this doc).

**Why this document exists:** The provider (PHI/HIPAA) build is deliberately locked
in code until real legal paperwork is in place — the PHI path refuses to run unless
`BAA_ATTESTED=true`, and that flag may only be set once a Business Associate
Agreement (BAA) is actually in effect for the inference path we use. The items below
are the critical path. Each is written in plain language, ends with a "you're done
when" check, and is followed by a short **Developer follow-up** describing what
changes in this repository once the paperwork lands.

**What does NOT change with this revision:** Magisterium AI remains the doctrinal
authority and final arbiter — it is never substitutable and this switch does not
touch it. GPT-5.5 replaces only the *mechanical* role (routing, drafting, agent
reasoning) that Claude previously held by default.

---

## ☐ Action Item 1 — Confirm the Microsoft DPA/BAA **and** that the $125k credits cover Azure OpenAI

**Why it matters:** With Azure OpenAI as the production inference path, Microsoft's
HIPAA BAA (bundled in its **Data Protection Addendum**, DPA) is the legal basis for
`BAA_ATTESTED=true` — no separate AI-vendor BAA needed. It applies automatically to
most commercial subscriptions, but "most" is not a compliance answer: we need it
confirmed in writing for **our** subscription. Same conversation, second question:
credit programs sometimes exclude specific services, so confirm the $125k actually
burns on Azure OpenAI usage before building the plan around it.

**Steps:**

1. Log in to the Azure portal (**portal.azure.com**) with the account that owns the
   CosmaNeura subscription, and note the **subscription ID** and agreement type
   (usually "Microsoft Customer Agreement" — visible under *Subscriptions →
   Overview*).
2. Open a support request (or email the Microsoft account rep if we have one) and
   ask, in writing:
   - *"Please confirm that the Microsoft Products and Services Data Protection
     Addendum, including the HIPAA Business Associate provisions, applies to
     subscription `<subscription ID>`, and that Azure OpenAI Service and Azure
     Health Data Services de-identification are in scope."*
   - *"Please confirm our Azure credits ($125k) apply to Azure OpenAI Service
     usage, and tell us our subscription's Azure OpenAI quota tier."* (GPT-5.5
     deploys by default on Tier 5+; lower tiers need a quota request — see Action
     Item 2.)
3. Save the written confirmations with the company's compliance records.
4. Tell the developer the covered subscription ID and the quota tier.

**You're done when:** you have Microsoft's confirmation of both points in writing,
filed, and the developer knows the subscription ID and quota tier.

**Developer follow-up (in this repo):**
- Verify the resources in `infra/` (Azure OpenAI runtime, de-id service in
  `infra/deid.tf`, Container Apps) all live under the confirmed subscription ID,
  and record that subscription ID in `DEPLOY_AZURE.md`.
- The DPA confirmation is what justifies `BAA_ATTESTED=true` on the Azure OpenAI
  path — record that basis and date in `SETUP_AND_HANDOFF.md` so the attestation
  is traceable.

---

## ☐ Action Item 2 — Deploy GPT-5.5 in Azure AI Foundry and hand the developer the deployment names

**Why it matters:** The repo selects models by **Azure deployment name**
(`REASONING_MODEL` / `FAST_MODEL` in the production env are deployment names, not
model IDs). Someone with portal access — you, or you plus the developer on a short
call — has to create those deployments before the code can use them. GPT-5.5 has
been generally available in Microsoft Foundry since April 2026 and is offered in
eastus2 and most major US regions.

**Steps:**

1. Go to **ai.azure.com** (Azure AI Foundry) signed in under the confirmed
   subscription. Reuse the existing Azure OpenAI resource from the main
   CosmaNeura app if it's in a region that offers GPT-5.5; otherwise create a
   resource in one that does (e.g., eastus2).
2. If Action Item 1 revealed a quota tier below 5, submit the quota request for
   GPT-5.5 in that region (a form linked from the deployment screen; typically
   answered in days).
3. Create **two deployments** and write down the exact names you give them:
   - a **reasoning** deployment on GPT-5.5 (suggested name: `gpt-5-5-sovereign`)
   - a **fast/cheap** deployment on the current small model tier in the catalog
     (the "mini" variant of the newest GPT series — ask the developer or Foundry
     support which is current; suggested name: `fast-sovereign`)
4. In **Cost Management → Budgets**, set a budget on the subscription with email
   alerts (e.g., alert at $10k/month — the pace that spends $125k in a year) so
   credit burn is visible from day one.
5. Give the developer: the resource endpoint, the two deployment names, and
   confirmation the budget alert exists.

**You're done when:** both deployments exist, the budget alert is set, and the
developer has the endpoint + deployment names.

**Developer follow-up (in this repo):**
- Flip the production env (already scaffolded in `.env.production.backend.example`):
  `LLM_PROVIDER=azure_openai`, `REASONING_MODEL=<gpt-5.5 deployment name>`,
  `FAST_MODEL=<fast deployment name>`, endpoint/key via the existing 1Password
  references.
- Check `AZURE_OPENAI_API_VERSION`: the repo default (`2025-03-01-preview` in
  `app/config.py`) predates GPT-5.5 — confirm the current GA API version in the
  Azure OpenAI docs and update the env (and the `app/config.py` default).
- Consider flipping the code default `llm_provider` from `"anthropic"` to
  `"azure_openai"` in `app/config.py` to match reality — developer's call.
- Note the known trade-off in `SETUP_AND_HANDOFF.md`: the optional inline
  Magisterium **MCP advisory layer only attaches on the Anthropic path**
  (`app/clients/llm.py`) and is unavailable on Azure OpenAI. It is off by default
  and the final Magisterium REST arbitration gate is completely unaffected.
- Run `PYTHONPATH=. python -m pytest tests/ -q` (all 28 must pass), then a live
  smoke test with `MOCK_MODE=false` against the new deployments — **synthetic
  prompts only**; the go-live gates below still apply before any real PHI.

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
2. Complete the BAA questionnaire at **x.ai/legal/baa**. Describe the use case
   plainly: healthcare API, provider build handles PHI, requesting a BAA for API
   usage.
3. Record the outcome either way — an executed BAA, or a written "not available /
   not yet" — and file it with the compliance records.
4. Tell the developer the outcome, so the Phase 1 model-policy configuration marks
   the xAI path correctly.

**You're done when:** the questionnaire is submitted and the outcome (signed BAA or
a documented "no") is filed and communicated to the developer.

**Developer follow-up (in this repo):**
- In the Phase 1 gateway work, the xAI provider adapter gets
  `baa_covered: false` until an executed xAI BAA is on file — never before.
- If xAI declines or stalls, nothing blocks: Azure OpenAI is the BAA-covered path
  for the provider build; xAI stays patient-persona-only.

---

## Deferred — Anthropic BAA (with Zero Data Retention)

Previously Action Item 1; deferred now that Azure OpenAI is the production path.
Revisit when either becomes true:

- **Phase 1 gateway** wants Anthropic as a second BAA-covered provider for
  redundancy or per-role model policy; or
- we want the optional **inline Magisterium MCP advisory layer**, which only works
  on the Anthropic first-party path.

When revisited: submit the inquiry at **privacy.anthropic.com** (API BAA with Zero
Data Retention; available to usage-based API customers — does **not** require an
Enterprise plan). Until countersigned, the Anthropic path stays `BAA_ATTESTED=false`
and is patient-persona / synthetic-data only.

---

## How these fit the go-live gate

No real patient or provider traffic until **all** of the following are true — each
maps to a switch or setting the developer controls:

| Gate | Where it lives | Unblocked by |
|---|---|---|
| Executed BAA on the inference path | `BAA_ATTESTED` (env → `app/config.py`) | Action Item 1 (Microsoft DPA confirmation for the Azure OpenAI path) |
| HIPAA-grade de-identification | `DEID_PROVIDER=azure` (replaces the regex baseline) | Action Item 1 (de-id service in DPA scope) + Phase 5 validation |
| Persona bound to verified identity | `AUTH_MODE=oidc` + production IdP values | Separate deployment task (Phase 5) |

**Credits ≠ compliance:** the $125k lets us build and test aggressively, but every
test before the gates above pass uses **synthetic or de-identified data only**.

Items not in this document (WORM audit store, rate limiting, pen test, counsel
review) remain tracked as Phase 5 hardening work.
