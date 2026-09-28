# Session Prep panel layout

Tier 1, one UI outcome: a long prepared brief scrolls within the panel while
the conversation and composer remain available. A separate Show/Hide brief
control changes display only, with keyboard focus and disclosure semantics.
Existing record changes, access revocation, close, and destroy still clear
the brief; background completion preserves the clinician's collapsed state.

Independently review and merge Sovereign, then copy its exact panel bytes into
a new immutable Core asset and update the commit, SHA and SRI pins in a separate
reviewed Core PR. Retain the previous asset. The current staging release operator
deploys the governed context receiver, Sovereign backend and pinned UI together
through the existing authorized local staging release process while Actions are
disabled. Keep the current provider cohort and public/Scribe services unchanged.

Validate the existing contract/completion harnesses and a synthetic long READY
brief at desktop, narrow and short viewport sizes. Verify independent brief/chat
scrolling, keyboard Show/Hide, visible composer including multiline drafts, and
record-scope clearing. The local fixture is evidence outside the repository.

Recover the UI by restoring Core's previous immutable asset pin or its recorded
prior frontend revision. Any coordinated backend recovery follows the existing
provider-question context flight plan. This slice adds no history reader or
backend contract and makes no claim to restore prior conversations.
