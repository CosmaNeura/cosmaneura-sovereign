# Default-off production provider Session Prep profile

Risk: Tier 2. This source checkpoint makes the production provider configuration
compatible with the platform-authoritative Session Prep runtime. It creates no
Azure resource, changes no live configuration, and leaves workflows disabled.

The production example keeps `FEATURE_SESSION_PREP=false` with
`SESSION_PREP_CONTEXT_SOURCE=synthetic`, no fixed synthetic context handle, and
the separate persistent app-creation gate remains `live_apps_enabled=false`.
The false/synthetic pair is a dark runtime state, not a synthetic production
chart source. A future reviewed activation uses only the true/platform pair.

Production provider authentication always uses Core's opaque mount token:
`AUTH_MODE=disabled`, `PLATFORM_AGENT_API_ENABLED=true`, the exact production
Core origin and current Agent API scope, and the Terraform-owned provider UAI.
The legacy Clerk metadata retained in the export does not authorize a request;
Core owns browser identity verification, current practice entitlement and note
ownership. No per-user Session Prep role stamping is required.

The profile sets `FEATURE_ATHENA_LABS=false` and value-free operational audit to
stdout. Production vault sync, Terraform secret bindings and inventory checks
require no unused Athena credentials. Staging's existing accepted env keyset,
profile and vault inventory are preserved. Public exports reject all provider
feature, context, workload identity and record-grant controls. The existing
platform capability ceiling continues to exclude referrals, labs, plan
workbench and journey digest from both welcome and execution.

Before a founder-approved production activation, merge and review this source;
prepare exact immutable Core/Sovereign images and frontend origin/flag/SRI;
provision the dedicated provider service and verify its UAI/AgentApi.Access
binding to Core's exact audience/issuer/allowlist; confirm schema compatibility
and worker monitoring; and retain a compatible recovery image pair. Keep Core
availability off during preparation. Apply any required schema first, update
Core worker/scheduler before API, and enable only the coordinated paired
release after synthetic acceptance. This checkpoint does not supply the
production role-assignment planner, artifact-promotion workflow, smoke-token
exchange, or provider-only app-creation/deployment control.

Recovery for this profile changes the feature/context pair back to
false/synthetic together and disables Core's global availability switch through
a separately reviewed release. It does not drop schema or records, weaken
authentication, restore unused Athena credentials, or activate a public app.

Validation is secretless: exercise both accepted profile pairs from env export
through runtime startup validation; refuse mixed auth, origins, scopes, context
controls and public leakage; preserve staging acceptance; and run existing
deployment, capability and Session Prep regressions plus OpenTofu validation.
