# Session Prep synthetic acceptance packet v1

## Purpose and boundary

This packet proves the source provenance and local synthetic behavior of the
Session Prep vertical slice. It is read-only: it does not deploy, change
configuration, contact a provider system, or authorize real records or PHI.

The v1 verifier accepts only the reviewed Core and Sovereign commits below,
expressed as exact lowercase 40-character Git IDs. It also pins six
security-critical source files by SHA-256 instead of inferring shell or
TypeScript behavior from text. It reads every checked source, schema, fixture,
and browser asset from Git objects rather than from either mutable working
tree. The Sovereign commit is archived to a temporary directory before its
existing four-state demo self-test and panel contract harness run. Every Git
operation disables replacement objects, and every extracted file is compared
byte-for-byte with the pinned tree before execution. Subprocess output,
repository paths, fixture values, context handles, credentials, and payloads
are never copied into the evidence.

## Exact inputs and command

The reconciled source inputs at the time this packet was prepared are:

- Core merge commit: `3d56d68e285ad0a136904f0632874ac67797db5e`
  (tree `eefd766abbe20c51669705c5c6b238ddbd3a5cd4`).
- Sovereign merge commit: `b4da6defb2c31166fc2902c320cc1aa028e3cc3f`
  (tree `66b1484a96bd8a5d14fe9bb27ab19d0088bcef03`).
- Core-pinned Sovereign panel source:
  `024655ca3a542caf02e46d1b9b209d3c5b4d96d0`.
- Panel SHA-256:
  `ab64c6d2135d7b217834363791728ab04fb9d86524f1643cb72f88cfeb4e0d5c`.
- Panel SRI:
  `sha256-q2TG0hNdeyF4NDY3kXKKsE+52GUk8WQ8ty+Iz+tODVw=`.

From the Sovereign repository, run:

```bash
acceptance_core_repo=/absolute/path/to/cosmaneura
PYTHONPATH=. python tools/verify_session_prep_acceptance.py \
  --core-repo "$acceptance_core_repo" \
  --core-commit 3d56d68e285ad0a136904f0632874ac67797db5e \
  --sovereign-repo . \
  --sovereign-source-commit b4da6defb2c31166fc2902c320cc1aa028e3cc3f
```

The command prints one canonical JSON object. `evidence_sha256` is the
SHA-256 of the canonical serialization of the nested `evidence` object, not
of the self-referential outer envelope. Repeated runs with the same Git
objects produce the same evidence and digest. For the exact inputs above, the
expected evidence digest is
`sha256:9215d4f229803f1e076f5eaa0bd2ce8956ee19f0ecc4488f3d4cbe4f6ea3be6a`.

The packet's focused regression and static checks are:

```bash
PYTHONPATH=. python -m pytest tests/test_session_prep_acceptance.py -q
ruff check tools/verify_session_prep_acceptance.py tests/test_session_prep_acceptance.py
ruff format --check tools/verify_session_prep_acceptance.py tests/test_session_prep_acceptance.py
```

A later source commit requires a new reviewed packet version with updated
commit, tree, and source-manifest pins. Merely replacing a command-line commit
ID fails closed. Do not pass a branch, tag, abbreviated SHA, or working-tree
path as a source revision.

## Automated acceptance

The packet fails closed unless all of these statements hold:

1. Core and Sovereign commits and tree IDs resolve exactly to the reviewed v1
   pair with Git replacement objects disabled.
2. The six pinned Core deployment/panel and Sovereign config/demo/harness
   objects match the reviewed SHA-256 manifest.
3. Core's panel SHA-256, SRI, content-addressed filename, and upstream source
   commit agree.
4. The Core-pinned source commit is an ancestor of the supplied Sovereign
   commit, and both `app/panel.js` objects equal the Core-vendored bytes.
5. Both `provider-session-prep-v1` directories contain exactly the expected
   two schemas and seven fixtures, byte-identical across repositories.
