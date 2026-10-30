# Session Prep factual review and interrupted-response recovery

Tier 2: provider chart-answer arbitration, factual routing and panel transport.
This is authorized isolated source work. It does not activate a cohort, alter
record authority, change configuration or schema, or publish/deploy an image.

## Problem and outcome

Staging returned the static required-review failure for the full synthetic Bob
dose-correction, Sara metformin/A1C and Don medication/blood-count questions.
The exact live adjudication disposition is not yet proven. An offline
reproduction separately proves that the current refusal regex rejects an
affirmative historical-reporting policy judgment accompanied only by a
clinical-accuracy limitation, before the strict verdict parser can run.

Distinguish that narrow limitation only inside the internally validated
LOW/ENGAGEMENT historical-policy wrapper. Require an explicit, unconditional,
unquoted policy judgment and complete clinical-only limitation sentences. All
other refusal matches deny. The complete Magisterium assessment still passes
through the existing strict boolean parser; no judgment is inferred or repaired.
No original-note value is added to the PHI-neutral review projection.

The observed short symptom/follow-up chart questions also receive dated source
excerpts rather than synthesized prose through the existing closed matcher.
Only recorded breathing, chest-pain or nausea findings and recorded/scheduled
follow-up shapes are added. External requests, extra care instructions,
recommendations, ethics and broad overviews retain their existing routing and
capability gates. A matcher never grants patient or record authority.

Browser chat initialization is bounded to 30 seconds before submission. A
submitted request retains the provider's 180-second terminal-response budget
plus a 10-second browser allowance, then releases controls and preserves the
question. Progress appears after 30 seconds. The existing saved-response check
and explicit retry retain their canonical body/UUID and record generation.
There is one submission per user action; no automatic resend. Late callbacks,
responses and old-generation timers cannot render into or unlock another turn.

## Current state and release dependencies

Retain the existing staging pair pending independent review and live retest:

- Core source `9a6fc4635c81e300312f44f212390d03f19e6c72`, PR894, digest
  `17292b82478794f59ea00fba8989717c69bb6f8d508f3171f794c24666ae8eaf`.
- Provider source `d1b32d84b61a34f61427429b957d056f1636dba2`, digest
  `92ee6f32a4419be01ae525058441d8009a92a58072f7e7b1b16cd3e91f35c684`.

The browser asset is pinned and vendored by Core. Its companion pin amendment
must use the final exact `app/panel.js` bytes, SHA-256 and matching SRI. Deploying this
provider source alone does not update the Core-hosted panel. The two source
changes can be held independently; do not silently replace a runtime asset or
reduce integrity validation. Scribe and public Sovereign stay outside scope.

No new flag is needed: existing Session Prep mount/access controls govern this
lane, and rollback is the retained reviewed source/image plus its matching Core
asset. Sovereign owns no schema; migrations and stored-data rewrites are N/A.
Existing accepted answers and turn history remain unchanged.

## Verification and hold points

Run adversarial offline assessment checks covering clinical-only limitations,
genuine doctrinal/policy refusals, mixed scope, missing judgments, conditional
or quoted conformance, negative/duplicate/non-boolean/inconsistent parser
results, and ordinary/HIGH review preservation. Verify actual selector/review/
parse/final-receipt calls and the unchanged neutral outbound policy text.

Run virtual-clock panel tests for stalled bearer/context/fetch/body reads,
deadline relative to the server budget, progress, exact retry with fresh auth,
saved-answer lookup without inference, callback rejection after cleanup,
record switch/destroy and independent newer-request finalization.

Complete independent non-implementer review against the final substantive head
before publishing a PR; record that exact review again after the final push as
required by AGENTS.md. The parent task owns review and any release decision.

Live release acceptance must repeat the full approved synthetic Bob/Sara/Don
questions, verify dated exact excerpts and correction-versus-treatment-history
wording, then compare immediate and restored saved-answer bytes. The offline
reproduction does not establish a live vendor verdict or close those gates.
If any supported factual question still fails review, withhold that gate and
investigate with status-only evidence; never bypass the required authority.

Production activation remains a separate founder-controlled decision. No
production action or administrative/compliance task is included here.
