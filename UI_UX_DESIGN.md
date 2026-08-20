# UI/UX Design — Two Surfaces, One Brain

**Status:** versioned reference implementation. The provider-first platform
activation uses the protected `GET /welcome` bootstrap and the versioned Cosmas
dashboard panel. Public activation and the labs lane remain disabled rollout
steps.

The chatbot ships on two surfaces. Both are thin clients over the **same two
endpoints** — `GET /welcome` (bootstrap) and `POST /chat` (everything else).
Both calls require the opaque platform mount token and embed version `1.0`.
The browser supplies presentation hints, never authority.

```
Surface A: chat.cosmaneura.com          Surface B: app.cosmaneura.com
(standalone, paying users)              (native panel inside Cosmas)
        │                                       │  + automatic context
        └──────────────┬────────────────────────┘
                       ▼
        GET /welcome · POST /chat  (this repo — the Sovereign)
```

---

## 1. Design principles — or: why Copilot annoys people, and what we do instead

| Copilot failure mode | Our countermeasure |
|---|---|
| **Context-blind** — often doesn't reliably know what you're looking at; you re-explain | Surface B sends only opaque platform snapshot/thread/digest references for the open record. Sovereign retrieves governed context through the platform boundary; the browser does not replay raw notes. |
| **Answers stranded in the sidebar** — you copy-paste results back into your document | Responses carry machine-readable payloads (`structured`, `citations`); the panel offers one-click actions: *Insert into note*, *Save prep to visit*. The answer lands where the work is. |
| **Popup amnesia** — every invocation starts from zero | The platform owns canonical accepted/final turns. The host may hydrate governed history for display; browser callbacks are never the durability boundary. |
| **Mode pickers and ribbon buttons** — the user must know which feature to summon | Invisible routing. The clinician just types; the Sovereign's router picks the lane (visit prep / ethics consult / referral / explain). Chips are suggestions, never modes. |
| **Nagging** — autopop-ups, upsells, "try asking…" toasts | Quiet by default. A collapsed rail with one button. It speaks first **only** when opened, never uninvited. |
| **Opaque answers** — where did this come from? | A visible trust surface: source citations, the doctrinal-review line ("✓ Reviewed for conformity with Catholic teaching"), and intent badges. Clinicians extend trust to what shows its work. |

**Not a plugin, by design.** Browser extensions and Office add-ins are
workarounds for not owning the host application. You own both hosts. Surface A
is a page this repo already serves; Surface B is a first-party component in the
Cosmas frontend with server-side access to its own data. Nothing to install,
nothing to break on a Chrome update, no extension-store review cycle.

---

## 2. Surface A — standalone chat (paying users)

**What it is:** the full-page chat at its own URL (e.g. chat.cosmaneura.com),
the direct-to-user paid product. First implementation is in this branch
(`app/demo.html`, served at `GET /demo` when enabled) and already implements
the interaction design:

- **The AI speaks first**: greeting + guided-path chips from `GET /welcome`
  (per-persona; deterministic; no LLM call).
- **Emergency treatment**: an `intent=emergency` reply renders as a visually
  distinct red-bordered card, not an ordinary bubble.
- **Trust surface**: intent badge, source line, doctrinal-review line.
- **Structured cards**: provider matches (with booking links) and trials
  render as cards, not prose.
- **Governed disclaimer surface** on standard responses; explicit sensitive
  presentation suppresses it rather than relying on client inference.

**Auth & payment gating (deployment, not new code):** Clerk authenticates the
browser and the platform exchange validates the applicable entitlement. The
page sends only the returned opaque mount token to `/chat`; platform-enabled
Sovereign runs with direct OIDC verification disabled.
Unauthenticated visitors see the marketing/paywall page, not the chat.
Provider authority comes only from the platform-resolved mount context; a role,
persona, or record hint in browser JSON cannot grant it.

**Later (Phase 2/3, unchanged):** per-tenant theming of this same page, and
the sandboxed-iframe widget for **third-party** partner sites (MyCatholicDoctor).
The iframe is for sites you don't own; Cosmas never needs it.

## 3. Surface B — native panel inside Cosmas

**What it is:** a right-side rail in app.cosmaneura.com, collapsed to a single
button, expanding to the same chat UI as Surface A (same component styling,
same rendering rules) plus context awareness and actions.

**Reference implementation (built):** `app/panel.js`, served by the Sovereign
at `GET /embed/v1/panel.js`. Self-contained vanilla JS — no framework, no dependencies,
prefixed styles. The host must supply the ratified version, exact HTTPS origin,
token/context callbacks, and human-contact callback; other action adapters are
optional:

```html
<script src="https://sovereign.example.com/embed/v1/panel.js"></script>
<script>
  const panel = CosmaNeuraPanel.mount({
    sovereignUrl: "https://sovereign.example.com",
    embedVersion: "1.0",
    getAuthToken: async () => host.getFreshPlatformContextToken(),
    getContext: async () => ({
      clientId: openRecord.threadKey,
      thread_ref: openRecord.assistantThreadRef,
      context_snapshot_ref: openRecord.assistantSnapshotRef,
      digest_ref: openRecord.journeyDigestRef,
      terminology: settings.terminology,
      care_model: openRecord.careModel,
    }),
    onInsert: (text) => note.insert(text),      // optional action hooks
    onSave:   (text) => visits.savePrep(text),
    onHumanContact: action => host.openHumanContactPath(action.action_id),
    loadHistory: clientId => platform.loadGovernedAssistantHistory(clientId),
    onHistoryUpdated: (clientId, history) => ephemeralUi.replace(clientId, history),
  });

  // REQUIRED whenever the open record changes: swaps the visible conversation
  // with the record. Without it the previous patient's thread stays on screen
  // under the new patient's chart — wrong-patient display. The panel also
  // rebuilds defensively if it sees a new clientId at send time.
  onRecordChange(async id => {
    host.invalidateCachedMountToken();
    await panel.setRecord(id);
  });
</script>
```

