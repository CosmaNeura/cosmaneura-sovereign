# Sovereign staging provenance — 2026-09-02

**Status:** read-only reconciliation record; no deployment, configuration,
traffic, registry, or other cloud state was changed.

## Observation

Observed at `2026-09-02T12:56:18Z` through Azure CLI metadata and the public
health endpoint.

| Item | Observed value |
|---|---|
| Resource group | `cosmaneura-sovereign-staging` |
| Container App | `cosmaneura-sovereign-staging-app` |
| Active/latest ready revision | `cosmaneura-sovereign-staging-app--gpt41-20260825` |
| Revision created | `2026-08-25T14:32:00Z` |
| Revision state | active, `Healthy`, `Provisioned` |
| Replicas / traffic | 1 replica; 100% traffic |
| Revision mode | `Single` |
| Image | `cosmaneurasovereignstagingregistry.azurecr.io/cosmaneura/sovereign@sha256:e37370a7746a1618927a88987c77c192f0891f5e4b87d24a349a234eaaeb649d` |
| Public health | `status=ok`, non-mock, auth disabled, provider-assistant mount, Platform Agent API enabled |

The selected nonsecret runtime flags were:

```text
MOUNT_PROFILE=provider_assistant
AUTH_MODE=disabled
BAA_ATTESTED=true
MOCK_MODE=false
PLATFORM_AGENT_API_ENABLED=true
FEATURE_ATHENA_LABS=false
DEID_PROVIDER=azure
LLM_PROVIDER=azure_openai
MOUNT_TENANT_ID=tenant_staging_preview_195900
SOVEREIGN_MODE=hybrid
AUDIT_SINK=stdout
```

No secret-backed environment value, token, request body, patient reference, or
clinical content was read or recorded.

## Source mapping

ACR manifest metadata reports that the live digest was created at
`2026-08-21T11:49:06.6767826Z` and currently carries the single tag
`91410aa920ad5cde00b2feec3efe5e4ccceec869`. The repository's read-only OCI
revision-label verifier independently accepted that same digest/source pair.

GitHub identifies SHA `91410aa920ad5cde00b2feec3efe5e4ccceec869` as the merge
commit for [PR #23, “Fix Azure GPT-5 temperature compatibility”](https://github.com/CosmaNeura/cosmaneura-sovereign/pull/23),
merged into `main` at `2026-08-21T11:34:44Z`.

After a fresh `git fetch origin`, the contract-consumer baseline is
`origin/main` SHA `5a8f4a3ac207fac7f1ed7f804e6812eccfafe7f1`. Git ancestry
checks show the live source is an ancestor of that baseline and is **17 commits
behind it**. Staging therefore does not represent the current source baseline.

A known tenant-enforcement change at
`73d18a32ab9744af05cabd6e4936e16568ff2909` is not an ancestor of either the
live source SHA or current `origin/main`. It must not be described as deployed
or merged.

## Source inventory and disposition

| Source | Relationship | Disposition for this slice |
|---|---|---|
| Live `91410aa920ad5cde00b2feec3efe5e4ccceec869` / digest `sha256:e37370a7746a1618927a88987c77c192f0891f5e4b87d24a349a234eaaeb649d` | Healthy staging deployment; 17 commits behind current `origin/main` | **Retain temporarily.** This contract-only change does not replace or mutate staging. |
| `origin/main` `5a8f4a3ac207fac7f1ed7f804e6812eccfafe7f1` | Clean source baseline for the dormant consumer contract | **Assimilate contract only.** Review and merge independently before any activation decision. |
| Architecture `25806a26734fb3f3ea61680436ca2ed88d268b08` | Ratified `session-prep.context.read@1.0.0` schemas and synthetic fixtures | **Consume byte-for-byte.** This slice adds only dormant receiver models and tests. |
| Tenant-enforcement `73d18a32ab9744af05cabd6e4936e16568ff2909` | Separate source line; absent from both current `main` and the live image | **Keep separate.** Reconcile authentication/tenant behavior in its own reviewed change; do not fold it into this contract slice. |

Replacing the live image is explicitly deferred. A later promotion must first
reconcile the 17-commit drift, select the intended tenant/auth changes, and
verify one reviewed source SHA and immutable image digest.

## Other deployment facts

- The public health response was:
  `{"status":"ok","mock_mode":false,"auth_mode":"disabled","mount_profile":"provider_assistant","platform_agent_api_enabled":true}`.
- Azure reports that the Terraform-declared production resource group
  `cosmaneura-sovereign-prod` does not exist.
- This report does not establish Session Prep context-read availability. The
  contract models introduced alongside it are dormant and are not imported by
  runtime code.

## Read-only evidence commands

The reconciliation used only:

```text
git fetch origin
az containerapp show ... --query <nonsecret projection>
az containerapp revision show ... --query <revision projection>
az acr manifest show-metadata ... --query <digest/time/tags>
infra/scripts/verify-acr-image-revision.py <registry> <repository> <digest> <source-sha>
gh pr view 23 --repo CosmaNeura/cosmaneura-sovereign --json ...
git merge-base --is-ancestor ...
git rev-list --count ...
curl --fail --silent --show-error --max-time 15 <staging-fqdn>/healthz
az group exists --name cosmaneura-sovereign-prod
```
