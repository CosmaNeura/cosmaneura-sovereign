# Reviewed-note evidence mode

Risk: Tier 2, provider context. Owner: Session Prep engineering. Status:
implemented, not yet deployed to staging. The user selected dated excerpt
cards for specific chart questions while retaining the generated preparation
brief as the overview. The server enables exact evidence for ENGAGEMENT
answers only when the existing authorized lane supplies full reviewed-note
sources. Browser metadata cannot choose this mode or its sources. General
education, Visit Prep synthesis, ethics consultation, summary-only fallback,
the generated brief and v1.0/v1.1/v1.2 Core contracts remain unchanged.

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
The panel renders compact dated section cards without displaying the model
indices. Untrusted quoted markup is rendered literally. Existing de-identification
still runs before doctrinal arbitration; no doctrinal MCP is enabled on the
BAA drafting call. The canonical answer contains safe literal Markdown for
exports. A fixed evidence envelope lets the panel reverse that encoding once,
HTML-escape once and preserve source punctuation and whitespace in both live
responses and restored history. The envelope controls formatting only; it
cannot grant authority or validate model claims by itself. Multiline source
titles are flattened into one label line so they cannot manufacture cards.

## Limits

Exact section matching proves provenance only. It does not prove relevance,
that all later corrections were selected, that a historical plan became the
current regimen, or that the whole question was answered. The answer states
this limitation and preserves the actual source-read notice. This is not a
semantic clinical-claim validator or an evaluation of live model selection
quality. The preserved summary-only fallback does not gain any new
machine-validation claim from this change. Future clickable source citations
require an independently authorized Core route contract; date/section labels
do not imply that those links currently exist.

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

Recovery: remove the explicit ENGAGEMENT dispatch of `exact_evidence=True` to
restore prior synthesis without changing preparation artifacts. If reverting
the primitive and panel renderer, revert this PR's implementation commits.
No data cleanup or database rollback is required. Deployment remains within
the existing Session Prep feature/authorized staging cohort; no new flag,
account, permission or cohort is enabled by this change.

## Validation

`tests/test_provider_evidence.py` covers exact old/new note attribution,
unknown fields and model dates/URLs, foreign indices, duplicate keys,
unbounded output, partial-text status distortion, markup, corrected source
text, server-owned dispatch, summary fallback refusal, de-identification,
arbitration replacement and canonical rejection. Existing provider context,
source readiness, routing, ethics, conversation, emergency, sensitive
disclosure and mount tests exercise unchanged behavior. A harness mounts the
actual panel and verifies exact visible inequalities, ampersands/entities,
Markdown punctuation, backslashes and multiline text in both network replies
and restored history. Its HTML output cannot create source-supplied scripts,
images, links, headings or lists. Existing synthesis Markdown remains intact.

An independent agent reviews the final exact commit before any PR merge.
Sovereign receiver/renderer deployment precedes a new reviewed Core panel pin.
Do not reuse or amend an older merged pin. Synthetic live selection evaluation
follows staging activation; production is outside this change.
