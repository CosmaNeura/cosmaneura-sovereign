# Reviewed excerpt emission

Tier 2: this is an optional provider source-association contract. It changes no
capability, review gate, authentication, configuration, schema or deployment.
The source base is the normal merged PR100 commit
`7bd07d69f17d5577ec49501ff927b76d33c9aafe`.

The owner is the Session Prep integration team. The initial cohort is only the
already authorized staging integration. Production remains outside this plan.
The support header is protocol negotiation, not review or activation evidence.
Keep this plan through paired acceptance; close it when that evidence is saved.

Core first advertises exactly one `X-Cosmaneura-Reviewed-Excerpts: 1.0.0` header
on an authorized ready reviewed-source response. Sovereign checks that signal
after the existing transport and DTO guards. Missing support preserves legacy
payloads; unknown or duplicate support fails closed without a downgrade.
The signal stays private and never enters a selector or reviewer prompt.

After validated section selection and the existing required conforming review,
Sovereign sends the optional final `reviewed_excerpts` namespace. It contains
only the closed version, ordered-enumeration SHA256, mode, and contiguous ordinal
with original received source/section indices. Selection and timeline sorting
never reindex the DTO. The digest binds all received source identities, full
visit timestamps, and ordered sections, including unselected sections.

The existing answer, dates, scope notice, disclaimers, public structured data,
and citation titles stay unchanged. Sovereign emits no URL or source reference.
Core must reconstruct that exact complete answer, retain the accepted note
version and independent review time, and derive authorized display citations.
An invalid selection, nonready context, failed review or unavailable dependency
cannot carry the namespace. Ambiguous canonical-final closure retries the exact
same final arguments without rereading or selecting again.

Receiver-first qualification must include the existing source-scope notice
before the provider disclaimer. The local pure-helper comparison initially
found the receiver omitted that notice, so paired acceptance remains blocked
until its owner repairs and independently verifies that exact envelope. No
provider envelope change or review relaxation substitutes for that repair.

Offline regression coverage lives in `tests/test_reviewed_excerpt_emitter.py`:
transport negotiation, legacy equality, original/reordered sections, same-day
identical quotes, timeline chronology, full-enumeration binding, strict closed
validators, review/failure suppression, canonical writing and deadline replay.
Models and canonical transports in pipeline cases are synthetic mocks; these
tests prove no deployed behavior, clinical completeness or real authorization.

Before staging publication, obtain independent review on exact final source,
ordinary CI, and the owner's compatible accepted-final/read-citation tests.
Then reconcile the actual image pair under the separately authorized staging
release plan. Visible links/history/export belong to a subsequent consumer
change and must use only Core-accepted read maps. A merge does not deploy.

Recovery is a normal reviewed revert of this emitter change to the PR100
behavior, followed only by an authorized immutable staging replacement. A
compatible Core receiver continues accepting legacy finals without the optional
namespace. Never force-push, remove the scope notice, bypass source/review
validation, or change the production latch for recovery.
