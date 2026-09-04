# Session Prep exact staging build admission

## Purpose and boundary

This control closes the handoff between the reviewed Session Prep source-pair
reconciliation and the first staging image build. Every invocation of
`infra/scripts/build-bootstrap-image.sh` with
`DEPLOYMENT_ENVIRONMENT=staging` must revalidate a fresh synthetic UI receipt
and the exact Core/Sovereign pair before the script can download Nixpacks or
invoke Azure.

This change does not run that build. It does not push an image, deploy staging,
activate Session Prep, validate a provider journey, or authorize real-provider
or PHI use. Production retains its existing clean-`HEAD` build path and cannot
consume the staging admission mode.

## Exact admitted source

The admission tool delegates source and receipt verification to the already
reviewed `tools/reconcile_session_prep_source_candidate.py`. It accepts no
caller-selected commit:

- Core commit `fc4fbc299cd1edcf0b5e35c4e95a7ee62ed5907b`, tree
  `b29429957d7b0c2840924ec60bb18efdf68c568d`;
- Sovereign commit `01be35ab851c4d2cd25a98020437b9ab7ba49665`, tree
  `852a7fbb0a998035485c8b480de9c2a703796512`.

The Sovereign commit is the image payload. The newer, clean operator checkout
contains the admission/build control but is not silently substituted for the
payload. After admission, the builder independently resolves the exact commit
and tree with replacement objects and lazy fetching disabled, derives the
commit timestamp from that object, archives that commit, and applies the same
full commit as the OCI revision label and immutable cache key. The builder also
binds the canonical build-admission evidence digest to the fixed
`org.cosmaneura.session-prep.build-admission` OCI label; the label is an exact
`sha256:<64 lowercase hex>` value and carries no receipt content or path.

The shell accepts exactly one newline-terminated, 154-byte admission record:
40-byte commit, tab, 40-byte tree, tab, 71-byte SHA-256 identifier, newline.
It reconstitutes the three fields byte-for-byte before use. Git status disables
repository-configured filesystem monitors, and archiving uses a private bare
Git metadata directory with repository, global, XDG, and system attribute
overrides excluded. The admitted tree's committed `.gitattributes` remain in
force; uncommitted `info/attributes` or operator attributes cannot change the
archive.

## Required evidence

First complete the separate loopback-only synthetic UI/UX exercise in
`session-prep-synthetic-ui-ux-exercise.md`. Its worksheet must be advanced one
closed check at a time and sealed into a fresh metadata-only receipt. Do not use
a fixture, a copied test receipt, real provider data, a patient record, PHI, a
credential, or a live service.

The receipt remains an unauthenticated operator attestation. Admission proves
only that its closed structure, freshness, pinned source, and automated
supports pass the existing verifier. It does not prove who performed the
manual observations or that they occurred.

For an offline admission check from a clean Sovereign controller checkout:

```bash
PYTHONPATH=. python tools/admit_session_prep_staging_build.py \
  --core-repo /absolute/path/to/cosmaneura \
  --sovereign-repo . \
  --ui-receipt /absolute/private/path/to/fresh-synthetic-ui-evidence.json
```

The command emits one bounded canonical JSON object. It contains only public
Git identities, digests, the fixed staging registry/repository, and Boolean
claims. It contains no receipt body, source or receipt path, browser metadata,
clinical text, identifier, credential, URL for a provider record, or free-form
operator text.

Its only positive release claims are:

- `source_pair_reconciled: true`; and
- `build_input_admitted: true`.

It always keeps these claims false:

- `image_built`;
- `staging_deployed`;
- `staging_validated`;
- `auth_iam_ready`;
- `durable_operational_controls_ready`;
- `real_provider_phi_allowed`; and
- `production_ready`.

## Future build invocation

Only after the manual receipt exists and this code has its own exact review may
an authorized operator deliberately start a staging registry build:

```bash
SESSION_PREP_CORE_REPO=/absolute/path/to/cosmaneura \
SESSION_PREP_UI_RECEIPT=/absolute/private/path/to/fresh-synthetic-ui-evidence.json \
DEPLOYMENT_ENVIRONMENT=staging \
REGISTRY=cosmaneurasovereignstagingregistry.azurecr.io \
IMAGE_REPO=cosmaneura/sovereign \
  ./infra/scripts/build-bootstrap-image.sh
```

For staging, neither admission input is optional and the image repository is
fixed. Missing, stale, malformed, spliced, drifted, default-on, or path-leaking
evidence stops before the first network or build action. The admission output
is captured internally; the existing builder stdout contract remains one
immutable image reference only after an actual successful build.

Running the command is a separately authorized ACR mutation. It is prohibited
as part of this source-only increment. A future immutable digest must still be
verified, paired with the exact Core deployment, exercised with the disabled
Session Prep canary, and subjected to the reviewed rollback path before any
staging-validation claim changes.

## Verification

Run the focused offline checks:

```bash
PYTHONPATH=. python -m pytest \
  tests/test_session_prep_staging_build_admission.py \
  tests/test_session_prep_source_candidate.py -q
ruff check tools/admit_session_prep_staging_build.py \
  infra/scripts/finalize-nixpacks-context.py \
  tests/test_session_prep_staging_build_admission.py
ruff format --check tools/admit_session_prep_staging_build.py \
  infra/scripts/finalize-nixpacks-context.py \
  tests/test_session_prep_staging_build_admission.py
bash -n infra/scripts/build-bootstrap-image.sh
```

The tests must prove exact reconciler reuse, fixed source identities, bounded
metadata, false deferred gates, tamper rejection, sanitized failure output,
mandatory staging inputs, exact bounded field parsing, admission-before-network
ordering, filesystem-monitor suppression, external-attribute isolation, and an
end-to-end fake build proving explicit commit archive and both provenance
labels. They use fake commands and perform no network, registry, deployment,
provider, or PHI operation.

## Stop and recovery

On any mismatch, retain every Session Prep deployment/activation gate as false
and do not retry by weakening a pin, changing the selected commit, copying a
test receipt, or bypassing the staging builder. Recovery for this source-only
change is to revert the admission tool, builder integration, tests, and this
flight plan, including the optional admission-label support in
`infra/scripts/finalize-nixpacks-context.py`. No cloud rollback is needed
because this increment creates no cloud state.

Real-provider and PHI use remain prohibited until the separate Auth/IAM and
durable operational-control work is complete, independently evidenced, and
explicitly approved.
