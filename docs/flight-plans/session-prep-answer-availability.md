# Session Prep answer availability and verified export

Tier 2: provider answer display and the opt-in cross-service read contract.
This is local source preparation on admitted provider A6. Source publication,
staging rollout, production activation and host asset changes require their
own review and authorization. Existing user policy hides unavailable answers;
this change does not delete records or expand retention.

Core owns the deadline. Deploy its separately reviewed SOURCE10 receiver,
`POST /service/v1/agent/conversation-turns/read-display-timed`, before this
provider sender and the matching generated panel/host asset pin. The closed
request is schema `1.3.0`, mode `recent` or `turn`, and an exact turn UUID or
null. The retained display envelope adds UTC `observed_at` and an assistant
`expires_at` determined from the earliest verified persisted receipt deadline.
User messages carry null expiry. Existing closed 1.0/1.1/1.2 routes remain.
The provider proxies only this exact read through `/embed/history-timed`,
retaining existing mount/workload authority, trace, size and no-store bounds.
Core supplies receipt validation evidence; this provider owns no migration.

The panel obtains a fresh timed read before showing an accepted provider
answer. It subtracts the whole measured request/callback duration from the
server remaining interval, using a monotonic clock. Missing, invalid,
expired, offline or mismatched metadata leaves text and actions hidden.
Close, tab hiding, record change and destruction revoke cached display before
any fresh callback can return. Reopen and visibility resume require a new
read. At the deadline, answer text and actions are replaced by a status;
the provider's input draft is preserved.

Every provider export performs a fresh exact-turn read under the same current
encounter/context. Turn reference, canonical answer, recorded date, expiry,
presentation and reviewed source map must still match. Any failure saves no
file and never falls back to cached text. Export auth loss also revokes access
while retaining the newer input draft. The Markdown adds authoritative answer,
verification and availability clocks without altering the canonical answer or
dated source excerpts. The filename has the verification clock and no patient
identifier. Patient legacy behavior remains unchanged.

Local verification uses synthetic closed-contract fixtures, MockTransport,
TestClient and existing built-in Node fake DOM/clock fixtures. Normal provider
Python 3.13.15 binds all 39 locked packages; 95 targeted tests passed. The first
normal run's 94 pass/one incorrect missing-version expectation is retained;
the corrected test verifies both existing 426 version and 401 auth gates.
Eight current Node recipes passed, including expiry/resume/export races.
The immutable older untimed export fixture passes on A6 and fails against the
new timed contract; this compatibility boundary is recorded separately.

Before any rollout, bind final source review, generated delivery provenance,
receiver readiness and exact host panel pin. Pair rollback must retain this
answer-hiding policy: an old panel that trusts untimed cached answers is not a
policy-preserving recovery. Keeping this sender without the timed receiver
fails closed, so the operational recovery is a verified paired release or
temporarily hiding provider answer actions. Scribe, backend ownership,
existing records, other feature flags and production configuration are outside
this source change.
