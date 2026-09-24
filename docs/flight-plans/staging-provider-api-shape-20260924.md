# Staging provider API serialization comparison

Tier 1. The paired staging provider activation stopped before any update:
Azure's app response includes empty secret-reference values, default probe
fields, `imageType: ContainerImage`, and computed `ephemeralStorage: 2Gi`;
the ready-revision response omits them. Both fixed staging mounts are healthy
on their retained images. Raw dictionary equality incorrectly reports drift.

Normalize only those observed defaults, with the computed-storage exception
limited to the observed 0.5 CPU / 1Gi allocation. Keep exact comparison for
all remaining fields and retain source/review, immutable-image, readiness,
traffic, recovery and public-mount checks. No deployment permission changes,
runtime application changes, dependency changes, schema or production work.

The focused test exercises the real `state` function against both Azure
response shapes and rejects changes to images, command/args, secret references,
inline values, mount profile, resource sizes, probes and unknown fields. The
read-only corrected check also accepted both live baseline mounts at
2026-09-24 02:35 UTC without displaying environment values.

Run the local quality suite and independent Cursor review after the final
substantive push. Merge, build and publish a fresh immutable candidate bound
to current merged Core `82e8ca268cff7bfd3329c6193808bc129a78912e`, then invoke
the reviewed local deployer. Preserve the public mount. Before retrying,
re-observe current targets and make a fresh, unexpired paired plan.

Recovery preserves the existing provider revision `gpt41-20260825` and its
full template/image/flags. There is no database migration. Reverting this
comparison change only restores the false refusal; it does not undo a cloud
deployment. Retain deployment and synthetic workflow evidence separately.
