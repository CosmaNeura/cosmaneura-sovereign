# Operational provider beta — engineering execution

Started 2026-09-23. Scope: Session Prep from authorized finalized notes, ERD
consultation, and bounded visit assistance for selected MyCatholicDoctor users.
Labs, referral-directory results, plan-workbench, and journey-digest are outside
this first release. The provider server intersects platform-issued capabilities
with its release ceiling; a broad grant cannot silently activate another lane.

## Source and deployment reconciliation

- Sovereign base: `f1fd78c53f52b8b5f45a7195861d7b76083a4474`.
- Core integration base: `ddfe047c` (refresh the full identity before promotion).
- Observed staging image: `sha256:e37370a7746a1618927a88987c77c192f0891f5e4b87d24a349a234eaaeb649d`.
- Observed revision: `cosmaneura-sovereign-staging-app--gpt41-20260825`.
- Decision: retain staging until the integrated candidate passes local checks,
  then replace with a verified immutable artifact and preserve the old digest
  and configuration for rollback. Do not mix in unrelated working-tree edits.
- GitHub Actions is disabled. Local tests, security checks, source-bound build
  evidence, and operator deployment are the execution path for this work.

## Ordered engineering milestones

1. Establish a repeatable local release check, exercise the current UI, build
   the exact integrated source, and verify forward/rollback staging behavior.
2. Add runtime record-bound Session Prep using Core authority, finalized-note
   sources, durable storage, and bounded generation. Prove wrong-user,
   wrong-practice, wrong-record, expired, revoked, and retry isolation.
3. Verify timeout/retry/rate controls, content-free operational signals,
   dependency failure handling, and recovery with synthetic load and failures.
4. Enforce the same small beta capability set in welcome and execution. Keep
   sample referrals and labs inaccessible even under overbroad platform grants.

Use synthetic records for development, UI exercises, load checks, and deployment
smokes. Administrative and compliance work is excluded from this engineering
plan; preserve the existing technical data-protection boundaries.

## Recovery and evidence

This is Tier 2 because it changes provider capability and record boundaries.
Ship separately reviewable increments. Receiver changes precede sender changes.
Reverting the capability increment restores its previous source behavior; no
schema or external state changes are involved. Record actual test and deployment
results as they occur. A planned check is never evidence that it passed.

## Record-specific sender validation

Core draft increments: source review #456, provider-record authority #457, and
PostgreSQL worker/artifacts/receipts #458. The sender supports explicit
`SESSION_PREP_CONTEXT_SOURCE=platform` with `FEATURE_SESSION_PREP=true` and no
fixed synthetic context. It consumes only the authenticated Core resolve header,
requires provider capability and matching request correlation, and keeps the
handle out of public mount JSON and repr/log output. A missing handle fails
before canonical writes or tool access. Synthetic mode remains the default.

Local final Sovereign suite: 864 tests passed (six existing warnings). Ruff passes
on changed code. No rollout or cohort activation has occurred. Receiver schema
and application must land before enabling platform context mode; rollback uses
the previous image and configuration, leaving Core's additive structures dormant.

## Combined local acceptance — 2026-09-23

Core candidate `3956614abc2a3704924b67f2524272c10562dc89` combines the feature
stack (#456 → #457 → #458 → #461), backend dependency patch #462, and frontend
dependency stack (#463 → #464 → #465). This local verification branch is not a
deployable or oversized merge PR. Each individual PR meets the change budget.

The combined candidate passed 1,513 application tests, 131 authority-kernel
tests, four independent-connection concurrency tests, and 324 frontend component
tests. TypeScript, API generation, and the frontend build passed. There are 32
existing external-service/optional application skips; one optional historical
migration test is also skipped. Sovereign passed 864 tests. Registry dependency
scans report zero known advisories across 129 backend runtime packages and 646
frontend production packages; the pinned activemodel Git source is outside
registry coverage. Changed Core Python files have no medium/high Bandit findings:
11 low findings are unchanged route assertions and one is the gzip-token false
positive. Sovereign's Bandit report is clean.

The real Sovereign endpoint and clients called the real Core Agent API over
loopback and persisted synthetic records in isolated PostgreSQL. Pending → ready,
idempotent replay, receiver-process restart, browser context-injection refusal,
private lineage removal, source correction/invalidation, reviewed regeneration,
and cohort revocation all passed. Replays created no duplicate receipts, audit
events, or canonical phases; revocation produced no additional writes. This
rehearsal substituted local Clerk/Entra principals and a deterministic generator;
live identity, Azure model, deployed UI, and deployment rollback remain unproven.

The adjacent `operational-provider-beta-evidence-20260923.json` records exact
source commits, PR sizes, rehearsal results, and local report hashes. Independent
review remains pending: Cursor CLI is signed out and the Mac is locked. This
evidence does not substitute for the required non-implementer review.

## Remaining technical release sequence

1. Independently review the final PR heads and merge the bounded increments.
2. Resolve current staging drift before selecting the immutable merged candidate.
   Core web currently exposes build label `scribe-local-2ef8c2de69d0d9ed-dirty`,
   which cannot establish reviewed source provenance; worker/scheduler run
   `ace819712c2b4c757893a9bf134dad5198d1708f`. Preserve their current digests and
   configuration and retain them until replacement is explicitly selected.
3. Prepare the exact merged image locally, with Actions still disabled. Apply
   the additive expand rung through the founder-controlled release path, then
   align Core web, worker, and scheduler. Deploy the compatible Sovereign sender
   afterward. Existing source-bound admission pins must be refreshed through
   review for this new pair; do not relax or bypass them.
4. Configure the selected provider cohort, prove live identity/model connectivity,
   exercise the embedded UI on synthetic records, verify queue-health signals,
   and rehearse application rollback with the additive schema left dormant.

No live migration, cloud mutation, deployment, or cohort activation has occurred.
