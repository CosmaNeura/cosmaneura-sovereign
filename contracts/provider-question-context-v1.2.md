# Reviewed-source questions independent of brief generation

`provider-question.context.read@1.2.0` uses `detail: "reviewed_sources"` and
the same current-encounter authority as v1.1. It changes only the interpretation
of result readiness: READY means reviewed source notes are available, while
`brief_state: "ready" | "pending" | "unavailable"` describes the generated brief.

A source-READY result always contains the exact selected reviewed-source
provenance, versioned section content, current-through source date and selection
completeness. The existing five-note, 18,000-character per-note, 90,000 aggregate
and HTTP-response limits remain. A brief-READY result also contains the current
prepared brief and generation time; otherwise both are null. No source-ready
response can contain an old brief. With no eligible reviewed notes, the result
is UNAVAILABLE, reason `no_eligible_sources`, brief state UNAVAILABLE, and no
content/provenance/timestamps. There is no source-PENDING state.

Sovereign answers from these reviewed source sections without starting,
polling or waiting for brief generation. The canonical final answer still
requires Core acceptance of the exact source-only receipt. The receipt's
initial brief state does not change when generation finishes later.

Sovereign prefers v1.2, then v1.1, then v1.0 only after each older Core version
explicitly rejects an unregistered tool with the documented 404 error. Every
attempt has a distinct version-bound key within one ten-second total deadline.
Auth, record conflicts, invalid responses and generation status never trigger
fallback. Legacy v1 summary-only behavior retains its explicit scope notice.

Deploy the additive Core receiver first. Rolling Sovereign back retains the
older v1.0/v1.1 behavior without removing Core's additive receivers. No new
identity, permission, external EHR capability or database schema is required.
