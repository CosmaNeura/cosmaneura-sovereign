# Provider-only production artifact preparation

Risk: Tier 2. This checkpoint closes only the offline artifact-identity gap for
the provider Session Prep service. It does not build, publish, deploy, enable,
or configure production. It adds no workflow, cloud command, credential,
provider record, chart data, or activation path.

## Explicit immutable source and fixed scope

`tools/prepare_provider_production_artifact.py` requires an explicit full
Sovereign source commit, without a default or branch/tag input. This permits a
reviewed dependency correction to replace the earlier candidate without
changing the preparation tool for every release. The selected checkout must be
clean, its `HEAD` must equal that two-parent merge, and its archive is produced
through isolated Git metadata using the same external-attribute exclusions as
the existing bootstrap builder. The raw archive SHA-256 and committed tree are
recorded without recording a local path.

The preparation is fixed to the `provider_assistant` mount. It records that no
public image or public source is included and rejects a Sovereign image with a
baked `MOUNT_PROFILE=public` or a conflicting mount-profile label. This is a
release-scope exclusion, not a claim that the generic application source could
never be configured for another mount in a different, separately reviewed
release.

## Existing-artifact inputs

The tool consumes, but never creates or downloads, two local OCI image layouts:

- the exact provider Sovereign candidate and its independently supplied
  immutable manifest digest; and
- one exact existing Core candidate, its full source commit, and its
  independently supplied immutable manifest digest.

Neither service's source or artifact identity has a default. An operator must
supply the actual reviewed candidate pair; the tool does not infer or invent a
not-yet-built image. For
both layouts it checks the raw index, manifest, config, and every compressed
layer against parent digest and size fields. Each index must select exactly one
Linux/amd64 manifest, the config must also declare Linux/amd64, and the config's
`org.opencontainers.image.revision` label must equal the supplied exact source
commit. These checks make a source/image splice, platform substitution, or raw
blob change fail closed.

## Output contract

`prepare` creates one canonical JSON document with exclusive-create semantics
and read-only owner permissions. It contains only public source identities,
content digests, platform names, fixed scope, and false authorization claims.
It has no timestamp, path, URL, identity credential, or free-form operator
field, so identical inputs produce identical bytes. The payload has its own
SHA-256 and an explicit digest of the Core/Sovereign candidate pair.

Every authorization remains false:

- production enabled;
- activation authorized;
- deployment authorized;
- Azure mutation authorized; and
- public deployment authorized.

The source/archive hash and OCI revision-label binding prove only the identities
observed by this offline preparation. They do not prove GitHub review or merge
status, binary reproducibility, runtime correctness, production readiness, or
activation readiness. Those
remain separate release gates with their own independently reviewed evidence.
The release operator must separately match the exact reviewed, merged source
through the existing release controls. Updating the source input never makes an
old image or preparation document valid for the replacement source.

`verify` rereads the document, revalidates those static false claims, rebuilds
the complete observation from the current clean source and raw OCI layouts, and
requires exact equality. Rehashing a modified document cannot turn on a gate or
include a public artifact.

The tool has no Azure, registry, HTTP, Docker, build, or deployment operation.
It is deliberately not called by `deploy-dual.sh`, the existing build workflow,
or `tools/local_staging_release.py`. Production activation remains founder-only
and must separately provide identity/IAM, saved-plan, migration, recovery,
alerting, aggregate eligibility, and synthetic acceptance evidence.

## Offline use

From an exact clean checkout after this change itself is merged:

```bash
PYTHONPATH=. python tools/prepare_provider_production_artifact.py prepare \
  --sovereign-repo /absolute/path/to/exact-sovereign-checkout \
  --sovereign-source-commit <exact-reviewed-merged-sovereign-commit> \
  --sovereign-oci-layout /offline/provider-sovereign-oci \
  --sovereign-image-digest sha256:<exact-sovereign-manifest-digest> \
  --core-oci-layout /offline/existing-core-oci \
  --core-source-commit <exact-reviewed-core-commit> \
  --core-image-digest sha256:<exact-core-manifest-digest> \
  --output /private/provider-production-preparation.json
```

Reverification uses the same seven source/artifact arguments with `verify` and
`--manifest`. The sole stdout value is the preparation payload SHA-256. A
failure emits only a fixed refusal message; it does not echo local paths or OCI
contents.

## Verification and stop condition

Run only offline checks for this checkpoint:

```bash
PYTHONPATH=. python -m pytest \
  tests/test_provider_production_artifact_preparation.py -q
ruff check tools/prepare_provider_production_artifact.py \
  tests/test_provider_production_artifact_preparation.py
ruff format --check tools/prepare_provider_production_artifact.py \
  tests/test_provider_production_artifact_preparation.py
```

The focused tests cover exact source and clean-tree identity, raw manifest,
config and layer tamper, platform drift, Core source/image splicing, public
mount exclusion, rehashed authorization/public flips, successful revalidation,
exclusive output creation, mandatory immutable source input, and replacement
source/image binding after a corrected merge. They build synthetic local Git and
OCI fixtures; they perform no network or cloud action.

Stop on any mismatch. Do not omit either explicit source commit, substitute a tag
for a digest, omit the Core candidate, change platform selection, edit the manifest,
or interpret this preparation as permission to publish or activate anything.

Recovery before release is to revert this source change and retain the explicit
release hold. Do not publish the earlier security-affected candidate to recover
tool compatibility. No runtime, schema, configuration, or deployment changes are
part of this checkpoint.
