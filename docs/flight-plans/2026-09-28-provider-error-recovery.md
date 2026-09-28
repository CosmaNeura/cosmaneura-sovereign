# Provider transport failure recovery — Tier 2

This provider-path change prevents a transport failure during drafting or
mandatory doctrinal review from leaking an unreviewed answer or leaving a
handled accepted turn without a final outcome. It includes the merged greeting
fix parent `38464ad584d29bc446fa015da96851b0a787aa06`.

Sovereign emits a fixed unavailable answer, records failed model usage and a
failed terminal observation, and writes canonical `outcome=unavailable` with
all consumed history/prepared-context receipts. Core remains the authority for
final acceptance. Authentication, scope or dependency refusal blocks egress;
an unsuccessful final write remains nonretryable and available for explicit
response checking. Cancellation and accepted-UUID replay retain their prior
semantics. No historical pending turn is backfilled or rerun.

The receiver contract, schema, panel asset, timeouts and feature flags do not
change. Existing Core PR533 (`b4cf6da9`, image `9afa6b56…`) is the compatible
receiver. Keep the current provider cohort, public mount and Scribe API10/native16.

After independent review of the final pushed head, root may merge and use the
canonical local build/publication/deployment path under the existing staging
delegation while Actions remain disabled. Verify exact source/image readiness,
preservation and both a real successful Ethics consult and an unavailable-error
case. A handled unavailable answer is not successful Ethics workflow acceptance.

Recovery is the retained provider image
`sha256:543010825167e5885ca210ade4d57be8f036f37c6756acfafabc90fb9f3579f0`
from deployed `fe82fe3f`; use the canonical reviewed recovery plan. Already
accepted unavailable finals remain immutable; rollback does not delete them.
