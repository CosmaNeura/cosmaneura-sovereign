# Flight plan: isolate the Session Prep handle from generic chat

## Outcome and boundaries

- Problem and measurable outcome: the server-owned `context_snapshot_ref` must
  never enter host hydration or browser state. Executable coverage must prove
  it is absent from generic host context, `POST /chat`, the browser
  `POST /session-prep` body, URLs, rendered DOM, and browser persistence while
  Sovereign still places the configured value in the Core tool request.
- In scope: the versioned panel, browser Session Prep request, Sovereign route
  and server-derived tool request, local synthetic walkthrough contract,
  browser/runtime regressions, and panel integration documentation.
- Explicit non-goals: changing Session Prep polling, response projection, UI
  states, Core eligibility/snapshot semantics, Auth/IAM, operational controls,
  environment configuration, or deploying any environment.
- Affected users, mounts, services, environments, and data classes: provider
  assistant panel code only; synthetic fixtures only in verification. No real
  provider data or PHI is authorized.

## Risk and dependencies

- Tier and reason: Tier 2 because this narrows a provider browser data boundary
  in a cross-repository Session Prep slice.
- Irreversible or high-blast-radius decisions: none; the change removes the
  handle from official browser inputs and is revertible.
- Dependency-ordered PR stack; explain how each PR can be held safely: the
  already-merged Core receiver remains backward compatible; this Sovereign
  correction can remain unpromoted; a later Core asset-pin change must consume
  the exact reviewed panel bytes before any synthetic activation.
- Cross-repo contract/version and receiver-before-sender order: retain the
  server-to-server `session-prep.context.read@1.0.0` request unchanged. Browser
  ingress contains only `client_turn_id`; Sovereign adds the configured
  `context_snapshot_ref` after authority validation. No sender activation
  precedes the existing Core receiver.

## Current deployed state

- Live SHA/digest/configuration/mount evidence by affected environment: the
  latest checked-in read-only record is
  `docs/STAGING_PROVENANCE_2026-09-02.md`; this branch performs no new live
  inspection and makes no deployment claim.
- Default-branch difference and deployed-only behavior: this work starts from
  exact `origin/main` `92e606029d1fa3f2173f415d6b85f517204a7b63`.
  The recorded staging image predates Session Prep and remains unchanged.
- Reconciliation decision: retain staging temporarily; do not promote this
  unmerged branch or replace a deployed digest.
- Previous known-good artifact and recovery boundary: the exact main baseline
  above; revert this isolated commit and restore the corresponding prior Core
  asset pin if the corrected panel fails synthetic verification.

## Feature flag / release control

- Control name, stable practice key, owner, and expiry: existing
  `FEATURE_SESSION_PREP`, default off; owner and any activation/expiry decision
  remain with the founder. It is not evidence of Auth/IAM or operational
  readiness.
- Cohorts and activation authority: synthetic-only staging fixtures after an
  exact reviewed cross-repository build; founder controls any later activation.
- Removal PR/condition: separate decision only after Auth/IAM and operational
  controls are complete and verified.
- No real-provider or PHI cohort is permitted by this plan.

## Schema/data sequence

- N/A — Sovereign owns no database schema and this change creates no data.
- Core continues to own finalized-note eligibility and immutable snapshots.

## Rollout and promotion

- Ordered environments/mounts/cohorts and hold points: local synthetic tests,
  independent review of an exact SHA, Core pin to those exact bytes, combined
  synthetic cross-repository tests, then hold. No live promotion is included.
- Health signals and measurable abort thresholds: abort on any handle in host
  hydration or browser body/URL/DOM/storage, any browser Session Prep body with
  fields beyond `client_turn_id`, any Core tool request missing the exact
  configured handle, any stale-record rendering, or any failed regression.
- Synthetic canary journey and paired-rollback evidence: exercise generic chat
  and all four Session Prep states without exposing the server-owned handle,
  plus record switching, late content, retry, and revocation; confirm the Core
  client receives the configured handle. Recovery is the exact revert above.
- Founder-only manual production/PHI promotion: blocked until separate Auth/IAM
  and operational-control work is complete. This plan authorizes none.

## Security and recovery

- Open security acceptances, scope, expiry, and promotion impact: none granted.
  Synthetic-only validation does not authorize real-provider or PHI use.
- Recovery move: revert the isolated Sovereign commit and the corresponding
  Core asset-pin commit; leave environments, mounts, and external data intact.
- Observation window before irreversible cleanup: N/A; no cleanup or external
  data mutation occurs.

## Completion

- Evidence required: focused and full relevant tests, JavaScript syntax check,
  exact panel byte hash, clean diff checks, and independent review of the final
  commit before a PR can be promoted.
- Follow-up cleanup PRs and owners: Core must pin the corrected reviewed bytes;
  Auth/IAM and operational controls remain separate founder-owned work.
- Documentation and contract records: this plan and `UI_UX_DESIGN.md`.
