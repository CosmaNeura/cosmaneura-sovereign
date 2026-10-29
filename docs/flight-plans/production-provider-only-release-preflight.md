# Provider-only production bootstrap and preparation

Risk: **Tier 2**, production infrastructure/security boundaries. Sovereign owns
no database migration. This source change grants no deployment authorization.
The founder remains the sole production executor. Session Prep activation,
Core migration/cutover, Agent API assignment and user acceptance are separate
release gates. Scribe and existing staging/public workloads are outside scope.

## Problem and resulting behavior

The legacy preflight requires an anonymous public env and seeded public vault,
and bootstrap grants include remote ACR Tasks and public-vault write access.
Those requirements conflict with the reviewed provider-only production app
control and a locally built, verified immutable image.

Explicit `SOVEREIGN_RELEASE_SCOPE=provider-only` is accepted only in production.
It validates provider inputs without reading a public env, requires exactly the
three production provider secrets and an **empty** public vault, still verifies
both distinct runtime identities and their complete direct/inherited data-plane
roles, and requires the provider app to be the sole app in this dedicated group.
Public, unknown, duplicate or unobservable app inventories fail closed. The
provider's versionless Key Vault/identity/registry bindings retain their existing
strict verifier. No runtime role, app role, token or mount semantics change.

The explicit Terraform `bootstrap_release_scope="provider-only"` creates only
operator AcrPush and provider Key Vault Secrets Officer. It requires production
with the legacy dual-app and both-vault-readiness controls false. The default
`dual` preserves existing resource addresses, four bootstrap roles and staging
plans. `deploy-dual.sh` and the remote ACR bootstrap refuse provider-only mode;
the selected path must publish a separately verified local immutable artifact.

## Founder sequence and exact gates

1. Use a clean exact reviewed merge checkout. Initialize the canonical
   `sovereign-prod.tfstate` backend. Create a fresh **normally locked** full dark
   foundation plan with `github_deploy_enabled=false`, all app/readiness gates
   false, empty image and no bootstrap operator. Review every action; the
   previously observed 16-create plan is historical preparation, not approval.
2. From those established foundations, create a fresh normally locked full
   bootstrap plan with only `bootstrap_release_scope=provider-only` and the
   exact reviewed operator object ID. Require only the two temporary role
   creations; no app/config, runtime-role, public-write or ACR Tasks change.
   Founder applies the exact reviewed plan and waits for propagation. Verify:

   ```bash
   DEPLOYMENT_ENVIRONMENT=prod SOVEREIGN_RELEASE_SCOPE=provider-only \
     BOOTSTRAP_OPERATOR_OBJECT_ID="$BOOTSTRAP_OPERATOR_OBJECT_ID" \
     DEID_SERVICE_ID="$DEID_SERVICE_ID" \
     infra/scripts/verify-bootstrap-role-assignments.sh
   ```

3. Publish the reviewed immutable local provider image to the dedicated
   production registry, verify its actual manifest/config/layers, and verify the
   current Core/Sovereign pair with `tools/prepare_provider_production_artifact.py`.
   Source, image digest and pair must match the final review; neither a tag nor
   Docker image ID substitutes for the registry manifest digest. Seed only:

   ```bash
   infra/scripts/sync-key-vault-secrets.sh prod provider cosmasovprodprovider
   DEPLOYMENT_ENVIRONMENT=prod SOVEREIGN_RELEASE_SCOPE=provider-only \
     DEID_SERVICE_ID="$DEID_SERVICE_ID" \
     infra/scripts/verify-key-vault-boundaries.sh
   ```

   The provider env contains exactly three existing `op://Production` references.
   Do not seed/read the public env or add Athena credentials. Keep runtime
   `FEATURE_SESSION_PREP=false` and `SESSION_PREP_CONTEXT_SOURCE=synthetic`.
4. Persist provider-app and provider-only readiness true, legacy dual-app and
   global readiness false, GitHub deployment false and the reviewed immutable
   production image. Omit the bootstrap operator ID. Create a fresh normally
   locked full provider-app plan, requiring only one provider app creation and
   removal of the two temporary bootstrap roles; public app/config absent,
   established foundations and runtime grants unchanged. Founder applies only
   that reviewed plan. Verify:

   ```bash
   infra/scripts/preflight.sh prod provider-only
   ```

   Before first creation this command reports that the live provider boundary
   awaits creation. That warning does not prove a running service. Afterward it
   verifies the sole provider app and the empty public vault. Neither result
   substitutes for production Core authorization, synthetic functional smoke,
   persistence/export, alert delivery/recovery or separately authorized go-live.

The parent release packet must bind the final merged source, actual plans,
immutable Core/provider artifacts, operator/managed identities, current state
and evidence hashes. No previous read-only `-lock=false` plan is reusable as an
apply plan. The existing full-plan/`prevent_destroy`/backend guards remain; do
not bypass them with target apply, changed state keys or ignored discrepancies.

## Recovery

Keep production Session Prep dark while preparing the service. Leave created
foundations/app intact; do not destroy long-lived resources or restore an old
unsafe image. A failed seed leaves only provider secrets to reconcile. Remove
only the two temporary scoped operator roles through a fresh reviewed full plan
after verified publication/seeding; omit the operator ID thereafter. Any later
functional failure uses the separately reviewed Core feature-lane closure or a
verified fix-forward image, preserving Scribe and the unused public foundation.

## Verification

Executed synthetic shell-stub tests cover complete runtime role and vault
checks, absent public env/app, conflicting readiness, metadata failure,
cross-vault refusal and unchanged default dual/staging checks. Provider-mocked
OpenTofu plans cover exact two-vs-four operator grants, unchanged runtime roles,
dark app/config projection and rejected staging/unknown/conflicting scope.
Schema validation uses local cached providers with backend-free initialization;
no production credentials, Azure operations, seeding or real apply are exercised.
Independent review is required after the final substantive push.