The component implements the full action contract below (badges, emergency
card, trust line, verified-calculations box, Copy/Download, Insert/Save
hooks), validates the closed governed-history shape, and keys thread continuity
to `clientId`. The panel **never scrapes the page** and sends no raw history,
note, profile, lab, patient, or digest content in the initial activation.

The panel sends the opaque mount token as the sole bearer on `/welcome` and
`/chat`, requires and verifies embed version `1.0`, and sends one fresh random
`client_turn_id` identically in the chat body and header. It never retries a
chat automatically. After an ambiguous transport or retryable service failure,
it preserves the exact serialized body, UUID, record generation, and draft,
reacquires a fresh bearer/context pair, and offers an explicit user retry.
Canonical conversation durability belongs to
the platform Agent API, not browser history callbacks.

### The multi-session context problem (and its answer)

A panel can only send what the host gives it, and browser replay of prior notes
would expand both the PHI surface and request ambiguity. The governed answer is
the platform snapshot/digest boundary:

1. **Context snapshots.** The host sends an opaque `context_snapshot_ref`;
   authorized server-to-server retrieval decides the minimum necessary content.
2. **The journey digest.** In platform mode the host uses the ratified platform
   digest operation at note-write time and passes only its opaque reference to
   governed consumers. Sovereign's legacy `POST /digest` is unavailable when
   the Agent API boundary is enabled so it cannot create an orphan lineage or
   unpersisted model output. The legacy pinned build may retain the local route.

**Initial provider context contract** — only opaque platform-owned references and
nonclinical presentation hints are sent. Every raw migration bridge is rejected:

```json
{
  "client_turn_id": "<fresh random UUIDv4>", // identical to the required header
  "message": "<what the clinician typed>",
  "metadata": {
    "clientId": "opaque-host-thread-key",
    "context_snapshot_ref": "snapshot_opaque",
    "thread_ref": "thread_opaque",
    "digest_ref": "digest_opaque",
    "care_model": "internal family systems",  // from the client's profile
    "terminology": "client/therapist",        // the account-level setting
    "output_format": "chat|note|document"     // the panel's output selector
  }
}
```

The user experience this buys: Scott opens a client's record, clicks the rail,
and types "help me prep the next session" — **no pasting, no uploading, no
re-explaining who the client is.** That single difference is the product.

**Action contract** — the panel first switches on required
`presentation.mode` and honors every named suppression. It never infers a
presentation mode from `intent`. Only then may it expose unsuppressed content:

| Response signal | Panel affordance |
|---|---|
| `structured.feature == "visit_prep"` | **Insert into note** / **Save prep** buttons on the bubble |
| `intent == "ethics_consult"` | Render ERD/PubMed citations as a reference block; **Copy consult prep** |
| `structured.feature == "plan_workbench"` | Levers analysis with citations; **never** auto-merged into the SOAP note |
| `structured.computed` (non-empty) | "✓ Verified calculations — computed, not generated" box (BMI/BMR/macros from code, the anti-wrong-calories guarantee) |
| `structured.output_format == "note"` | **Copy** button — clean text for the Athena paste |
| `structured.output_format == "document"` | **Copy** + **Download** — the Word→PDF replacement |
| `structured.matches` | Provider cards with booking links (as Surface A) |
| `presentation.mode == "emergency"` | Red card + the panel stays open |
| `presentation.mode == "sensitive_disclosure"` | Humane answer plus only the supplied human-contact action; all seven ordinary surfaces suppressed |

**State:** the Sovereign remains stateless. The platform owns canonical
conversation persistence and authority-bound record context. The panel owns
only ephemeral display state and optional UI caching.

## 4. Shared API surface (what both UIs consume)

| Endpoint | Purpose |
|---|---|
| `GET /welcome` | Requires the sole opaque mount bearer and `X-Cosmaneura-Embed-Version: 1.0`; Sovereign resolves the mount before returning filtered paths. Query `persona` is ignored for authority. |
| `POST /chat` | Also requires the same canonical UUIDv4 in the body and `X-Cosmaneura-Client-Turn-ID`; the response echoes both the UUID and embed version. |

**Activation blocker:** the ratified Agent API mount resolver currently opens a
pending interaction lineage on every resolution, while `/welcome` has no turn
phases to close. Before platform activation, governance must ratify either a
bootstrap/no-lineage resolution purpose or the treatment of these zero-phase,
zero-audit, zero-usage bootstrap lineages. Sovereign does not invent that wire
extension locally.

**Latency note / next step:** responses are currently single-shot. When lanes
go live on real models, add SSE streaming to `/chat` so the answer types in —
perceived speed is a UX feature; budget it into Phase 1 gateway work.

## 5. Built in this branch vs. next

**Built:** protected mount-resolved `GET /welcome`, the upgraded
standalone chat page (welcome-driven bootstrap, persona-aware chips, emergency
card treatment, doctrinal trust line, structured cards), the versioned panel,
and server-to-platform canonical turn persistence.

**Next:** complete the provider-host mount-token adapter and live lineage smoke;
then public activation, SSE streaming, tenant theming, and any separately
ratified third-party embed.
