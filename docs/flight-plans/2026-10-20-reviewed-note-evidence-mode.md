# Reviewed-note evidence mode

Risk: Tier 2, provider context. Owner: Session Prep engineering. Status: dark,
not dispatched by the public endpoint and not activated in staging. This is
an explicit server-only agent argument; browser metadata cannot enable it.
The provider-facing choice between synthesis and evidence excerpts remains
with the user. Existing synthesis, ethics consultation and v1.0/v1.1/v1.2 Core
contracts remain unchanged.

## Deployable outcome

Add a bounded evidence-selection primitive for factual record questions. The
BAA-covered drafting call returns only source/section indices and exact full
section text. Sovereign validates the complete selection against the already
authorized reviewed-source version, derives date and section labels itself,
and renders quoted sections without model-written clinical assertions.

This deliberately requires the full selected section, rather than a substring
or line. A standalone line can omit a correction or negation immediately above
it. Bounds are eight sections, 3,000 characters per section, 8,000 quote
characters in total, 40,000 raw model-output characters and 18,000 rendered
characters. A section that exceeds these limits cannot be excerpted in this
mode. No evidence is salvaged from an invalid selection.

The model sees disposable zero-based indices, not Core source handles or
version digests. Dates and labels derive from authorized metadata. There is
no authorized visit route in Core v1.2, so this mode does not invent links.
Untrusted quoted markup is rendered literally. Existing de-identification
still runs before doctrinal arbitration; no doctrinal MCP is enabled on the
BAA drafting call.

## Limits

Exact section matching proves provenance only. It does not prove relevance,
that all later corrections were selected, that a historical plan became the
current regimen, or that the whole question was answered. The answer states
this limitation and preserves the actual source-read notice. This is not a
semantic clinical-claim validator or an evaluation of live model selection
quality. Until the UX choice is resolved, normal synthesized answers do not
gain any new machine-validation claim from this change.

## Recovery and authority

Invalid or empty selections produce a fixed non-answer, no fact citations and
an unavailable canonical outcome. They do not rerun the same accepted turn or
fall back to unvalidated prose. A new question can make a new attempt. A
summary-only context cannot support this explicit exact-source mode.

A nonconforming doctrinal verdict blocks the quoted answer even if the verdict
does not supply replacement prose. Replacement prose cannot inherit chart
citations; the mode returns a fixed non-answer and retains the real review
metadata. The normal synthesis and ethics replacement behavior is preserved.

The final response retains its provider-context receipt dependency. Core's
existing final-acceptance authority/freshness check can still reject corrected,
revoked or mismatched context before answer egress. No schema, migrations,
configuration, dependency, browser ingress or permission changes are required.

Recovery: remove any future explicit server dispatch of `exact_evidence=True`
to restore normal synthesis; this PR itself has no active dispatch to remove.
If reverting this primitive, revert the implementation commit. No data cleanup
or database rollback is required.

## Validation

`tests/test_provider_evidence.py` covers exact old/new note attribution,
unknown fields and model dates/URLs, foreign indices, duplicate keys,
unbounded output, partial-text status distortion, markup, corrected source
text, dark/default behavior, summary fallback refusal, de-identification,
arbitration replacement and canonical rejection. Existing provider context,
source readiness, routing, ethics, conversation, emergency, sensitive
disclosure and mount tests exercise unchanged behavior.

An independent agent reviews the final exact commit before any PR merge.
Staging activation and synthetic live selection evaluation are separate steps
after the provider UX choice; production is outside this change.
