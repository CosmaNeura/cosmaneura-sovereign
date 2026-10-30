# Session Prep review disposition observation

Risk: **Tier 2** because this is a provider-path operational diagnostic. Scope is
isolated source preparation only; no publication, deployment or configuration
change is included.

## Problem and change

The platform-mode local audit deliberately drops arbitrary `details`. Therefore
`arbitration` loses even its conformance Boolean. The durable request-scoped
platform observer carries a closed terminal status, not the review cause. A
failed turn cannot distinguish an explicit negative judgment, scope refusal,
invalid parser result, empty assessment or a review-dependency failure.

Add one `review_disposition.<closed enum>` event through the existing local audit
channel. It contains the existing generated trace, time, persona, event and owner
only. No reviewer prose, note value, clinical identifier, token, raw error or
content digest is added. Unknown private dispositions become a fixed unknown
code. Only an exception during the review call yields review upstream failure.

The required review, verdict, egress visibility, access grants, configuration,
API models and durable observer contract stay unchanged. No migration, release
flag or new dependency applies. The existing profile audit gate and platform
switch remain in force. The legacy offline sink keeps its existing shape.

## Validation

Use offline mocked provider turns to cover affirmative and negative judgments,
scope refusal, malformed verdicts, empty/malformed assessments, review upstream
failure and pre-review drafting failure. Verify unchanged unavailable closures,
presentation, persistence and terminal observer status. Verify exact breadcrumb
keys, closed event values, no sentinel review/draft/error/identifier payload,
and no platform-mode local file/chain. Test hostile private disposition objects
without coercing them. Run existing strict verdict, historical wrapper, platform
audit and provider-context regression suites.

## Release verification and recovery

After a separately reviewed release, verify the deployed source and collect the
correct provider stderr stream for a fresh generated trace. Re-run each live
factual-answer refusal. A collected disposition for that trace identifies the
code outcome; correlate it with the existing final failed/completed observation.
No `AUDIT-OP` rows in an earlier sample proves neither runtime switch values nor
vendor cause. A missing new event remains an unclosed collection/reachability
gate; source tests do not establish live diagnostic delivery.

Rollback to the preceding provider image removes only the added events. No data,
schema, access, review policy, deployed configuration or Core artifact requires
rollback from this source change.
