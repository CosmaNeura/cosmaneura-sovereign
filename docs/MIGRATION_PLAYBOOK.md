# MIGRATION_PLAYBOOK.md — Sovereign applicability (v0.2.1)

Sovereign is a stateless service and owns no database, migration framework, or
schema. Local migration rungs, database restore rehearsal, backfill, and
contract cleanup are therefore **N/A**. Do not invent Alembic commands, a
rehearsal database, or a CI job for this repository.

Auth, provider/PHI, de-identification, mount-profile, security-boundary, and
production-promotion changes can still be Tier 2. Their controls are a flight
plan, independent agent review, explicit health/abort thresholds, reversible
runtime rollout where possible, and founder-only production promotion. They do
not claim a schema rung merely because the change is high risk.

## When another repository owns the data change

If a Sovereign rollout depends on a platform-owned schema or stored-data
contract, the owning repository supplies the migration PR, rung, PHI-safe
rehearsal, invariants, and recovery evidence. The coordinated flight plan must:

1. Name the owning repository, versioned contract, and exact PRs.
2. Deploy the backward-compatible receiver before activating Sovereign as a
   sender.
3. Show that both versions can coexist and that each step can be held safely.
4. Link the owner's rehearsal evidence rather than duplicating PHI or raw
   output in Sovereign artifacts.
5. Leave additive expand structures dormant on rollback; destructive cleanup
   remains a later, separately approved contract step in the owning repo.

## Runtime rehearsal

Synthetic smoke, mount failover, and paired rollback exercises may be required
by a Sovereign flight plan. They are runtime rollout evidence, not database
migration rehearsal. Never copy production PHI into Sovereign staging or agent
context. A scoped security acceptance permits only the environment/data
boundary it names; by default it allows dark non-PHI development and blocks
production or PHI-bearing promotion.

No migration, destructive cleanup, production promotion, repository archive,
or resource deletion is authorized by this document.
