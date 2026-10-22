# Flight plan: dated factual timelines without generated chart claims

## Outcome and boundaries

The observed reviewed-visit timeline question could take the VISIT_PREP drafting
path and produce a prose synthesis while other factual chart questions showed
literal source excerpts. Recognized historical fact questions now use the same
authorized source-evidence lane when classified VISIT_PREP. Intentional upcoming
visit preparation retains its existing drafting behavior.

The closed reviewed-timeline request copies whole clinical sections, including
subjective, medication, objective, lab, assessment, plan, follow-up, screening
gap, observed/deferred exam, return precaution and risk calculation sections.
Known demographic/background history, allergy and immunization headings are
omitted with visible scope text; unrecognized headings remain included. Every
selected note must have a represented section.
Dates identify note versions, not proof of medication use or completed testing.
Disagreeing source wording stays literal. This is an excerpt chronology, not the
entire chart, a current medication list, an order ledger or general entailment.

Initial beta scope remains providers reading their own authorized visits.
No new capability, record scope, API, model call, dependency, schema or setting
is introduced. Required review, PHI boundaries, context freshness/finalization,
HIGH/ethics paths and genuine upcoming-visit preparation remain in force.
Legacy summary-only context cannot synthesize a recognized factual answer.

## Risk, dependency and release sequence

Tier 2: canonical provider answer dispatch and a cross-repository renderer pin.
The timeline uses the fixed `Reviewed visit timeline — exact source sections:`
envelope with at most 64 cards. Generic index-selected evidence retains its old
eight-card contract and one bounded selection call. Timeline selection has no
additional model call. Whole-section limits remain 3,000 characters each,
8,000 aggregate and 18,000 canonical characters (also 64 KiB UTF-8).
Overflow or an unrepresented note discards the complete timeline and gives an
actionable request to open source notes or ask about one visit date/fact.

Current reviewed provider source is Sovereign f4fa7a12; Core pins its 37f81d
panel asset. The new envelope is not supported by that deployed old asset.
Hold both candidates pending independent post-final-push reviews. A separate
Core asset-only PR must vendor the exact final Sovereign panel, source commit,
SHA/SRI and manifest with receiver tests. Deploy the compatible Core receiver
before the Sovereign sender, then verify both actual images and provider UI.
This document does not authorize a deployment or claim runtime acceptance.

No new flag is needed: existing Session Prep and canonical provider capabilities
still gate access. A recovery must pair the old Sovereign answer implementation
with its matching panel asset while retaining the current frontier-aware Core
backend/schema; restoring an old pre-frontier Core application is unsafe after
acknowledged transcript corrections. No data writes or migrations in this PR.

## Verification and completion

The explicitly fictional UI fixture contains all 81 visible sections from three
ordinary provider-read notes: 1,741 / 2,223 / 2,219 body characters. It re-expresses
rendered text as synthetic source payloads, not a canonical Markdown or review-
version export. The corrected clinical projection retains 36 sections / 3,898 characters.
The five-visit control retains all five dates and 60 clinical sections. Latest
date is expanded, older groups are native accessible disclosures; exact terminal
care-time/AI boilerplate remains accessible under Additional note text.

Local controls cover actual VISIT_PREP misclassification, all six factual
questions, ordinary preparation, mixed/action/quoted requests, legacy context,
denied authority/transport, required review, whole-response limits, unknown
clinical headings, markup/forged packet strings, footer preservation and live/
canonical-history rendering through the real public panel mount API. Local
checks do not establish model relevance, live BAA transport, fresh brief success,
or operational alert delivery.

The prior pushed 49de484 was rejected: its heading-only exclusions hid unique
follow-up, screening-gap, exam and return-precaution evidence despite reporting
validated excerpts. This descendant removes those exclusions and the deferred-
exam exclusion regex, and also retains risk calculations. Regression controls
use the exact independent synthetic counterexamples, preserving their entire
qualified text and dates in both live and restored history. The 64-card limit
fits the complete 60-card five-visit control without raising the unchanged raw,
canonical or UTF-8 text budgets. Prior failed review/evidence stays historical;
fresh independent review must bind the corrected final substantive push.

Complete only after independent source and Core-pin reviews, compatible runtime
rollout evidence and ordinary provider UI replay of the exact fictional timeline.
The fresh v5 prepared-brief gap remains separate; factual excerpts do not prove
that the generated brief is READY. No production promotion or cleanup here.
