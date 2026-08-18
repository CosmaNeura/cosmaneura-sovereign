# Isolated staging live-smoke and paired rollback rehearsal

This is the required non-production path for proving both physical mounts and
their paired rollback behavior. It uses the same Terraform resources and
non-mock controls as production, but every stateful Azure resource has a
`staging` name and the backend key is `sovereign-staging.tfstate`.

Staging owns `cosmaneura-staging-deid` in East US in addition to its own
resource group, ACR, provider and public Container App Environments, Log
Analytics workspaces, apps, runtime identities, Key Vaults, state key, and
secret references. Its provider identity has no role on the production DeID
service. The GitHub OIDC identity and all of its grants are default-off in
staging; this rehearsal uses the reviewed operator path.

## External gates

Do not create the live apps until all of these are true:

- the applicable Azure BAA assertion has been reviewed;
- a staging Clerk issuer/template can mint one approved `provider` JWT and one
  valid JWT without that role;
- the platform host can mint a 60-second record grant for a designated,
  vendor-owned synthetic athenahealth Preview record in practice `195900`;
- the Staging 1Password vault has separate provider/public Magisterium and
  Azure OpenAI items, Preview API credentials, and a staging-only canonical
  Fernet record-grant key;
- the two `.env.staging.*` files contain real, reviewed non-secret values; and
- the operator has an Azure role capable of the reviewed bootstrap/apply.

No real patient record or production EHR credential is permitted.

The Log Analytics/Container App Environment phase must not be applied until the
shared state account's blob versioning and soft-delete recovery policy has been
approved. Workspace shared keys are provider-computed sensitive state fields;
the staging root does not own or modify that cross-stack backend.

## 1. Select staging explicitly

From a clean checkout:

```bash
cp .env.staging.backend.example .env.staging.backend
cp .env.staging.public.example .env.staging.public
cp infra/deployment.staging.auto.tfvars.example infra/deployment.auto.tfvars
```

Replace the Clerk placeholders and review every assertion. Keep all secret
entries as `op://Staging/...` references. Never substitute Production items.
Keep `github_deploy_enabled=false`; no staging workflow currently needs that
federated principal or its deployment roles. Do not add a DeID resource ID or
endpoint: Terraform creates the staging service and injects its returned URL.

Initialize and prove the backend boundary:

```bash
./infra/scripts/init-environment.sh staging
./infra/scripts/preflight.sh staging
```

`init-environment.sh` always uses `-reconfigure`, and preflight independently
reads OpenTofu's backend metadata. Terraform also records a prevent-destroy
environment guard in state, so a cached production backend cannot be reused for
a staging plan without a hard failure.

## 2. Bootstrap foundations and secrets

Keep all three release gates fail-closed. Resolve the operator object ID,
review a targeted plan for the bootstrap roles and their dependencies, then
apply only the reviewed targets described in `DEPLOY_CHECKLIST.md`. The names
must be exactly:

- resource group `cosmaneura-sovereign-staging`;
- registry `cosmaneurasovereignstagingregistry.azurecr.io`;
- vaults `cosmasovstagingprovider` and `cosmasovstagingpublic`.

Build the first clean immutable digest:

```bash
DEPLOYMENT_ENVIRONMENT=staging \
REGISTRY=cosmaneurasovereignstagingregistry.azurecr.io \
  ./infra/scripts/build-bootstrap-image.sh
```

Seed and verify the two exact secret inventories:

```bash
./infra/scripts/sync-key-vault-secrets.sh \
  staging provider cosmasovstagingprovider
./infra/scripts/sync-key-vault-secrets.sh \
  staging public cosmasovstagingpublic

DEPLOYMENT_ENVIRONMENT=staging \
DEID_SERVICE_ID="$(tofu -chdir=infra output -raw azure_deid_service_id)" \
  ./infra/scripts/verify-key-vault-boundaries.sh
```

Set both gates true and `initial_image_ref` to that staging ACR digest in the
ignored `infra/deployment.auto.tfvars`. Review a full plan showing only staging
resources, then apply without `bootstrap_operator_object_id`. Re-run preflight
and the live boundary verifier afterward.

## 3. Run live smokes and force the paired rollback

Build a second clean immutable digest from the exact checkout that will run the
rehearsal. Commit every intended source change and remove every untracked file;
the wrapper refuses a dirty tree. It must differ from the paired baseline. The
operator needs pull access to the staging ACR so the wrapper can resolve the
image config by digest and prove its `org.opencontainers.image.revision` label
equals the checked-out `HEAD` before any Container App mutation or evidence
write. Use a private controlling terminal so short-lived Clerk JWTs and the
record grant are prompted after the new revisions are healthy rather than
persisted:

```bash
DEPLOYMENT_ENVIRONMENT=staging \
IMAGE_REF='cosmaneurasovereignstagingregistry.azurecr.io/cosmaneura/sovereign@sha256:<second-digest>' \
REVISION_SUFFIX='rehearse-<short-id>' \
SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED=true \
SOVEREIGN_INTERACTIVE_SMOKE_CREDENTIALS=true \
REHEARSAL_EVIDENCE_PATH='/absolute/private/path/paired-rollback.json' \
  ./infra/scripts/rehearse-staging-rollback.sh
```

The script proves the source tree is clean, verifies the candidate manifest and
image-config digests plus its exact source revision label, and proves the
baseline is one paired staging digest. It then deploys the second digest to both
apps, runs both live-model mount smokes and the one bounded synthetic Preview
lab read, injects failure only after those pass, and delegates rollback to
`deploy-dual.sh`. Finally it proves both active healthy images equal the exact
original baseline and re-runs the identity/vault/app boundary check.

The JSON evidence contains only resource-neutral revision/image identifiers,
the source commit, timestamps, and the pass result. It never contains JWTs, a
record grant, record reference, response body, answer, or lab value. The file is
created with mode-safe defaults and is never overwritten.

V7/V8 centralized durable audit remains deferred and must still be reported as
non-blocking only for this tightly controlled synthetic Preview rehearsal. It
continues to block any real-PHI, multi-tenant, or durable-audit claim.
