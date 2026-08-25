# Flight plan: name the outcome

Use this template only for Tier 2, cross-repo rollouts, or changes released
over multiple deploys/cohorts. Delete instructional text from the completed
plan and mark inapplicable sections `N/A` with a reason.

## Outcome and boundaries

- Problem and measurable outcome:
- In scope:
- Explicit non-goals:
- Affected users, mounts, services, environments, and data classes:

## Risk and dependencies

- Tier and reason:
- Irreversible or high-blast-radius decisions:
- Dependency-ordered PR stack; explain how each PR can be held safely:
- Cross-repo contract/version and receiver-before-sender order:

## Current deployed state

- Live SHA/digest/configuration/mount evidence by affected environment:
- Default-branch difference and deployed-only behavior:
- Reconciliation decision: assimilate / retain temporarily / replace
- Previous known-good artifact and recovery boundary:

## Feature flag / release control (user-visible reversible behavior only)

- Control name, stable practice key, owner, and expiry:
- Cohorts and activation authority:
- Removal PR/condition:
- If no reversible user-visible behavior, reason this is N/A:

## Schema/data sequence

- N/A — Sovereign owns no database schema.
- If an external schema or stored-data contract changes, link the owning
  repository's rung, rehearsal, invariants, recovery, and receiver-first order:

## Rollout and promotion

- Ordered environments/mounts/cohorts and hold points:
- Health signals and measurable abort thresholds:
- Synthetic canary journey and paired-rollback evidence:
- Founder-only manual production/PHI promotion run, or current blocker while
  the default-disabled workflow lacks an already-built artifact path:

## Security and recovery

- Open security acceptances, scope, expiry, and promotion impact:
- Recovery move for behavior, image, infrastructure, mounts, and external data
  damage as applicable:
- Observation window before irreversible cleanup in an owning repository:

## Completion

- Evidence required to call the outcome complete:
- Follow-up cleanup PRs and owners:
- Documentation and contract records to update:
