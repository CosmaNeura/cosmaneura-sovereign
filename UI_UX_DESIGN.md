# UI/UX Design — Two Surfaces, One Brain

**Status:** design + first implementation. The standalone chat surface and the
shared `GET /welcome` bootstrap are built in this branch; the Cosmas dashboard
panel is specified here for the Cosmas frontend team (separate codebase).

The chatbot ships on two surfaces. Both are thin clients over the **same two
endpoints** — `GET /welcome` (bootstrap) and `POST /chat` (everything else).
No client-side intelligence, no divergence: the Sovereign is the brain, the
surfaces are skin.

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
| **Context-blind** — often doesn't reliably know what you're looking at; you re-explain | Surface B injects the open record automatically: the note/session on screen flows into `metadata` on every message. The user never pastes anything (that's the exact workflow we're replacing). |
| **Answers stranded in the sidebar** — you copy-paste results back into your document | Responses carry machine-readable payloads (`structured`, `citations`); the panel offers one-click actions: *Insert into note*, *Save prep to visit*. The answer lands where the work is. |
| **Popup amnesia** — every invocation starts from zero | One continuous thread **per client/patient**, keyed by the record the clinician has open. Thread history lives in Cosmas's DB (the Sovereign stays stateless); Cosmas replays it via `history[]`. |
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
- **Persistent footer disclaimer** (per INTERACTION_DESIGN Decision 3).

**Auth & payment gating (deployment, not new code):** `AUTH_MODE=oidc` against
Clerk; the paid entitlement (e.g. the $59/mo tier) is a Clerk claim; the page
exchanges the Clerk session for the bearer token it sends to `/chat`.
Unauthenticated visitors see the marketing/paywall page, not the chat.
Providers additionally carry the clinician role claim → provider persona,
verified server-side (already enforced by the principal override).

**Later (Phase 2/3, unchanged):** per-tenant theming of this same page, and
the sandboxed-iframe widget for **third-party** partner sites (MyCatholicDoctor).
The iframe is for sites you don't own; Cosmas never needs it.

## 3. Surface B — native panel inside Cosmas

**What it is:** a right-side rail in app.cosmaneura.com, collapsed to a single
button, expanding to the same chat UI as Surface A (same component styling,
same rendering rules) plus context awareness and actions.

**Reference implementation (built):** `app/panel.js`, served by the Sovereign
at `GET /panel.js`. Self-contained vanilla JS — no framework, no dependencies,
prefixed styles. The host mounts it with two required callbacks; everything
else is optional:

```html
<script src="https://sovereign.example.com/panel.js"></script>
<script>
  CosmaNeuraPanel.mount({
    sovereignUrl: "https://sovereign.example.com",
    getAuthToken: async () => currentUserJwt,   // the auth bridge
    getContext:   async () => ({                // whatever record is on screen
      clientId, journey_digest, prior_sessions: latestNote,
      terminology, care_model /* + labs, metrics, templates per view */ }),
    onInsert: (text) => note.insert(text),      // optional action hooks
    onSave:   (text) => visits.savePrep(text),
    loadHistory, saveHistory,                   // optional thread persistence
  });
</script>
```

The component implements the full action contract below (badges, emergency
card, trust line, verified-calculations box, Copy/Download, Insert/Save
hooks), budgets context payloads to stay inside the Sovereign's body cap
(newest-first truncation), and keys thread continuity to `clientId`. The
panel **never scrapes the page** — the host owns what context flows, which is
both the privacy posture and the reason it can't go stale when the dashboard
UI changes.

### The multi-session context problem (and its answer)

A panel can only send what the host gives it, and "my last 2-3 visits" as raw
transcripts would blow request budgets. Two-part answer, both built:

1. **Notes are already compressed sessions.** Cosmas generates a note per
   visit — so "last 3 visits" context is 3 *notes* (a few KB), not 3
   transcripts. The panel's `prior_sessions` accepts either.
2. **The journey digest (`POST /digest`).** For long journeys, the host calls
   `/digest` **once per session at note-write time** — off the chat hot path —
   merging the new session into a compact rolling digest (≤300 words: themes,
   trajectory, commitments, open threads) stored per client. Chat requests
   then carry one small `metadata.journey_digest` field, and context cost
   stays constant whether the client has 3 sessions or 300. Same auth,
   persona gate, BAA tripwire, and audit chain as `/chat`.

**Context contract** — Cosmas attaches this to every `/chat` call, silently:

```json
{
  "persona": "provider",                      // from the clinician's JWT, verified server-side
  "message": "<what the clinician typed>",
  "history": [ ...this client's thread... ],
  "metadata": {
    "prior_sessions": "<transcript/notes of the record on screen>",
    "care_model": "internal family systems",  // from the client's profile
    "terminology": "client/therapist",        // the account-level setting
    "case_context": "<case facts if the view is a case/chart>",
    "output_format": "chat|note|document",    // the panel's output selector
    "patient_profile": "self-directed",       // Cosmas's engagement tag, if any
    "intake_summary": "<pre-visit intake bot summary, if any>",
    "labs": "<lab values/trends when the view is a lab record>",
    "patient_metrics": {"sex": "female", "age": 52,
                        "weight_lb": 200, "height_in": 65},
    "calorie_target": "1500-1600",            // clinician-set, drives verified calc
    "note_template": "<the provider's own note type, incl. audit sections>"
  }
}
```

The user experience this buys: Scott opens a client's record, clicks the rail,
and types "help me prep the next session" — **no pasting, no uploading, no
re-explaining who the client is.** That single difference is the product.

**Action contract** — what the panel does with responses:

| Response signal | Panel affordance |
|---|---|
| `structured.feature == "visit_prep"` | **Insert into note** / **Save prep** buttons on the bubble |
| `intent == "ethics_consult"` | Render ERD/PubMed citations as a reference block; **Copy consult prep** |
| `structured.feature == "plan_workbench"` | Levers analysis with citations; **never** auto-merged into the SOAP note |
| `structured.computed` (non-empty) | "✓ Verified calculations — computed, not generated" box (BMI/BMR/macros from code, the anti-wrong-calories guarantee) |
| `structured.output_format == "note"` | **Copy** button — clean text for the Athena paste |
| `structured.output_format == "document"` | **Copy** + **Download** — the Word→PDF replacement |
| `structured.matches` | Provider cards with booking links (as Surface A) |
| `intent == "emergency"` | Red card + the panel stays open |

**State:** the Sovereign remains stateless. Cosmas owns thread persistence
(per-client), the terminology setting (the client/therapist toggle users
asked for), and which record is on screen. This split means the backend
scales to partner tenants later without carrying anyone's UI state.

## 4. Shared API surface (what both UIs consume)

| Endpoint | Purpose |
|---|---|
| `GET /welcome?persona=patient\|provider` | Greeting, guided paths (label → prefilled message → expected intent), disclaimer, emergency note, partner name. Built in this branch. |
| `POST /chat` | Everything else. The UI reads: `answer` (markdown-ish), `intent` (badge + lane-specific affordances), `citations`, `structured`, `doctrinal_review` (trust line), `safety_notice`, `trace_id` (support reference). |

**Latency note / next step:** responses are currently single-shot. When lanes
go live on real models, add SSE streaming to `/chat` so the answer types in —
perceived speed is a UX feature; budget it into Phase 1 gateway work.

## 5. Built in this branch vs. next

**Built:** `GET /welcome` (per-persona greeting + paths, tested), the upgraded
standalone chat page (welcome-driven bootstrap, persona-aware chips, emergency
card treatment, doctrinal trust line, structured cards), config `PARTNER_NAME`.

**Next:** Clerk wiring + paywall page for chat.cosmaneura.com (deployment);
the Cosmas panel component (Cosmas repo, per §3 contracts); SSE streaming;
Phase 2 tenant theming; Phase 3 partner iframe (third parties only).
