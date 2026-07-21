# UI/UX Design — Two Surfaces, One Brain

**Status:** design + first implementation. The standalone chat surface and the
shared `GET /welcome` bootstrap are built in this branch; the Cosmos dashboard
panel is specified here for the Cosmos frontend team (separate codebase).

The chatbot ships on two surfaces. Both are thin clients over the **same two
endpoints** — `GET /welcome` (bootstrap) and `POST /chat` (everything else).
No client-side intelligence, no divergence: the Sovereign is the brain, the
surfaces are skin.

```
Surface A: chat.cosmaneura.com          Surface B: app.cosmaneura.com
(standalone, paying users)              (native panel inside Cosmos)
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
| **Popup amnesia** — every invocation starts from zero | One continuous thread **per client/patient**, keyed by the record the clinician has open. Thread history lives in Cosmos's DB (the Sovereign stays stateless); Cosmos replays it via `history[]`. |
| **Mode pickers and ribbon buttons** — the user must know which feature to summon | Invisible routing. The clinician just types; the Sovereign's router picks the lane (visit prep / ethics consult / referral / explain). Chips are suggestions, never modes. |
| **Nagging** — autopop-ups, upsells, "try asking…" toasts | Quiet by default. A collapsed rail with one button. It speaks first **only** when opened, never uninvited. |
| **Opaque answers** — where did this come from? | A visible trust surface: source citations, the doctrinal-review line ("✓ Reviewed for conformity with Catholic teaching"), and intent badges. Clinicians extend trust to what shows its work. |

**Not a plugin, by design.** Browser extensions and Office add-ins are
workarounds for not owning the host application. You own both hosts. Surface A
is a page this repo already serves; Surface B is a first-party component in the
Cosmos frontend with server-side access to its own data. Nothing to install,
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
The iframe is for sites you don't own; Cosmos never needs it.

## 3. Surface B — native panel inside Cosmos

**What it is:** a right-side rail in app.cosmaneura.com, collapsed to a single
button, expanding to the same chat UI as Surface A (same component styling,
same rendering rules) plus context awareness and actions.

**Context contract** — Cosmos attaches this to every `/chat` call, silently:

```json
{
  "persona": "provider",                      // from the clinician's JWT, verified server-side
  "message": "<what the clinician typed>",
  "history": [ ...this client's thread... ],
  "metadata": {
    "prior_sessions": "<transcript/notes of the record on screen>",
    "care_model": "internal family systems",  // from the client's profile
    "terminology": "client/therapist",        // the account-level setting
    "case_context": "<case facts if the view is a case/chart>"
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
| `structured.matches` | Provider cards with booking links (as Surface A) |
| `intent == "emergency"` | Red card + the panel stays open |

**State:** the Sovereign remains stateless. Cosmos owns thread persistence
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
the Cosmos panel component (Cosmos repo, per §3 contracts); SSE streaming;
Phase 2 tenant theming; Phase 3 partner iframe (third parties only).
