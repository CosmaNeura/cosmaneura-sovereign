# Accept the authorized independent staging review format

Risk: Tier 2, local staging source admission. No application, schema, identity,
cohort, cloud resource, dependency, or GitHub setting changes.

The owner authorized independent Codex reviews during the operational-beta
work. Current merged Core PR505 carries such a review, but Sovereign's local
source verifier accepts only a Cursor envelope and rejects that valid pair.
Core already recognizes the explicitly labeled agent-review format.

Accept the existing Cursor envelope only for Cursor, and the existing agent
envelope only for Codex. Preserve the exact merged-current-source, reviewed
tree, founder author, PR association, full-comment hash, nonempty report,
reviewed head, accepted result, and report-hash requirements. The final-push
claim must be boolean true. A report remains published independent-review
evidence; this does not constitute or alter GitHub approval.

Local validation must demonstrate both valid formats, refusal of mislabeled
or unknown reviewers, and unchanged source/report binding. A read-only check
against the actual current merged pair verifies practical compatibility.
It must not build, publish, deploy, retrieve Azure credentials, or mutate data.

Recovery is a source revert. Previously accepted Cursor plans remain compatible
throughout. Reverting removes Codex-format admission; it does not change any
running service or retained revision. Future build/deploy operations retain
their separate exact-plan validation and independent operational review.
