# Flight plan: ground ordinary Session Prep fact questions

## Outcome and boundaries

- Problem and measurable outcome: short provider questions such as “How many
  cups of water does Don drink per day?” and “What appointment time does Don
  prefer?” must use canonical reviewed-note context even when the BAA routing
  classifier returns a false negative. They must never fall through to generic
  education, outside references, or visible intent/doctrinal badges.
- In scope: a conservative deterministic ENGAGEMENT routing guard, exact-source
  non-answer copy for partial context, and synthetic regression tests.
- Explicit non-goals: no broader chart search, external EHR read, clinical
  recommendation, new patient selector, Core/Scribe contract change, schema,
  configuration, cohort activation, or deployment.
- Affected users, mounts, services, environments, and data classes: the
  authorized provider-assistant mount and Sovereign provider-context routing;
  synthetic test data only in this change. No environment is mutated.

## Risk and dependencies

- Tier and reason: **Tier 2** because the change affects the provider/PHI
  routing path, although it neither grants authority nor changes the record
  contract.
- Irreversible or high-blast-radius decisions: none. The guard runs only after
  the existing provider mount is resolved and before the existing canonical
  context read; mount, capability, accepted-turn, BAA, and final-acceptance
  checks remain authoritative.
- Dependency-ordered PR stack: one Sovereign PR. It can remain unmerged and
  undeployed indefinitely. Core and Scribe require no change.
- Cross-repo contract/version and receiver-before-sender order: N/A; the
  existing `provider-question.context.read@1.2.0` contract is unchanged.

## Current deployed state

- Live SHA/digest/configuration/mount evidence by affected environment: not
  reconciled by this source-only change. No deployment is authorized. Record
  exact staging state before any later rollout.
- Default-branch difference and deployed-only behavior: the branch starts from
  Sovereign `main` at `5c116c50da187c4ddb85c9352dceb8d8b7195aa4` and adds only
  the scoped routing, copy, tests, and this plan.
- Reconciliation decision: retain the current deployed state until an explicit
  reviewed staging rollout chooses assimilate or replace from exact evidence.
- Previous known-good artifact and recovery boundary: the exact Sovereign
  staging artifact must be recorded before rollout; source recovery is to
  revert this change commit.

## Feature flag / release control

- Existing `FEATURE_SESSION_PREP` and the existing authorized provider cohort
  remain the release controls. Owner: Session Prep engineering/founder.
- No cohort changes are part of this work. Any staging or production activation
  requires a separate explicit decision.
- Removal condition: revert this change if grounded patient-fact routing causes
  a regression; no data cleanup is required.

## Schema/data sequence

- N/A — Sovereign owns no database schema, and no external schema or stored-data
  contract changes.

## Rollout and promotion

- Default state: **do not deploy**. After independent review, a later authorized
  rollout may exercise synthetic staging first and hold before any production
  decision.
- Health signals and abort thresholds: abort on any generic/outside-reference
  answer for the two canary questions, any missing reviewed-context dependency,
  any badge on a factual excerpt/non-answer, or any capability/auth regression.
- Synthetic canary: use a patient with one included fact and another fact only
  in an omitted or temporarily unavailable source; verify exact excerpts for
  the former and a fixed non-answer for the latter. Reopen the visit to verify
  canonical persistence.
- Production promotion: founder-only and out of scope. The default-disabled
  production workflow and its existing blockers remain unchanged.

## Security and recovery

- No security acceptance is requested. The guard cannot choose a patient or
  source and cannot bypass mount, capability, BAA, or canonical acceptance.
- Recovery: restore the recorded prior Sovereign artifact and revert this change
  commit. Core/Scribe, database, configuration, mounts, and persisted answers
  require no rollback or cleanup.
- Observation window: N/A for cleanup; no destructive or irreversible action.

## Completion

- Required evidence: focused routing/evidence suites and Ruff pass; independent
  review of the final commit; later synthetic staging canary only under explicit
  rollout approval.
- Follow-up: investigate live-classifier evaluation separately; do not broaden
  the deterministic grammar in this PR.
- Documentation: this plan is the durable Tier-2 record for the source change.
