# AGENTS.md — Cosmaneura engineering guardrails

> Canonical copy lives in `cosmaneura-architecture/deploy-kit/`. Sync on Contract Friday.
> Keep this file under ~150 lines: only non-inferable rules belong here.

## Stack

GitHub + GitHub Actions (no Azure DevOps) · Azure Container Apps · ACR ·
Azure Database for PostgreSQL Flexible Server · Clerk + Microsoft Entra ID
(sign-in; varies by service). Core deploys from `master`. Key Vault + App
Insights are target-state, not yet live in core prod.
**This repo: the sovereign chatbot. ACA **staging only** — never deployed to production.**

## Commands

- Install: `make install`
- Test (all): `make test`
- Test (single file): `uv run pytest <path>`
- Lint + typecheck: `ruff check app tests`
- Local dev: `make serve  # or `make demo` for the offline pipeline`
- DB migration create: no Alembic in this repo — state lives in the platform. N/A.
- Deploy: manual `workflow_dispatch` only, and gated by `vars.SOVEREIGN_DEPLOY_ENABLED`, which is `false` on purpose. See Repo notes.

## Risk tiers (declare one on every PR)

- **Tier 0** — copy, styling, docs, config text, additive UI behind an existing flag. CI green → merge.
- **Tier 1** — new feature logic, new endpoints, additive-only schema changes (new tables/columns, nullable). Requires: feature flag + flight plan reference.
- **Tier 2** — anything touching `app/security/auth.py`, `app/clients/athena/`, the de-identification path (`infra/deid.tf`, DEID_* config), and any change to a mount profile, or any destructive migration. Requires: flight plan, migration ladder compliance (docs/MIGRATION_PLAYBOOK.md), rehearsal run, founder sign-off.

Tier is set by the *paths a change touches*, never by urgency.

## Permission boundaries

**Allowed freely**
- Creating branches, PRs, and stacked PRs; small refactors inside a feature's own module
- Adding feature flags; additive migrations (new nullable columns, new tables, new indexes)
- Updating docs, tests, and flight plans

**Ask first**
- Any change under the Tier 2 paths above — even one line
- Adding a new external dependency or third-party service (BAA implications)
- Changing CI workflows, environment config, or anything under `.github/`
- Backfill scripts that write to production data

**Never**
- Destructive migrations (DROP/RENAME column or table, type changes) outside a labeled `contract` PR whose hold period has elapsed
- Importing auth/billing internals from feature code — go through the public interface of the module only
- Turning a feature flag on for all users in code; rollout happens through the flag system, by cohort
- Touching production directly (`az` commands against prod resources), force-pushing the default branch, committing secrets, or putting real PHI in tests, fixtures, seeds, staging, or logs

## Architecture rules (the non-inferable ones)

- Identity: only this repo's designated auth adapter module(s) — `app/security/auth.py` (provider-neutral OIDC) and `app/clients/athena/auth.py` (Athena) — may import vendor SDKs (Clerk, Entra/MSAL) or parse provider claims. All domain code consumes a normalized principal from the adapter — never raw tokens, never provider-specific branches. One adapter per service; there is no cross-service facade.
- Reversible user-visible behavior ships behind a flag/cohort control from its first commit (name: `ff_<feature>_<yyyymm>`, owner + expiry in the flight plan). Refactors, security fixes, infra, and schema work are NOT flagged — small PRs + migration ladder + revert.
- All schema changes follow docs/MIGRATION_PLAYBOOK.md. Additive first; destructive only as a final `contract` step. A "rung" = one independently deployable, observable, backward-compatible transition (may span coordinated cross-repo PRs; receiver/auth deploys before sender activation).
- Users carry an `auth_version` (or equivalent) marker during auth migrations; both old and new paths must work until the contract step.

## Workflow

1. New feature? FIRST draft `docs/flight-plans/<feature>.md` from the template. Do not write feature code before the flight plan exists.
2. Build as a stack of small PRs (interface/seam → logic → UI → flag wiring). One session per layer. Keep diffs reviewable by one human.
3. Every PR fills in the PR template completely, including the tier and the change record.
4. If a PR changes deploy-relevant behavior, update this file in the same PR.

## Repo notes

- **Provider-neutral OIDC**, not Clerk-native — staging currently points at a Clerk
  dev issuer, which is a staging convenience and not the production contract.
- Staging is live (`cosmaneura-sovereign-staging`, both mounts running). **Production
  does not exist**: no resource group, no registry, no container apps.
- The Deploy job has never executed. It requires `workflow_dispatch` on `main` AND
  `SOVEREIGN_DEPLOY_ENABLED == true`. Turning that variable on is a declaration that
  six gates are cleared — it is the founder’s call, never an agent’s.
- Four workflow variables are unset with no fallback: `CONTAINER_REGISTRY_HOST`,
  `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`. Separately, no
  federated credential names this repo, so `azure/login` cannot succeed yet.
- `main` has no branch protection.
- Infra here is OpenTofu (`infra/*.tf`), not Bicep.
