# Visit Prep patient clarification integration

This branch adds the bounded patient clarification generator to Sovereign and
reuses its existing model client and Core usage reporting. It supports the
approved five-topic Visit Prep interview without installing a patient route,
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

## Paired Core work and remaining connection

Core's paired branch selects current context under the existing patient
authority, applies Core clinical de-identification outside database locks and
maps accepted excerpts back only through unchanged original words. Core's
ordinary proposal and explicit patient-decision commands remain authoritative.

The dedicated Core to Sovereign authenticated transport and patient context
and usage lineage contract remain unfinished. The provider Companion display
contract is not permission for conversational analysis. Neither a generic
`/chat` call nor a fabricated provider mount is substituted. Browser generation
and controlled service acceptance follow the actual connection.

## Evidence and recovery

Focused checks use invented text, an offline model transport and a synthetic
usage writer through the actual metered client. They cover bounded questions,
organization, gates, malformed output, privacy tripwire and usage failure. Exact
commands, tested commits and tool limitations are recorded in the draft PR.
They are not live model, privacy-provider or clinical qualification.

This is Tier 2 patient-information integration, with no schema or dependency
change. Keep the switch off and revert the additive increment for recovery.
Existing provider routes and workflows are unchanged. No deployment, real
patient data, messaging, new service identity or runtime activation is included.
