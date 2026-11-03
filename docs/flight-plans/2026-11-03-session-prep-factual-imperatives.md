# Exact reviewed facts for a bounded historical imperative

Tier 2: this change affects provider reviewed-record answer routing. It does
not add record authority or enable an external or clinical-action capability.

An observed request, "Show the recorded blood pressures and medication
instructions from both reviewed prior visits," could receive a Visit Prep
classification and enter narrative synthesis. The complete historical-query
matcher lacked that imperative form. The matcher now recognizes bounded
Show/List/Quote variants of this same documented-fact request. The existing
override sends them to Engagement's exact reviewed-section selector, preserving
dated quotations and canonical retained-citation coordinates.

The external-read, advisory, and care-action vetoes remain in place. Complete
matching rejects extra clauses. Reviewed-source readiness, canonical record
and capability gates, selection validation, and doctrinal review are unchanged.
A ready reviewed-source response supports this lookup while its brief is
pending or unavailable. General visit overviews retain narrative synthesis
with the actual reviewed-source footer.

Validation uses fictional records, a scripted Visit Prep classification, both
reviewed visits and a dated medication correction. Tests assert exact text,
dates, citation coordinates, required review and no narrative fallback; they
also preserve prescribing/external refusals and overview behavior. These are
dispatch/contract regressions, not live-model relevance or clinical validation.

Independently review the final provider head, normally merge, then use the
existing authorized provider release procedure. No Core asset, database,
configuration, or Scribe image change is required. Recovery is to restore the
previously verified provider revision; no source-note or answer-data rewrite
is needed. This local patch does not publish or deploy.
