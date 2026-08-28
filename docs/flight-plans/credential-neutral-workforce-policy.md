# Flight plan: credential-neutral Sovereign governance

## Outcome and boundaries

- Outcome: current Sovereign guidance states that practices authorize workforce membership and
  roles; credential/licensure status and NPI never grant or block platform or real-PHI access.
- In scope: repository guidance, internal design notes, handoff ownership, and documentation.
- Non-goals: no auth/session, mount, PHI, model, vendor, infrastructure, deploy, or production
  behavior change.
- Data: documentation only; no customer, provider, or patient data.

## Risk and dependencies

- Tier: Tier 0 documentation coordinated with a cross-repository architecture decision.
- Architecture receiver: CosmaNeura architecture ADR 0025/0026 via PR #11.
- Runtime authority: Core, not Sovereign, owns identity, workforce membership/role, legal state,
  entitlement, tenant/record/purpose scope, consent, and capability rollout.
- Hold point: this PR does not authorize real-PHI activation or production promotion.

## Release control and data sequence

N/A. There is no runtime change, feature flag, schema, migration, or data operation.

## Rollout and recovery

- Merge only after exact-head review and green required CI. Do not deploy from this plan.
- Recovery is a documentation revert; deployed Sovereign state is unchanged.
- Any later public activation still requires the existing synthetic rehearsal, BAA/vendor,
  de-identification, mount-authority, retention, recovery, and founder gates.

## Completion

- No current Sovereign source of truth treats credential/licensure or NPI as authorization.
- Guidance states that Sovereign receives no raw audio/video/media payloads.
- John Clemente is recorded as the initial primary operational POC; secondary coverage remains a
  blocker.
