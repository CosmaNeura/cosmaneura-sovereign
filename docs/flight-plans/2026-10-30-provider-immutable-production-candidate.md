# Provider-only immutable artifact preparation and selection

Risk: Tier 2 (production artifact/provenance). Source-only; no deployment, activation,
identity, role, configuration or database changes. Founder dispatch only.

## Problem and resulting behavior

The legacy dual workflow builds before deploying both mounts; it cannot supply a provider-only artifact.
The offline OCI pair tool proves bytes and authorizes no promotion. Both stay unchanged.

`Prepare provider production artifact` provides two separate modes:

1. `prepare` requires founder original/rerun actors, canonical manual current
   `main`, clean exact Git source and successful pinned quality/infrastructure
   jobs. The new preparation latch defaults off. An isolated Git archive and
   checksum-pinned Nixpacks generator produce a hash-locked Linux amd64 image;
   the existing finalizer pins the base image and validates generated arguments.
   The image carries only the provider dark defaults: Session Prep false and
   synthetic context. Fixed source/build files are checked in an owned stopped
   image without executing it. The exact config ID is pushed to the fixed
   provider production repository. A no-force candidate tag and digest manifest
   are made non-writable/non-deletable and read back before an artifact is saved.
2. `select` has no Azure identity. It accepts only an authenticated successful
   earlier prepare run/attempt at the same exact current source, complete job
   inventory, canonical active workflow and immutable artifact API ID. The ZIP
   bytes must match GitHub's digest and contain exactly one bounded regular
   `candidate.json`. Source/archive/build-contract identities, registry/repository
   and dark configuration must match. A separate provenance selection receipt
   references the producer/archive and current selection run; it grants no
   deployment authority. A caller's self-hashed document or readiness Boolean
   never substitutes for authenticated producer evidence.

## Before a founder may prepare artifacts

Review and merge this source. Keep the legacy deployment latch off. Provision
and separately verify the production registry and a registry-only federated
artifact identity before enabling `SOVEREIGN_PROVIDER_ARTIFACT_PREPARATION_ENABLED`.
This source does not create that identity or establish its privileges. The new
workflow uses `AZURE_PROVIDER_ARTIFACT_CLIENT_ID`; no deployment identity is
silently reused. Start a fresh run if `main` advances. No agent dispatches it.

## Validation and remaining gates

Synthetic tests cover metadata/ZIP/source/config failures, token isolation,
stopped-image cleanup, retention and publication ordering. They do not perform
live builds, GitHub artifact downloads, registry locking or runtime acceptance.
API metadata is capped at 1 MiB; candidate JSON at 128 KiB; the artifact archive
at 1 MiB. Client output uses a private spool and deadline with a post-download
byte check, not a streaming download limit. GH's fixed-host API client handles
artifact redirects; no caller URL or manually forwarded bearer is accepted.

A later separately reviewed provider-only consumer must re-admit this producer,
verify fresh registry retention, bind the compatible production Core/frontend
pair, strict TLS/schema/identity/role/configuration and recovery state, and use
the admitted digest for both initial creation and later update. This PR supplies
none of that activation or live acceptance evidence. Public, Core, staging,
Scribe and legacy dual resources remain outside its targets.

## Recovery

Leave the new preparation latch false; do not dispatch preparation. Revert this
source to restore the prior workflow inventory. If the founder later creates
retained artifacts, preserve them: switching off preparation does not remove
registry artifacts or roll back an app. No app, schema or data rollback is
performed by this source outcome.
