# Flight plan: contain Sovereign production deployment before activation

## Outcome and boundaries

- Problem and measurable outcome: a future enabled GitHub deployment must
  refuse non-founder dispatches and stale reruns before Azure mutation.
- In scope: source-only workflow gates, regression tests, and accurate runbook
  text.
- Explicit non-goals: enabling `SOVEREIGN_DEPLOY_ENABLED`, changing GitHub or
  Azure configuration, creating credentials or resources, deploying, or
  redesigning the build-then-deploy path into artifact promotion.
- Affected users, mounts, services, environments, and data classes: the future
  production provider and public mounts; no runtime or data-plane change while
  the deployment flag remains false.

## Risk and dependencies

- Tier and reason: Tier 2 because this changes the production promotion
  security boundary, even though it only narrows authority.
- Irreversible or high-blast-radius decisions: none; the source change is
  revertible and performs no external mutation.
- Dependency-ordered PR stack: one containment change based on current `main`.
  Activation remains a separate founder decision after all readiness gates.
- Cross-repo contract/version and receiver-before-sender order: N/A; no runtime
  contract changes.

## Current deployed state

- Live SHA/digest/configuration/mount evidence by affected environment:
  production resources do not exist according to the repository's current
  deployment record; this source-only change performs no live reconciliation.
- Default-branch difference and deployed-only behavior: the current workflow is
  default-disabled but would accept any authorized dispatcher if its flag were
  enabled. No deployed behavior changes in this flight.
- Reconciliation decision: retain temporarily. Keep the deployment flag false
  and reconcile live state only as part of a separately approved activation.
- Previous known-good artifact and recovery boundary: N/A before production
  activation.

## Feature flag / release control (user-visible reversible behavior only)

- N/A. `SOVEREIGN_DEPLOY_ENABLED` is a deployment kill switch, not a feature
  flag; it remains false and receives no configuration change here.

## Schema/data sequence

- N/A — Sovereign owns no database schema and this change touches no data.

## Rollout and promotion

- Ordered environments/mounts/cohorts and hold points: merge source containment
  only. Hold all production activity behind the unchanged false kill switch.
- Health signals and measurable abort thresholds: workflow policy tests must
  pass; any actor, rerun actor, repository, ref, or current-main mismatch aborts.
- Synthetic canary journey and paired-rollback evidence: N/A for this
  non-deploying change; existing staging rehearsal remains required before
  activation.
- Founder-only manual production/PHI promotion: `JohnClem` must originate and
  rerun a manual dispatch on the exact current `main` commit. That is
  self-authorization, not independent review or readiness evidence.

## Security and recovery

- Open security acceptances: the absent smoke-time credential exchange,
  prebuilt-artifact promotion, production resources, federation, and other
  readiness gaps continue to block activation.
- Recovery move: revert this commit. Because the deployment switch remains
  false and no deployment occurs, there is no runtime rollback.
- Observation window before irreversible cleanup: N/A; no cleanup or
  irreversible operation is included.

## Completion

- Evidence required: focused workflow-policy tests, the repository test suite,
  lint, and independent agent review of the final commit.
- Follow-up cleanup: separately design artifact promotion and the smoke-time
  credential exchange; do not enable the deployment switch in this change.
- Documentation and contract records: `AGENTS.md`, `DEPLOY_AZURE.md`, and
  `DEPLOY_CHECKLIST.md` describe the contained but still disabled state.
