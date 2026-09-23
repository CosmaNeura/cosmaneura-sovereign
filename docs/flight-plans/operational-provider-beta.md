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
