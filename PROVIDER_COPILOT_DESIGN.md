# Provider Copilot — Visit Prep, Ethics Consult & Plan Workbench (Agents 4–6)

**Status:** scaffolded and tested in this branch (mock-mode, offline).
**Origin:** three real demand signals from user calls:

1. **The everyday case (Scott, fiathealth.org).** A therapist on the platform
   pastes each session's transcript into a consumer ChatGPT conversation and
   asks for next-session talking points ("keep it clear and simple, I don't
   have a lot of time"). That workflow works — and it moves behavioral-health
   session content outside any BAA boundary, per-client, every week.
2. **The deep case (clinician group chat).** "I do not know of any clinical
   decision tools for ethical advice in healthcare AI." OpenEvidence confirmed
   on the record it has no Catholic-perspective content, no Magisterium
   agreement, and no Linacre Quarterly corpus.
3. **The investigative case (Sandy, CNS nutritionist, functional medicine).**
   Current workflow: Zoom transcript → hand-strip identifiers → paste into
   ChatGPT/OpenEvidence → fix its errors → Word → PDF. Feeds it years of
   labs plus her own clinical assessment; asks for analysis and plan
   "separate from the SOAP note." Reported failure that shaped the design:
   asked for a 1,500–1,600 kcal plan, got 1,400s — twice.

This document describes how all three become native capabilities of the
Sovereign, behind **one chat surface**.

---

## 1. One chatbot, routed lanes

The clinician gets a single assistant. The existing router decides which chain
fires — no mode switch, no separate tools:

| The clinician says… | Intent | Chain | Doctrinal gate |
|---|---|---|---|
| "Help me organize my next session with this client…" | `visit_prep` | Agent 5, BAA LLM only | Light (low tier; full if moral weight detected) |
| "My patient is asking about tubal ligation — permitted under the ERDs?" | `ethics_consult` | Agent 4 + PubMed/Linacre evidence + Magisterium | **Always full arbitration (forced HIGH)** |
| "Her labs are back, the standard of care isn't working — which levers?" | `plan_workbench` | Agent 6 + deterministic calculators + PubMed | Tiered (raised when morally weighted) |
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

## 3b. Agent 6 — Care Plan Workbench ("the Investigator")

The post-visit deep-dive lane, designed with Sandy's transcript as the spec.
The right-panel "investigative side" promised on her call: labs + history +
the clinician's own assessment in; a levers-organized analysis out — always
separate from the SOAP note, never auto-merged into the chart.

Design rules (each traceable to her words):

- **The clinician's assessment is the anchor.** She uses AI to confirm and
  stress-test her hypothesis (the mast-cell case), not to replace it. The
  prompt demands honest agreement/disagreement with evidence — a stress-test,
  not a yes-man.
- **Numbers are computed, never generated.** `app/agents/calculators.py`
  computes BMI, Mifflin-St Jeor BMR, calorie-target parsing, and macro grams
  in Python; values are injected as an authoritative `<computed_values>`
  block, echoed to the UI in `structured.computed` (rendered as "✓ Verified
  calculations — computed, not generated"), and the model is forbidden to
  estimate or recompute. Missing inputs degrade to "numbers unavailable,"
  never to invented numbers. Her 1,400-vs-1,500 failure is a regression test.
- **Scope is state-dependent.** `metadata.note_template` is honored verbatim,
  including audit-defense sections ("Education provided") that protect a CNS
  whose documentation posture varies by state.
- **Drug–nutrient interactions** (her statin→CoQ10 class of insight) are
  raised as items to *verify* in a licensed reference (Natural Medicines
  Database — a future licensed integration), never asserted from model memory.
- **Inputs:** `metadata.labs` (multi-year values as text for now),
  `case_context` (history + the clinician's assessment), `patient_metrics`
  (sex/age/weight/height for the calculators), `calorie_target`,
  `macro_split`, `note_template`, `output_format`.
- **Evidence:** PubMed pull on the de-identified topic; openFDA/RxNorm and a
  USDA FoodData Central client (deterministic food data for a future meal-plan
  feature) are the next adapters when needed.

## 3c. Output flexibility (all provider lanes)

Per direct request, every provider lane supports three output shapes via
`metadata.output_format`, echoed back in `structured.output_format`:

| Format | Behavior | Real workflow it serves |
|---|---|---|
| `chat` (default) | Conversational reply | Quick questions |
| `note` | Clean clinical text, no chat artifacts | The copy-paste into Athena both users described |
| `document` | Titled, sectioned markdown | Sandy's Word→PDF patient deliverables (meal plans, lab explanations) |

Unknown values fall back to `chat`. The standalone UI shows the selector for
the provider persona only, with Copy (note/document) and Download (document)
actions on the bubble.

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

**In this branch:** Agents 4, 5 and 6; the PubMed client; the deterministic
calculators module; the three-way output selector across provider lanes;
router + mock-classifier coverage; forced-HIGH arbitration rule; three new
demo scenarios (8 total); test suite at 37 passing offline.

**Next:** prompt tuning against real material (a redacted long-form
write-up, a sample multi-year lab set, real note templates); Cosmas UI
hookup (chat panel + terminology setting + output selector); history-aware
routing (see `INTERACTION_DESIGN.md` §7); slot-extracted PubMed queries;
USDA FoodData Central client for deterministic meal-plan data; counsel
review of psychotherapy-notes handling before live behavioral-health
traffic.
