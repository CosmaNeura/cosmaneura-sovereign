# Visit Prep patient clarification integration

This branch adds the bounded patient clarification generator to Sovereign and
reuses its existing model client and Core usage reporting. It supports the
approved five-topic Visit Prep interview through a dormant Core-workload receiver,
changing provider Session Prep or authorizing live analysis. Core continues to
own sources, proposals, patient decisions, the provider package and retention.

## Implemented behavior

The generator accepts one Core-protected topic and at most two separately
recorded patient follow-ups. Its closed input contains no preparation, patient,
appointment or credential identifiers. It makes one bounded metered model call
and accepts only an unused neutral follow-up code, exact nonoverlapping patient
excerpts, or an unavailable result. It rejects new prose, invalid quotations,
additional fields, repeated prompts, third questions and incomplete usage
recording. Patient text cannot invoke tools or broaden the interview.

`VISIT_PREP_PATIENT_CLARIFICATION_ENABLED` defaults off. The private generator
also requires Azure inference configuration, BAA attestation and an already
active canonical usage context. Mock production completion is refused. These
checks do not establish actual contractual coverage, patient authorization or
that supplied text passed Core's clinical de-identifier. Its eventual host must
prove those facts. The existing non-PHI tripwire is defense in depth, not an
alternative to Core protection.

## Paired Core connection

Core's paired branch selects current context under the existing patient
authority, applies Core clinical de-identification outside database locks and
maps accepted excerpts back only through unchanged original words. Core's
ordinary proposal and explicit patient-decision commands remain authoritative.

The dedicated receiver accepts only a signed app-only Core workload with the
configured Entra tenant/audience/client and `VisitPrep.Clarify` role. It verifies
RS256 through bounded public-key transport, claims a one-use Core request bound
to the exact protected payload and calls the existing model client. Its distinct
patient accounting subject carries no provider interaction or mount credential.
Content-free usage returns to Core's single ledger before the reply is returned.
Core remains responsible for fresh source checks, grounded proposal persistence
and the patient's explicit accept/edit action. Generic `/chat`, provider tokens
and the companion display capability grant no access to this rail.

Core supplies generated migration `dd93e7a689f1` after `6c5c8f775016` for its
request fence and additive usage origin. Receiver-first release requires that
Core expand and callback implementation before this Sovereign sender is enabled.
The new wire/capability still requires canonical architecture reconciliation,
actual model/privacy-service acceptance and a founder-controlled release.
No live identity, role grant, environment configuration or service is created.

## Evidence and recovery

Focused checks use invented text, actual signed workload JWTs, offline Core/model
transports and the actual metered client. They cover bounded questions,
organization, gates, malformed output, privacy tripwire and usage failure. Exact
commands, tested commits and tool limitations are recorded in the draft PR.
They are not live model, privacy-provider or clinical qualification.

This is Tier 2 patient-information integration, with no schema or dependency
change. Keep the switch off and revert the additive increment for recovery.
Existing provider routes and workflows are unchanged. No deployment, real
patient data, messaging, new service identity or runtime activation is included.
