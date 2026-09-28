# Magisterium response wait — Tier 2

Two synthetic staging Ethics requests hit the inherited 30-second Magisterium
read timeout during required arbitration, including a short request. The failed
console diagnostic supplies no vendor timing measurement or evidence that a
90-second wait succeeds.

From merged PR63 `5424c28fea09fa59c184d05e52852af6426b8105`, give only Magisterium
REST reads a default 90-second inactivity budget. The optional
`MAGISTERIUM_READ_TIMEOUT_SECONDS` setting accepts finite values from 1 through
120 seconds. This is the maximum wait between received bytes, not a total
response or whole-turn deadline. Connect, write and pool budgets retain
`REQUEST_TIMEOUT_SECONDS`; other clients and retry behavior remain unchanged.
De-identification, required doctrinal review, canonical final authority,
failed usage, typed unavailability and cancellation semantics remain intact.

Independent review precedes the planned single combined provider staging
rollout with PR62/63, using the canonical local release path while Actions stay
disabled. Root retains the current cohort and configuration, Core PR533, public
mount and Scribe API10/native16. No schema or receiver change is needed. Source
default applies without a cloud configuration mutation.

Verify actual successful Ethics output and latency after deployment; this
adjustment alone is not a live workflow pass. If unsuccessful, use the reviewed
canonical recovery plan to the retained deployed `fe82fe3f` image
`sha256:543010825167e5885ca210ade4d57be8f036f37c6756acfafabc90fb9f3579f0`.
A later isolated recovery of just the budget can restore 30 seconds while
retaining PR63 error handling; accepted finals remain immutable.
