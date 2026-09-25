# Preserve the provider staging recovery revision

Risk: Tier 2, provider staging deployment and recovery. This changes only the
existing local Sovereign provider deployer; it adds no application, API, schema,
dependency, identity, or cohort behavior.

On September 25, the provider app had `maxInactiveRevisions=0`. The deployer
recorded a recovery revision without ensuring it would remain available after
replacement. Its immutable image is useful but does not by itself preserve the
old revision's complete environment, commands, probes, and resources.

The existing image update now also sets inactive-revision retention to ten.
After the candidate is healthy and the public mount is unchanged, the deployer
checks retention and reads the exact plan-bound preceding revision, requiring
its original image and successful provisioning. Missing retention, a removed
revision, or an unexpected image prevents a successful deployment report.

Local acceptance executes the actual deployment function against an Azure
test double. It proves that only the provider app is updated, the plan remains
unchanged, and seven retention/recovery failure cases cannot report success.
The existing source, target, and container-drift tests remain in scope.

This source change is not evidence of a live configuration update or rollback.
The first deployment must retain the currently running compatible baseline and
verify it after replacement. Before a revision-copy recovery exercise, compare
the previous template and application-scoped secret references with current
Core compatibility. Exercise and verify the recovered health and Session Prep
workflow; retention alone is not complete recovery acceptance.

Source recovery is a revert of this deployer change. Reverting source does not
lower an already-applied retention setting. Do not reset retention to zero or
delete retained revisions as part of recovery. Core and separate Scribe services
are outside this deployer's mutation scope.
