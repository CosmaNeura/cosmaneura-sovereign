# Provider Copilot — Visit Prep + Ethics Consult (Agents 4 & 5)

**Status:** scaffolded and tested in this branch (mock-mode, offline).
**Origin:** two real demand signals, one week apart:

1. **The everyday case (Scott, fiathealth.org).** A therapist on the platform
   pastes each session's transcript into a consumer ChatGPT conversation and
   asks for next-session talking points ("keep it clear and simple, I don't
   have a lot of time"). That workflow works — and it moves behavioral-health
   session content outside any BAA boundary, per-client, every week.
2. **The deep case (clinician group chat).** "I do not know of any clinical
   decision tools for ethical advice in healthcare AI." OpenEvidence confirmed
   on the record it has no Catholic-perspective content, no Magisterium
   agreement, and no Linacre Quarterly corpus.

This document describes how both become native capabilities of the Sovereign,
behind **one chat surface**.

---

## 1. One chatbot, routed lanes

The clinician gets a single assistant. The existing router decides which chain
fires — no mode switch, no separate tools:

| The clinician says… | Intent | Chain | Doctrinal gate |
|---|---|---|---|
| "Help me organize my next session with this client…" | `visit_prep` | Agent 5, BAA LLM only | Light (low tier; full if moral weight detected) |
| "My patient is asking about tubal ligation — permitted under the ERDs?" | `ethics_consult` | Agent 4 + PubMed/Linacre evidence + Magisterium | **Always full arbitration (forced HIGH)** |
| "Explain this patient's new diagnosis before the visit" | `engagement` | Agent 3 (existing) | Tiered |
| Referral / trials / teaching questions | existing intents | existing chains | Tiered |

Visit prep is expected to be the **high-frequency** lane; the ethics consult is
the **high-stakes** lane. The tiered gate keeps the frequent lane fast and
cheap while the Sovereign's full chain backs the moments that matter.

## 2. Agent 5 — Visit / Session Prep ("the Planner")

Replicates the ChatGPT workflow natively, with three things the side-channel
can never offer: **BAA containment** (transcripts go only to the BAA-covered
LLM), the **audit chain** (every prep is hash-logged), and the **Catholic
overlay** (drafts pass the arbitration gate like everything else).

- **Input:** the request plus `metadata.prior_sessions` (most recent
  transcript/notes — supplied by the calling app), `metadata.care_model`
  (e.g. "internal family systems"), `metadata.terminology`,
  `metadata.detail` (`brief` default).
- **Output:** 2–3 sentence recap → 3–5 talking points → short outline.
  Brief by default — the direct user feedback was that ChatGPT's version is
  "a little too much."
- **Terminology toggle:** behavioral-health users asked for
  "client/therapist" instead of "doctor/patient." `metadata.terminology`
  carries the pair; the calling UI owns the setting.
- **Boundaries in the prompt:** preparation, not prescription; no diagnosis;
  the session goes wherever the patient needs; the stated care model is
  respected, not evaluated.

**Compliance note for counsel (Phase 5):** therapy-session material may
qualify as *psychotherapy notes* under HIPAA (45 CFR 164.508(a)(2)), which
carry stricter authorization rules than general PHI. The architecture already
confines this content to the BAA path; counsel should confirm consent
language before live behavioral-health traffic.

## 3. Agent 4 — Clinical Ethics Consult ("the Consultor")

The ethics-CDS capability the market lacks: **conformance, not retrieval**.
Evidence tools (OpenEvidence et al.) answer *what does the literature say*;
this agent prepares *what may I do, and how, in an ERD-bound institution* —
and every draft is then reviewed by Magisterium, with the tiered gate
**bypassed by design** (an ethics consult is doctrine by definition, so the
orchestrator forces full arbitration regardless of the router's tier).

- **Structure of a consult prep:** the question restated in ERD terms →
  applicable directives cited by number (6th ed.) and moral principles
  (double effect, ordinary/extraordinary means, cooperation) → morally licit
  alternatives → explicit flag when the ERDs involve the ethics committee or
  diocesan bishop → concrete points to bring to the consult.
- **What it never does:** replace the ethics committee or render final moral
  judgment; give clinical recommendations.
- **Evidence grounding:** dual PubMed pull via the new `clients/pubmed.py` —
  general clinical literature plus a Linacre Quarterly-filtered search (the
  CMA's peer-reviewed journal is reachable through the same free NCBI API).
  Dual citations: USCCB ERDs on one side, PubMed literature on the other.

## 4. Evidence connections — current reality

| Source | Status |
|---|---|
| **PubMed / PMC (NCBI E-utilities)** | **Wired in this branch** (`clients/pubmed.py`), free, keyless (optional key raises rate limits), Linacre-filter built in |
| ClinicalTrials.gov v2, NPPES | Already wired (Agents 2, 1) |
| openFDA, RxNorm, MedlinePlus Connect | Free public APIs; adapters follow the same client pattern when needed |
| **OpenEvidence** | **No public API** (gated enterprise docs; integrations are negotiated partnerships). Two paths: (a) *bring-your-own-evidence* — a clinician pastes an OpenEvidence answer and the Sovereign reviews it for ERD conformance, which works today with zero integration; (b) the enterprise-partnership track via contact@openevidence.com. No technical dependency either way. |

**PHI invariant, extended:** PubMed joins Magisterium, ClinicalTrials.gov and
NPPES as a non-BAA hop — topic-only queries, derived from de-identified text,
with the same hard `assert_no_phi` tripwire in the client (regression-tested).

## 5. Integration contract for the main app (Cosmas dashboard bot)

The dashboard chatbot in app.cosmaneura.com calls the same single endpoint:

```
POST /chat
Authorization: Bearer <clinician JWT>     → provider persona (verified)
{
  "persona": "provider",
  "message": "<what the clinician typed>",
  "metadata": {
    "prior_sessions": "<latest transcript/notes text>",   // visit prep
    "care_model": "internal family systems",              // optional
    "terminology": "client/therapist",                    // UI setting
    "case_context": "<case facts>"                        // ethics consult
  }
}
```

The response's `intent` tells the UI which lane answered; `structured` and
`citations` carry the machine-readable payload (ERD/PubMed references for
consults, prep metadata for visit prep). Patient-journey stringing (multiple
sessions rather than the latest) is a data question for the calling app — the
Sovereign accepts whatever `prior_sessions` contains, within request limits.

## 6. Scaffolded here vs. next

**In this branch:** both agents, the PubMed client, router + mock-classifier
coverage, forced-HIGH arbitration rule, two new demo scenarios, five new
tests (33 total, all passing offline).

**Next:** prompt tuning against real transcripts; Cosmas UI hookup (chat
panel + terminology setting); history-aware routing (see
`INTERACTION_DESIGN.md` §7); slot-extracted PubMed queries; counsel review of
psychotherapy-notes handling before live behavioral-health traffic.
