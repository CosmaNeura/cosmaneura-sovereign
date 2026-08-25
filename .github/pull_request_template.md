<!-- deploy-kit v0.2.1 -->

## Outcome and scope

<!-- State one deployable outcome and the explicit non-goals. -->

## Risk class

- [ ] **Tier 0 — routine:** no deployment-surface, auth/session, PHI,
      security-boundary, or external-contract impact
- [ ] **Tier 1 — deployment-sensitive/reversible:** deployment or release
      behavior changes but has a deliberate recovery move
- [ ] **Tier 2 — irreversible/high blast radius:** auth, PHI, security,
      production identity/network/promotion, breaking contract, broad
      infrastructure, or destructive external data impact

Classification reason:

## Evidence required for every code change

- [ ] Repository CI is green for the final substantive push
- [ ] Independent non-implementer agent review identifies the final reviewed
      commit: <!-- link to durable check/comment -->
- [ ] Agent findings are resolved or explicitly accepted in the PR
- [ ] Existing GitHub approval requirements are satisfied in GitHub

Agent evidence is not a GitHub approval. Do not write a person's name here as
proof of review.

- [ ] This PR is one independently deployable outcome; if not, link the
      dependency-ordered split/stack decision

## Conditional controls

<!-- Complete what applies; mark the rest N/A with a reason. -->

- **User-visible reversible behavior:** feature-flag/release-control name,
  owner, expiry, and first cohort; otherwise N/A
- **Flight plan:** link when Tier 2, cross-repo, multi-deploy, or cohort
  rollout; otherwise N/A
- **Schema/data:** N/A — Sovereign owns no schema. Link the owning repository's
  rung/rehearsal evidence if an external schema or stored-data contract changes.
- **Cross-repo:** contract version and receiver-before-sender order; otherwise
  N/A
- **Security acceptance:** record link and allowed environment/data boundary;
  otherwise N/A

## Deployment and recovery

- Deployment/runtime effect:
- Current deployed-state comparison required: yes/no; evidence or reason:
- Health/canary signal:
- Exact rollback or recovery move:
- Previous known-good artifact/data boundary:

Only the founder may manually promote a merged `main` commit to production.
The run is self-authorization, not a pre-merge checkbox or independent review.
The current workflow is default-disabled and does not yet implement promotion
of an already-built artifact; do not check or imply that those gaps are closed.

## Change record

- Systems and data affected:
- PHI impact: none / read / write / external schema-data
- Tests and rehearsal actually run (local schema rehearsal is N/A):
- Documentation or runbook changed:
