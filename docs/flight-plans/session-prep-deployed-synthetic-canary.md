# Flight plan: deployed synthetic Session Prep canary

## Outcome and boundary

- Problem and measurable outcome: the source acceptance packet proves the
  Session Prep slice locally, but there is no credential-safe check of the
  deployed provider-to-Sovereign-to-Core path. Add one operator-run canary that
  proves either the expected disabled boundary or one current synthetic-ready
  response without printing content.
- In scope: a standalone deployed smoke client, offline transport tests, and
  this runbook. The canary uses the existing platform exchange, `/healthz`,
  `/welcome`, and `/session-prep` contracts without changing them.
- Explicit non-goals: runtime behavior, feature/configuration wiring, CI or
  deployment automation, Core code or data, browser UI automation, staging
  mutation, production promotion, Auth/IAM completion, operational-control
  attestation, real-provider use, or PHI.
- Affected users, mounts, services, environments, and data classes: an operator
  may run the tool against the provider mount and platform exchange only after
  selecting a platform-designated synthetic staging record. No patient or
  provider content is accepted by the tool.

## Risk and dependencies

- Tier and reason: **Tier 1** because this is deployment-sensitive acceptance
  tooling for a cross-repository provider path. It changes no runtime,
  configuration, deployment workflow, identity rule, or data schema.
- Dependency order: the already-merged Core receiver and Sovereign caller come
  first. This canary may land while both Session Prep feature gates remain off.
  A future activation and exact-SHA promotion remain separate reviewed work.
- Irreversible or high-blast-radius decisions: none. Removing the three files
  restores the prior source tree.
- Receiver-before-sender: the canary consumes the existing platform mount-token
  exchange and Sovereign browser contract. It introduces no sender or contract
  version.

## Current source and deployed state

- This plan starts from Sovereign `origin/main`
  `39d23c3044dc5bcbd615364f97276362c33c081d`, which includes the merged local
  synthetic acceptance packet.
- At preparation time, Core `origin/master` is
  `8b60ccc82a05740efbaa8ffa6ff7882d3a26e940`. Its Session Prep receiver, panel,
  contract, and forced-off deployment control are byte-identical to the
  accepted Core source at `3d56d68e285ad0a136904f0632874ac67797db5e`.
- No live request or deployment inspection is part of this change. It makes no
  claim about the image, configuration, database, identity, or health of any
  environment.
- The source acceptance packet remains pinned to its reviewed v1 pair. Re-pin
  it only after the exact staging candidate is selected; do not chase a moving
  Core default branch in this PR.

## Feature flag and invocation control

- Existing `FEATURE_SESSION_PREP` remains default-off. This PR neither wires nor
  changes it.
- `--expect` is required and accepts only `disabled` or `synthetic-ready`; the
  tool never infers an expected deployment state.
- Both modes require `SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED=true`, an exact
  provider test JWT, and an opaque platform record reference through environment
  variables. Credentials and identifiers are never accepted as arguments.
- `synthetic-ready` is not permission to enable the feature. Use it only after a
  separate reviewed activation has established the exact synthetic fixture,
  actor, appointment, capability, and server-only context binding.

## What the canary proves

For both modes, the canary:

1. Refuses same-host provider and platform origins and follows no redirects.
2. Requires the provider deployment to report `MOCK_MODE=false`, the
   `provider_assistant` mount, disabled direct auth, and platform authority.
3. Requires the platform exchange's exact HTTP 201 response and browser
   credential contract: a bounded `mctx_` token plus strict RFC3339 issuance,
   refresh, and expiry timestamps. Issuance may be at most 60 seconds ahead of
   the canary clock, the timestamps must be strictly ordered at browser
   millisecond precision, and refresh must still be in the future.
4. Requires every credential-bearing response—including the platform exchange—
   to echo embed version `1.0` and declare `Cache-Control: no-store`; all read
   bodies must also stay within 64 KiB and contain unambiguous JSON.
5. Sends no clinical text. The `/session-prep` body contains only one fresh
   canonical lowercase UUIDv4 `client_turn_id`, identical to its correlation
   header.
