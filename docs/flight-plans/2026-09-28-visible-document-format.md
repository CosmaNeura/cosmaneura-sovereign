# Visible Document format — Tier 2 flight plan

The existing panel exposes Chat and Document for enabled Concierge and small-talk
requests. These two lanes previously omitted the formatting instruction and
`structured.output_format`, so Document behaved as ordinary chat without a
markdown download. Apply the shared format resolver to their final drafting
prompts and carry the result through the existing response metadata contract.

Scope: Sovereign backend only, including provider paths. Keep Concierge public
trial/MCP context de-identified and retain its trial citations and advisory flag.
Keep the same authority, capability, conversation-history receipt, canonical
final-write and replay behavior. Canonical history restores stored answer text
and presentation only; it does not reconstruct download actions. No panel asset,
Core, schema, credentials, flags, hidden tools, or Scribe changes are required.

Validation: synthetic HTTP requests for both lanes exercise Document and default
chat, server-selected follow-up context, canonical final text/presentation,
final-write conflict and accepted replay. Feed the actual HTTP responses to the
unchanged panel and verify Download .md contains exactly the finalized answer
without another model request. Direct Concierge coverage retains de-identified
public slots, optional MCP, citations, and shared format fallback behavior.
Run affected offline pipeline/history/context tests and repository Ruff checks.

Release: independently review the final pushed commit before merge, then build
and publish the exact merged source through the canonical staging release path.
The existing provider cohort remains the release control; the root release owner
records live synthetic Document/default follow-up acceptance after deployment.
Recovery: restore the preceding reviewed Sovereign image through the canonical
staging controller using its fresh bound recovery receipt. No Core pin or schema
rollback is needed. Previous downloads remain local files; no server persistence
or content cleanup is added. This PR performs no deployment.
