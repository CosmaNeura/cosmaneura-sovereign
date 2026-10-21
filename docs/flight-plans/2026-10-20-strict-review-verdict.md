# Strict adjudication and truthful factual failure presentation

Risk: Tier 2, provider factual-answer adjudication. Source-only preparation is
authorized within the Session Prep operational-beta work. Initial beta scope
is providers' own visits; RN delegation is deferred.

## Change

Require the private adjudication object to contain `conforms` as an exact
boolean. `needs_revision` is optional and must be an exact boolean if present.
Unknown fields and simultaneous `conforms=true` / `needs_revision=true` are
unusable judgments. They deny conformance without truthiness or coercion.
An opt-in JSON parser rejects duplicate fields for adjudication only; other
classification callers retain their existing parsing behavior.
After the required reviewer call, a non-string or empty/whitespace assessment
is unavailable before either the refusal check or parser. Its internal verdict
contains only static failure wording and no reviewer citations; no parser or
retry can supply the absent assessment.
Required Magisterium review, the existing refusal gate, prompts, PHI handling,
authority, routing and model-call order remain unchanged. There are no retries.

An internal Pydantic private attribute distinguishes an unusable verdict from
genuine nonconformance/refusal without changing the wire schema. A future
factual failure from an unusable verdict receives a static interpretation-
unavailable answer. Other factual review denials retain the existing static
answer. Canonical persistence retains the actual safety/source-scope prose.
Absent assessments share the static factual interpretation failure. On other
answer paths, this absence alone replaces the draft with static unavailable
content and uses existing unavailable canonical/presentation semantics.
An already unavailable selection still receives required review and retains
its selection-primary static failure regardless of that review's result.

After required review, factual unavailability suppresses intent/doctrinal
badges, citations and cards and clears transient doctrine/citation payloads.
Successful factual answers and other doctrinal/revision/referral/LAB paths
retain their presentation. No reviewer or parser text is newly logged or
exported by factual failures.

## Evidence limits

The prior live Q1 closed with five successful SDK calls and a review rejection.
Its private assessment/parser output was discarded. Local hostile-value
reproductions establish a source guard defect and diagnostic ambiguity; they
do not establish the historical Q1 cause or demonstrate live acceptance of
this successor. Duplicate-key rejection is scoped to the private adjudication
call and does not claim broader changes to routing/classification parsing.

## Release and recovery

1. Run credential-neutral offline arbiter, factual canonical-presentation and
   compatibility checks. Bind an independent review to the final pushed SHA.
2. Root owns merge and any separate staging release decision after acceptance.
   No deployment, Core/panel/pin/config/schema change is included in this PR.
3. Preserve the deployed source/image/spec until an exact merged source image
   has its normal independent preflight. Recheck the same synthetic Q1 and
   successful factual questions after that separately authorized release.
4. Recovery is a source revert followed by normal exact-image promotion. It
   would restore the old coercion/presentation behavior; do not treat it as a
   way to admit invalid adjudication or bypass required review.

Independent review is pending; author tests are not independent acceptance.
