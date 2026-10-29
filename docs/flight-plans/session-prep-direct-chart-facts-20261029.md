# Session Prep direct chart facts

Tier 2: provider chart-answer routing; no authority, identity or schema change.

## Trigger and resulting behavior

The current staging provider image returned prose for the synthetic question
"What appointment time does Don prefer?", while returning dated excerpts for
other facts. A scripted reproduction shows that short fact questions use the
excerpt lane only when the initial label is Engagement; Visit Prep synthesizes
prose and other incorrect labels can deny the same lookup.

Apply the existing closed, non-advisory patient-fact matcher before dispatch for
known non-doctrinal labels. On a canonical provider turn with both education and
Session Prep record grants, select the existing reviewed-note answer lane.
Ethics/doctrine and unknown intents keep their routes. External retrieval,
actions, recommendations and mixed questions keep the existing classifier and
capability checks. No specialist capability is enabled.

## Invariants and verification

Keep accepted-turn persistence, provider/BAA resolution, current own-record
authority, exact source versions and final receipt validation. Matching a name
in a question never chooses the canonical patient. Missing feature/record grants
return a static unavailable answer before any incorrectly labelled specialist
dispatch, rather than falling through to generic knowledge or external tools.
A legacy summary cannot become an original-note quote.
Doctrinal review still runs and factual replies keep its existing badge policy.

Verify misclassification across the known labels, actual synthetic preference
text/date, missing grants, unsupported legacy context, external/action refusals
and retained ethics routing. Repeat the Don included/excluded questions against
the exact deployed provider image during integrated staging acceptance.

## Release and recovery

Merge only after independent exact-head review. Publish an immutable provider
image and deploy through the reviewed staging release path with the current Core
pair, preserving Scribe and public Sovereign. No production activation is part
of this source change. No migration or configuration addition is required.

If live acceptance fails, withhold the Session Prep lane or recover to the
retained secure PR89 provider digest `89bc55e07dd10c0c57e330887ccff70fdbeee37fb527c27e4596bc5d7eacf535`
under the reviewed deployment procedure. Never recover to the earlier image
containing the rejected trust bundle. Historical saved answers are retained;
this change governs new questions and does not rewrite prior accepted turns.
