# Session Prep quoted-note footer disclosure

Tier 2: bounded provider-content presentation. A whole selected PLAN section can
carry a care-time statement and an AI-assistance footer that obscure the dated
clinical evidence. The panel places only the two observed, exact terminal
boilerplate paragraphs in a native `details` disclosure, “Additional note text.”
The clinical prefix stays visible. Both rendered parts retain every original
character, including separating and trailing newlines.

Coverage is deliberately closed: care time must be the observed integer 0 or 1
sentence, followed after blank lines by the exact AI-assistance sentence, at the
end of a quote with nonblank preceding content. Modified, unknown, embedded or
nonterminal text stays entirely visible. This is not a clinical relevance filter,
shortened factual answer, or claim that boilerplate never matters to a question.
The disclosure always permits reading it.

Canonical answer, quoted source, dates, citations, stored history, selection
bounds, authority and required review remain unchanged. No selector, Core
receiver, source pin, configuration, dependencies or schema changes are included.
Native disclosure semantics and a visible focus style provide the control;
real screen-reader/device usability has not been tested in this increment.

Validation: 115 focused offline tests passed across provider evidence and footer
rendering, including both known variants, original-text reconstruction, literal
escaping, unknown/nonterminal/clinical-neighbor preservation, native disclosure
markup and live/history replay parity. Node syntax, Ruff and whitespace checks
passed. No live patient, model, network or database test was used.

Release control: John owns the existing provider-beta release decision; this
candidate remains undeployed until independent final-push review. Release the
reviewed Sovereign candidate and a separately reviewed exact matching Core panel
pin through the existing staged release process; this PR does not prepare or
apply that pin. Recovery is the prior reviewed 53631ec8 panel and matching Core
pin, or a reviewed revert with the same asset reconciliation. The release control
ends when the admitted candidate is accepted or restored; no new runtime flag is
introduced. No GitHub workflow, migration, deployment or activation is authorized
by this document.
