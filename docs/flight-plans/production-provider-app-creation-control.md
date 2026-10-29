# Default-off production provider app creation

Risk: Tier 2. This source checkpoint adds one production-only app-creation
control and changes no live resource, state, secrets or deployment workflow.

`production_provider_app_enabled` defaults to false. A separately reviewed
production plan may set it true only while the existing dual-app
`live_apps_enabled` gate stays false. The provider Container App, its env export
read and its existing app-scoped GitHub operator grant then use the new control.
The public app, public env read and public app operator grant remain governed
solely by the legacy dual gate. Non-disabled provider-only use in staging or
combining both controls fails variable validation.

The provider-only path requires
`production_provider_key_vault_secrets_ready=true`,
`key_vault_secrets_ready=false`, and one immutable image from this environment's
ACR. The provider assertion covers only the three provider runtime secrets and
cannot authorize a later dual-app plan. The legacy dual path still requires the
global both-vault assertion. This creation control does not enable the Session
Prep feature: its production profile remains false/synthetic until a separately
reviewed coordinated activation. Omitting the optional provider controls
preserves old production inputs, and staging inputs/profile are unchanged.

Foundation resource addresses and bootstrap topology remain unchanged. Public
identity, vault, logging and app-environment foundations currently exist in the
root independently of `live_apps_enabled`; this checkpoint does not suppress,
delete, move or authorize their provisioning. Review those foundations in the
saved production plan separately before any apply. The current paired
preflight, boundary verification and deploy scripts still assume both mounts;
they are not a provider-only release procedure and must not be used as one.
The production deployment workflow remains disabled.

Before creation, obtain a saved plan showing the exact new provider workload
and no public workload/config read, pin source/image identity, verify provider
identity/Agent API and vault boundaries, and choose a retained recovery image.
Provisioning and any later Session Prep activation remain separate decisions.
Existing `prevent_destroy` protections remain in force: setting the creation
gate false is not a rollback for an already-created app. Recover it on a
compatible retained image; do not remove resources or records as rollback.

Secretless validation covers dark defaults, provider-only and legacy dual plans,
exact provider-only secret bindings, conflicting readiness/gate controls,
staging compatibility, and persistent-input rejection of missing readiness or
mutable/wrong-environment images. No Azure or env-secret access is part of
those checks.