6. Makes one browser-level Session Prep request and performs no automatic retry.

`--expect disabled` additionally proves that `/welcome` omits the action and
`/session-prep` returns the closed, nonretryable 403 `CAPABILITY_DENIED`
problem. This mode does not reach the Core Session Prep tool because Sovereign
checks its feature gate first.

`--expect synthetic-ready` additionally proves that `/welcome` exposes exactly
one canonical `Open Session Prep` action and that `/session-prep` returns a
strict, current `SessionPrepResponse` with at least one source and a bounded
prepared brief. Before Pydantic validation, it applies the browser's closed
wire checks for UUID, strict RFC3339 timestamp, number, boolean, list, object,
and text types and bounds, preventing coercible or browser-incompatible values
from passing. Unknown fields, server-only lineage keys, stale, pending,
unavailable, malformed, oversized, redirected, or correlation-mismatched
responses all fail the canary.

## Safe operator use

Use a private shell and obtain a short-lived provider-role token for the
dedicated synthetic staging identity. Set values without echoing them, run one
explicit mode, and unset the credential immediately afterward:

```bash
export SOVEREIGN_PROVIDER_URL='https://<reviewed-provider-origin>'
export SOVEREIGN_PLATFORM_URL='https://<reviewed-platform-origin>'
export SOVEREIGN_SYNTHETIC_PLATFORM_RECORD_REF='<opaque-synthetic-record-ref>'
export SOVEREIGN_SYNTHETIC_RECORD_CONFIRMED=true
read -r -s SOVEREIGN_PROVIDER_TOKEN
export SOVEREIGN_PROVIDER_TOKEN

PYTHONPATH=. python tools/smoke_session_prep.py --expect disabled
# After a separately approved synthetic activation only:
# PYTHONPATH=. python tools/smoke_session_prep.py --expect synthetic-ready

unset SOVEREIGN_PROVIDER_TOKEN
```

Do not use a real provider account, patient record, appointment, note, or
clinical text. Stop if the platform cannot independently restrict the supplied
identity and record to the designated synthetic fixture.

## Evidence, limitations, and promotion gate

- Safe stdout contains named pass gates only. It excludes tokens, record
  references, trace identifiers, response bodies, brief text, and upstream
  error bodies. Capture it only in an approved non-PHI evidence location.
- The enabled canary intentionally creates canonical `session-prep.open` and
  final-response records for the synthetic fixture. It is not a side-effect-free
  health check and must not run periodically or against an unconfirmed record.
- A passing run proves one deployed synthetic journey. It does not prove
  browser layout, record switching, multi-replica behavior, tenant-wide Auth/IAM,
  real-data eligibility, BAA coverage, audit retention/integrity, monitoring,
  alerting, incident response, or clinical/legal approval.
- It does not identify the deployed image. Pair any later run with independent
  evidence of the exact Sovereign SHA, Core SHA, immutable image digest, active
  revisions, configuration, and rollback target.
- Real-provider and PHI use remains prohibited until the separate Auth/IAM and
  durable operational-control work is complete, independently evidenced, and
  explicitly approved.

## Verification and recovery

Before review:

```bash
PYTHONPATH=. python -m pytest \
  tests/test_session_prep_deployed_smoke.py \
  tests/test_deployed_smokes.py -q
ruff check tools/smoke_session_prep.py \
  tests/test_session_prep_deployed_smoke.py
ruff format --check tools/smoke_session_prep.py \
  tests/test_session_prep_deployed_smoke.py
```

The offline suite must prove the ready and disabled paths, exact request
minimization, no retry, confirmation-before-network behavior, distinct origins,
closed schema and correlation checks, response bounds, redirect rejection, and
failure-output sanitization. Then run the repository's full test suite and have
an independent agent review the final pushed SHA.

Recovery is source-only: revert the canary PR. A canary failure authorizes no
configuration change or retry; leave Session Prep disabled, retain the prior
immutable deployment, and investigate using synthetic evidence only.
