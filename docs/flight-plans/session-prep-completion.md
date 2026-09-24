# Session Prep completes in the provider panel

## Outcome and boundaries

Opening Session Prep should show the completed brief when its durable Core
build finishes, without repeated manual checks. The current server read stops
after three calls or ten seconds; a queued build can legitimately take longer.
The panel will follow pending/refreshing responses for at most three minutes
and twelve additional checks, respecting the returned retry interval. Each
logical check reacquires authority and uses a new turn; an ambiguous delivery
retry preserves its original turn and body. A hung request releases the panel
after thirty seconds and offers an exact retry.

Only the existing provider Session Prep panel and its synthetic tests change.
No Scribe, Core, deployment, schema, dependency, AI generation, or stored-data
contract changes are needed. No clinical content is cached in browser storage.

## Risk and dependencies

Tier 2: provider content and record lifecycle. Each new poll must revalidate
the host record and bearer. Record changes, closing the panel, hiding the tab,
destroying the mount, and revocation cancel work and clear its content. Late
responses and retained buttons from old requests cannot repopulate the panel.
Polling stops on terminal errors and on its time/request budget; manual checks
remain available. No automatic delivery retry loop is introduced.

This is a single Sovereign PR based on c39ffc3. It is independent of Core
PR487 and can wait while the separately owned staging release finishes.

## Current deployed state and release control

The latest pre-work observation was provider Sovereign c39ffc3, digest
`sha256:ae84e0b974d94c82bf0e8b32efc35f891c8f517106de7eb6249ae23ec85572ae`,
with platform-backed Session Prep enabled. Another task is updating staging;
that observation is not authorization to replace its release. Retain the
running deployment and leave this work on its isolated source branch.

Existing FEATURE_SESSION_PREP and Core provider cohort controls apply. The
founder owns activation for the select-provider beta. No new flag is needed;
this fixes completion within that existing opt-in capability. Cohort controls
remain until the beta owner authorizes wider availability.

## Schema/data sequence

N/A: no schema, persistence, or cross-repository contract changes.

## Validation and recovery

Use local deterministic browser lifecycle tests and Session Prep API/client
tests, followed by independent Cursor review of the final pushed commit.
Exercise pending-to-ready, refreshing-to-ready, fresh poll identity versus
exact transport retry, request/time limits, hung headers/body/auth, cancellation,
late responses, and authority loss. All fixtures are synthetic.

A later coordinated staging release should run the real provider journey and
confirm generation completes without clicking Check again. Stop that rollout
if a previous record renders, terminal errors repeat automatically, or polling
continues after leaving the panel. Source verification does not establish live
device or staging acceptance. Recovery is to retain/redeploy the preceding
coordinated Sovereign image; no Core or data rollback is required.

## Completion

Completed source checkpoint means committed/pushed code, passing local checks,
and exact-commit Cursor review with findings resolved. Staging deployment and
real-provider acceptance remain separate, with no production promotion here.
