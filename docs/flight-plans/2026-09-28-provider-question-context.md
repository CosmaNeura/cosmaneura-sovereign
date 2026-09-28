# Provider questions from the prepared visit summary

Tier 2: provider context and an additive cross-repository contract. Core remains
the authority for the current reviewed sources; Sovereign receives only their
bounded prepared summary. The existing Session Prep release control and provider
cohort apply. No new cohort, raw-note reader, database migration, browser context
field, or Scribe behavior is introduced.

Core publishes `provider-question.context.read@1.0.0` and transactionally checks
its receipt when accepting a canonical final response. Sovereign mirrors the
contract in `contracts/schemas/provider-question-context-v1/`. Visit Prep always
reads; Engagement and Ethics read when a BAA-side classifier determines that the
current question needs prior recorded context. General education skips the read.
READY supplies reviewed-summary facts to those agents only. Missing details must
be described as unavailable, not inferred. Pending/unavailable receipts finish
with outcome `unavailable` and direct the clinician to open Session Prep, then
ask a new question. There is no automatic generation or conversation reload.
Record selection uses the current question only. Earlier chat history is not
restored or represented as reviewed context; the source is a summary, not notes.

Release owner: the current staging release operator. Independently review and
merge both changes; deploy the backward-compatible Core receiver first, then
Sovereign through the canonical local staging release path under the existing
same-conversation delegation while Actions are disabled. Pin exact merged SHAs,
image digests, deployed baselines, and recovery revisions in the release evidence.
The Document format correction ships with the Sovereign consumer.

Before declaring the existing synthetic provider cohort functional, verify a
general education question, a documented prior follow-up question, a requested
fact absent from the summary, pending/unavailable recovery, Document download,
and canonical dependency acceptance. Verify source/context changes prevent answer
egress. Local tests use fictional inputs and mocked transports; live model
grounding quality still requires that synthetic staging check.

Recover by restoring the recorded prior Sovereign revision first. Core's additive
receiver can remain deployed for legacy senders; no data reversal is required.
The existing `FEATURE_SESSION_PREP` control can withdraw Session Prep and these
context reads together through the normal staging configuration process. Keep
the existing provider cohort fixed during release and recovery.
Preserve the public mount and all Scribe services. Existing source-state drift
must be reconciled explicitly before either staging deployment.
