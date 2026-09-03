# Session Prep exact staging source candidate

## Purpose and boundary

This flight plan reconciles one exact Core/Sovereign **source pair** for the
next synthetic-only Session Prep staging step. It is an offline source check,
not a deployment, image build, staging validation, provider pilot, or PHI
authorization.

The only accepted pair is:

- Core `fc4fbc299cd1edcf0b5e35c4e95a7ee62ed5907b`, tree
  `b29429957d7b0c2840924ec60bb18efdf68c568d`.
- Sovereign `01be35ab851c4d2cd25a98020437b9ab7ba49665`, tree
  `852a7fbb0a998035485c8b480de9c2a703796512`.

The pair is one tuple. A different commit in either repository is rejected,
even when it is a descendant with identical Session Prep files. Advancing the
candidate requires a new reviewed verifier version; branch names, tags,
abbreviated hashes, and mutable checkout content are not accepted evidence.

## Evidence required

Prepare a fresh receipt only by completing the separate local, loopback-only
[synthetic UI/UX exercise](session-prep-synthetic-ui-ux-exercise.md). Do not
fabricate a receipt from the schema or use a unit-test receipt as operator
evidence. The existing UI verifier deliberately describes a structurally
valid receipt as an unauthenticated, unproven operator attestation.

The receipt must remain local and metadata-only. Never put a screenshot,
provider or patient identifier, record reference, note, transcript, URL,
credential, free-form comment, or PHI in it. Do not commit it.

From a Sovereign checkout containing both exact commits, run:

```bash
core_repo=/absolute/path/to/cosmaneura
ui_receipt=/absolute/path/to/fresh-synthetic-ui-evidence.json
PYTHONPATH=. python tools/reconcile_session_prep_source_candidate.py \
  --core-repo "$core_repo" \
  --core-commit fc4fbc299cd1edcf0b5e35c4e95a7ee62ed5907b \
  --sovereign-repo . \
  --sovereign-commit 01be35ab851c4d2cd25a98020437b9ab7ba49665 \
  --ui-receipt "$ui_receipt"
```

The command performs no fetch and requires every needed object to be present
locally. It reads source only through exact Git objects with replacement
objects and lazy fetching disabled. It copies the bounded receipt once to a
private temporary file, then runs the exact committed
`verify_session_prep_ui_evidence.py` bytes. Git, verifier, harness, receipt,
and local path output is suppressed on every failure path.

## What a pass establishes

A successful canonical JSON packet establishes only:

1. Both candidate commits and trees are exact and descend from the reviewed
   Session Prep acceptance bases Core `3d56d68e…` and Sovereign `b4da6def…`.
2. The complete nine-object v1 contract set has identical bytes and Git blob
   identities in both candidates and both accepted bases.
3. The Core-vendored panel, Sovereign panel, accepted upstream panel source,
   SHA-256, SRI, and Core provenance-control bytes remain exact.
4. Eighteen Session Prep runtime, host, projection, routing, scope, and
   staging-control objects remain byte-identical to their accepted bases.
5. Core and Sovereign runtime defaults are false, and the exact reviewed Core
   staging deploy/workflow controls remain the forced-false versions.
6. The existing closed UI receipt verifier accepted a fresh receipt and its
   exact pinned automated support. This does **not** prove that a person
   performed the observations, who they were, or that any deployed surface
   was exercised.

The newer Core restore/rehearsal objects are part of the candidate tree but
are not interpreted or promoted by this check. This packet makes no claim
about their execution or operational acceptance.

The packet may state `source_pair_reconciled: true`. It always states all of
the following as `false`:

- `staging_deployed`
- `staging_validated`
- `image_built`
- `auth_iam_ready`
- `durable_operational_controls_ready`
- `real_provider_phi_allowed`
- `production_ready`

`evidence_sha256` covers canonical JSON of the nested `evidence` object. The
receipt is represented only by a SHA-256 of the exact bounded file bytes and
by `operator_observation_proven: false`; its contents never appear in output.

## Verification

Run the focused tests and repository checks:

```bash
PYTHONPATH=. python -m pytest tests/test_session_prep_source_candidate.py -q
PYTHONPATH=. python -m pytest tests/ -q
ruff check tools/reconcile_session_prep_source_candidate.py \
  tests/test_session_prep_source_candidate.py
ruff format --check tools/reconcile_session_prep_source_candidate.py \
  tests/test_session_prep_source_candidate.py
```

The focused suite covers exact-pair and tree pinning, absent and non-descendant
objects, pair splicing, replacement objects, disabled lazy fetch, mutable
checkout isolation, contract/panel/control drift, default-on gate shapes,
stale/tampered/unknown receipt content, verifier drift, receipt bounds,
subprocess failure/timeout, deterministic canonical output, and failure-output
redaction.

## Stop and recovery

Any mismatch or missing object, runtime, receipt, or harness fails closed and
emits no evidence packet. Do not fetch during reconciliation, loosen a pin,
edit a failed receipt, or treat a test-generated receipt as manual evidence.
Retain every feature gate as false and stop the staging sequence.

This change creates no deployed state. Recovery is to discard the rejected
packet or revert this verifier/document/test commit. A later image build,
staging deployment, deployed synthetic canary, Auth/IAM acceptance, durable
operational-control acceptance, and explicit go/no-go decision each require
separate evidence. Real-provider and PHI use remain prohibited until the
separate Auth/IAM and operational-control work is complete and approved.
