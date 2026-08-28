# AGENTS.md — Cosmaneura Sovereign engineering guardrails (v0.2.1)

The canonical baseline lives in `cosmaneura-architecture/deploy-kit/`. This
copy is adapted to Sovereign; stricter rules in `CLAUDE.md` still bind, and
governance architecture supersedes this repository's historical design note.

## Operating objective

Reduce deployment risk created by rapid code growth. Keep changes small,
independently deployable, observable, and reversible. Documentation supports
that work; it does not replace decomposition or deployed-state reconciliation.

## Verified commands and current deployment facts

- Local install: `make install`
- CI's locked install: `tools/compile_requirements_locks.sh --check`, then the
  hash-checked `uv pip install` sequence in `build_and_publish.yml`
- Test: `make test`; CI pytest:
  `PYTHONPATH=. .venv/bin/python -m pytest tests/ -q`; replace `tests/` with a
  path for a focused run
- Lint: `.venv/bin/ruff check .`
- Typecheck: none is configured
- Local service: `make serve`; offline demo: `make demo`; embedded demo:
  `make demo-dashboard`
- Migration: N/A. Sovereign is stateless and owns no database schema.

Staging exists, while production resources do not. `Build & Publish` has a
default-disabled manual deploy job for `main`, gated by
`SOVEREIGN_DEPLOY_ENABLED`; it has never executed. It currently builds and
deploys in the same run rather than promoting a previously built artifact, so
it does not yet satisfy the target promotion doctrine. Turning it on is not
authorized by this document.

Only the founder may dispatch a future production promotion. The workflow run
is explicit self-authorization, not independent approval. The workflow now
requires both the actor and rerun actor to be `JohnClem`, binds the run to the
exact current `main` commit before credentials and again immediately before
each mutation, and keeps the deploy job behind `SOVEREIGN_DEPLOY_ENABLED`.
Agents never dispatch or run direct production deployment commands. This is
pre-activation containment, not evidence that production is ready.

## Risk class (declare one on every PR)

- **Tier 0 — routine:** localized code, tests, copy, style, documentation, or
  behavior-preserving refactoring with no deployment-surface, schema/data,
  auth/session, billing, PHI, security-boundary, or external-contract impact.
- **Tier 1 — deployment-sensitive and reversible:** dependency, runtime
  configuration, CI/build path, additive API/contract, or user-visible
  behavior with a deliberate recovery move.
- **Tier 2 — irreversible or high blast radius:** auth/session, provider/PHI
  paths, security boundaries, production identity/networking/promotion,
  breaking contracts, broad infrastructure, or destructive external data work.

`app/security/auth.py`, `app/clients/athena/`, the de-identification path
(including `infra/deid.tf` and `DEID_*` configuration), and mount-profile
changes set a minimum of Tier 2. Classify by effect as well as path; urgency
never lowers the tier. Split mixed work where concerns can deploy separately.

## Conditional controls

- Use a feature flag or release control only for reversible user-visible
  behavior, with owner, expiry, and initial cohort. It is not rollback for
  infrastructure, security, or refactoring work.
- Use a flight plan for Tier 2, cross-repo rollouts, or multi-deploy/cohort
  releases. Routine single-deploy PRs do not invent one.
- Sovereign owns no schema: local migration rungs and database rehearsal are
  N/A. If another service's schema/data changes, that owner supplies the rung,
  rehearsal, and receiver-first plan; Sovereign links the evidence.
- Every code-changing PR receives an independent agent review after its last
  substantive push. A non-implementer agent records the reviewed commit,
  findings, and dispositions in a durable PR comment or check and links it
  from the PR. A later substantive push invalidates that review.
- Agent review is not GitHub approval. `main` currently has no branch
  protection or required human approval; preserve that setting until it is
  separately revisited.

## Approval, promotion, and reconciliation

- Do not claim a GitHub environment reviewer exists; private GitHub Team does
  not provide that control. Founder production dispatch is self-authorization.
- Before promoting over a deployed SHA, digest, configuration, or mount that
  differs from `main`, record the evidence and choose `assimilate`, `retain
  temporarily`, or `replace`. Never reconcile drift by blindly deploying.
- Security acceptance is scoped. Unless it says otherwise, it permits only
  dark development in a non-PHI environment and blocks production or
  PHI-bearing promotion in the affected scope.

## Permission boundaries

**Allowed without a separate production decision**

- Small/stacked PRs, tests, flight plans, and reversible refactors in scope.
- Read-only live-state inspection and preparation of reconciliation options.

**Ask first**

- Changes to auth/session, Athena, de-identification, provider/PHI paths,
  mount profiles, security boundaries, CI/deployment workflows, environment
  configuration, or production data.
- New dependencies/services, cohort activation, or production promotion.

**Never**

- Put PHI in tests, fixtures, seeds, staging, rehearsal exports, logs, PRs, or
  agent prompts. Magisterium and other non-BAA egress receive de-identified
  content only.
- Commit secrets, force-push `main`, deploy an unmerged branch, or represent
  agent output as GitHub approval.
- Perform schema cleanup from this repository; it does not own the schema.
- Archive a repository or delete long-lived deployed resources as part of this
  adoption.

## Architecture and workflow

- Vendor OIDC claim parsing stays in `app/security/auth.py`; Athena auth stays
  in `app/clients/athena/auth.py`. Domain code consumes normalized principals.
- NPPES/NPI is a read-only public referral data source, never provider
  authentication, licensure or credential proof, practice membership,
  entitlement, or a required onboarding field. Core owns public-provider
  onboarding and it must succeed with NPI absent.
- Preserve emergency short-circuiting, mount resolution, capability/BAA gates,
  canonical persistence, de-identification, doctrinal routing, and safe egress
  in the order defined by `CLAUDE.md`.
- Cross-repo work is receiver-first and backward-compatible.
- Each PR should be reviewable in roughly ten minutes and produce one
  deployable outcome. Prefer a dependency-ordered stack to a mixed tranche.
- The tracked, repository-specific `change-budget` workflow rejects more than
  2,000 changed lines or 50 changed files and fails closed when GitHub diff
  metadata is missing, malformed, or negative. Repository administration must
  separately require its status context for merge blocking; the workflow
  itself does not alter the current human-approval requirements. Change the
  threshold only in a separate reviewed policy PR, never by waiving an
  oversized PR.
- Every PR records tier, tests, exact recovery, agent-review evidence, and only
  the conditional controls that apply.
- Current gaps also include unset deployment variables, absent federation,
  no branch protection, and no prebuilt-artifact promotion path. Record them;
  do not describe them as implemented controls.
- No workflow currently validates tier, flight-plan, or agent-review evidence;
  those are documented process controls until an enforcement PR lands.