6. Sovereign's `feature_session_prep` default is the literal boolean `False`.
7. Core's pinned canonical staging controls force
   `SESSION_PREP_SYNTHETIC_ENABLED` to `false`, validate that forced-false
   set, and records the false release flag.
8. The temporary Sovereign archive contains exactly the pinned regular-file
   paths and bytes; attributes or local Git state cannot substitute content.
9. The exact Sovereign archive's existing demo self-test proves ready,
   refreshing, pending, and unavailable behavior with bounded polling.
10. The exact Sovereign archive's existing Node harness passes against the
   exact Core-vendored panel asset, including record and encounter switching,
   stale-response suppression, revocation, exact retry identity, and browser
   transport/DOM/storage leakage checks.

Successful evidence reports `source_provenance` and
`local_synthetic_acceptance` as `passed`. It always reports these deferred
facts explicitly as false:

- `staging_deployed`
- `auth_iam_ready`
- `durable_operational_controls_ready`
- `real_provider_phi_allowed`

Those values describe this packet's authority. They are not live-environment
observations and must not be edited to claim readiness.

## Manual synthetic UI/UX rubric

Use only the loopback demo and its bundled synthetic record. Do not paste or
transcribe provider, patient, appointment, note, or credential data.

```bash
PYTHONPATH=. python tools/run_session_prep_demo.py --self-test
PYTHONPATH=. python tools/run_session_prep_demo.py --port 8765
```

Open `http://127.0.0.1:8765/demo/session-prep` and verify:

- **Ready:** the bounded prepared brief is readable, its source count is
  understandable, and no internal references or raw note/transcript fields
  appear.
- **Refreshing:** the stale label is unambiguous, only the brief returned by
  that response appears, and the refresh action does not imply freshness.
- **Pending:** prior brief content disappears and the waiting state explains
  that nothing is ready yet.
- **Unavailable:** all prior brief content disappears and the state is
  distinct from an error or an empty ready result.
- **Switching:** changing the synthetic record or encounter clears the panel
  immediately; a late prior response never repaints it.
- **Revocation:** loss of authority closes the panel and removes all prepared
  content without a fallback copy.
- **Privacy:** browser developer tools show a `/session-prep` body containing
  only `client_turn_id`; no context handle, record, note, source, credential,
  or clinical content appears in the URL, generic chat request, or browser
  storage. The DOM contains only the bounded, typed response fields intended
  for display—never raw source text or an internal reference.
- **Usability:** keyboard focus, state headings, retry/refresh controls, text
  contrast, and narrow/wide layouts remain understandable. Record any copy or
  interaction changes separately; do not mutate this evidence after review.

Stop immediately if the demo is not loopback-only, requests a real login, or
shows anything other than the bundled synthetic fixture.

## Limitations and deferred gates

This is source and local synthetic evidence only. It does not prove that a
staging image was built from either commit, that an image digest was promoted,
that migrations or rollback work in an environment, or that live identity,
network, audit, retention, monitoring, alerting, incident response, or
clinical/legal controls are complete.

Real-provider and PHI use remains prohibited until the separate Auth/IAM work
and durable operational-control work are complete, independently evidenced,
and explicitly approved. A later synthetic staging exercise also needs its
own exact candidate SHA, image/recovery digests, protected workflow evidence,
operator approval, and recovery rehearsal. This packet cannot substitute for
any of those gates.

## Failure and recovery

Any mismatch, missing object/runtime, timeout, nonzero self-test, default-on
feature, or disclosure-policy violation exits nonzero and emits no evidence
packet. Child output is intentionally suppressed; diagnose failures by
rerunning the existing self-test or harness directly with synthetic inputs,
then review and commit a new exact source candidate.

The verifier and its temporary archive make no external or repository
mutations. Recovery from a failed run is therefore to retain the default-off
configuration, discard the failed evidence, and stop. If this packet itself
must be withdrawn, revert its single source commit; no runtime rollback or
environment action is required.
