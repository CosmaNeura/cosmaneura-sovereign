# Provider questions from reviewed source notes

`provider-question.context.read@1.1.0` adds the exact bounded reviewed source
sections used by the current prepared brief. This lets record questions recover
dated facts that a brief omitted, such as an earlier office BP. The existing
`1.0.0` prepared-summary exchange remains unchanged.

The v1.1 request is the existing turn-bound request with `tool_version: "1.1.0"`
and `arguments.detail: "reviewed_sources"`. The request cannot select a patient,
note, or snapshot. Core resolves the current encounter and rechecks the existing
provider authority, reviewed-note selection and canonical receipt boundaries.

The result has `schema_version: "1.1.0"`, all v1 fields, and `reviewed_sources`.
Each source carries `source_ref`, `source_version`, an aware `source_at`, and
`sections: {title: text}`. READY sources must match the selected provenance
exactly; pending/unavailable results contain an empty source array. Up to five
notes are allowed. Each note has 1–64 nonblank section titles, no NULs, at least
one nonblank text, and at most 18,000 characters in its canonical JSON section
serialization; the aggregate cap is 90,000. The existing 256-KiB HTTP response
ceiling also applies. There is no transcript or whole-chart read.

Sovereign sends the dated sections and brief only to its existing BAA drafting
path. Private references and source digests are removed from model prompts and
browser answers. Questions use the source sections when present, preserve
documented planned/ordered/cancelled/completed status, and cite note date and
section. Summary-only answers explicitly disclose their narrower read scope.
The deterministic scope notice survives doctrinal replacement and is persisted
with the final answer and its unchanged Core dependency receipt.

Sovereign now prefers the additive [v1.2 source-only exchange](provider-question-context-v1.2.md).
When v1.2 is unsupported, it requests v1.1, then falls back once to v1 only when old Core
returns HTTP 404 with `code: "RESOURCE_NOT_FOUND"` and the exact unregistered
tool detail. The two versions use distinct idempotency keys and one combined
ten-second deadline. Auth failures, record conflicts, outages, invalid data and
version-mismatched success responses never trigger fallback. No pending result
is polled into readiness.

Deploy the additive Core receiver before Sovereign. Recover by returning
Sovereign to the prior version while retaining Core's additive v1.1 receiver;
legacy clients continue using v1. No configuration or permission expansion is
part of this change. Local synthetic tests cover both wire versions, exact
source lineage, legacy fallback, drafting/public-egress boundaries and source
scope persistence. Live provider-answer correctness still requires the staging
synthetic fixture replay after the reviewed deployment.
