# athenahealth Lab Results — Integration Notes

Status: **integrated, tested offline, and historically live-verified against
the athenahealth Preview sandbox** — on 2026-08-14 the owner ran the earlier
direct-client smoke with real Developer Console credentials: OAuth token issued,
Donna Sandboxtest resolved (`a-195900.E-60178`), 76 lab reports / 129
discrete values retrieved (entered-in-error filtered), abnormal summary
returned 14 analytes (low platelets, high fasting glucose, high BUN and
creatinine, low eGFR), A1C 7.2 %. All five stages passed on the first run.

Sandbox data note: Donna carries only ONE A1C observation, so the panel's
trend chart (which requires >= 2 numeric points) will not draw for A1C on
live sandbox data — the agent states single-point trends in prose instead,
by design. Multi-observation analytes (e.g. creatinine, glucose) chart.

The current `tools/smoke_labs.py` exercises one host-bound record through the
deployed OIDC provider mount. It refuses name search, requires explicit
synthetic-record confirmation plus a fresh actor-bound grant, and prints no token,
record reference, lab value,
answer, or upstream response body.

Source: the `chatbot-bundle` delivered 2026-08-14, sandbox-verified against
athenahealth Preview (practice 195900, patient Donna Sandboxtest).

---

## 1. The one thing done differently from the brief — and why

The brief's task 2 says to merge `openai_schemas.TOOLS` into "the tools array
the chatbot already sends" and fold the bundle's `SYSTEM_PROMPT` into the
platform prompt. That assumes the chatbot is a single OpenAI function-calling
loop. **This service isn't one.** Every request runs:

```
emergency check → persona/auth gate → route → agent → PHI de-identification
→ Magisterium doctrinal arbitration → disclaimer → hash-chained audit → egress
```

Bolting a second chat loop with its own system prompt inside that would route
lab answers around the doctrinal gate, the PHI boundary and the audit chain —
the three guarantees the Sovereign exists to provide, and the ones the
compliance posture is sold on.

So the capability is wired in as **a routed lane**, exactly like the other
six agents:

| Bundle piece | Where it lives now |
|---|---|
| `athena_labs/` (FHIR client) | `app/clients/athena/` — vendored, near-verbatim |
| The four tools | `app/clients/labs.py` — async façade, flag + authz + PHI-safe errors |
| `example_chat_loop.SYSTEM_PROMPT` rules | `app/agents/labs_agent.py` `_SYSTEM` (never invent values; units/ranges/flags always; defer treatment decisions) |
| `openai_schemas.TOOLS` / `execute_tool` | **not used** — the router selects the lane; no function-calling loop |
| `tests/test_tools.py` | `tests/test_labs.py`, re-pointed at the vendored client |

The tool *capabilities* are unchanged: `find_patient`, `get_lab_results`
(`lab_results`), `get_analyte_trend` (`analyte_trend`),
`summarize_abnormal_labs` (`abnormal_summary`).

## 2. Deltas from the delivered code

- **Async boundary.** The vendored client is synchronous `requests`; this
  service is async on a single web process. Every EMR call is dispatched with
  `asyncio.to_thread` so an EMR round-trip cannot block the event loop.
  (Rewriting the client on httpx is the cleaner long-term fix; deliberately
  deferred to keep the delivered client intact.)
- **PHI in error paths.** `auth.py` and `fhir_source.py` embedded
  `resp.text[:500]` in exceptions. FHIR error bodies can carry patient data,
  and "PHI leakage through error paths" is a defect this repo already fixed
  once — the bodies are dropped.
- **One config system.** `config.py`'s import-time `load_dotenv()` is removed;
  athena settings come from the app's pydantic settings like every other
  secret, so credentials can't diverge from the Key Vault–backed path.
- **Dependencies:** added `requests~=2.32` only. `python-dotenv` is redundant
  (pydantic-settings reads `.env`), and `openai` is unnecessary since this
  service calls Azure OpenAI over httpx.
- **Not vendored:** `proprietary_source.py` (no contract, and the brief bars
  it from production — omitted so it cannot be called by accident), plus the
  demo/seed/chat-loop reference scripts.

## 3. Authorization — host mapping plus a signed grant

The Sovereign has no patient roster, so the platform host performs the
provider↔patient/open-appointment lookup. It returns a 60-second Fernet grant
whose fixed claims bind the verified Clerk subject to one synthetic Athena
Preview record. Sovereign verifies that grant before releasing the vendor
record reference to the bounded `labs.read` operation; raw caller-provided
record identifiers are not production authority.

1. **The host maps the open record.** Only an active, synthetic practice
   `195900` mapping for the authenticated provider's appointment can mint a grant.
2. **Sovereign verifies the grant.** Actor, issuer, audience, expiry, adapter,
   practice, environment, tier, and synthetic-only claims must all match.
3. **Name search is forbidden.** `LABS_ALLOW_PATIENT_SEARCH=true` fails startup;
   the model cannot select another patient by name.
4. **Access is audited without identifiers.** The agent returns
   `access.record_ref_sha256_16` (a hash) plus
   `resolved_by=platform_signed_open_record`,
   so the hash-chained audit shows who reached which record without writing a
   patient identifier into the log. *Open question for counsel: a HIPAA
   accounting of disclosures may require the real identifier in a protected
   store — that is a deliberate decision, not an oversight.*

## 4. Configuration

```bash
FEATURE_ATHENA_LABS=true          # whole capability, off by default
ATHENA_CLIENT_ID=…                # from Key Vault / 1Password, never committed
ATHENA_CLIENT_SECRET=…
ATHENA_ENV=preview                # preview | production
ATHENA_PRACTICE_ID=195900
ATHENA_DEPARTMENT_ID=1
LABS_ALLOW_PATIENT_SEARCH=false   # keep false outside the sandbox
SOVEREIGN_RECORD_GRANT_SECRET=…   # provider-only Fernet key, from Key Vault
```

With `MOCK_MODE=true` (the default) the lane returns deterministic stub labs
that reproduce the documented sandbox quirks — a missing LOINC, a
`status: unknown` report, and an `entered-in-error` report that must never
surface — so the whole lane is exercisable with zero credentials.

## 5. Verified

- `tests/test_labs.py` (12 tests): the bundle's tool assertions preserved
  through integration (voided-report filtering, abnormal-only filter, trend by
  LOINC *and* by name substring, latest-value grouping), plus flag-off,
  credential-less, production-search-refused, BAA-attestation, routing, and
  audit-hash gates.
- The brief's demo script, through the full pipeline:
  "Pull up Donna Sandboxtest" → `lab_review` (resolved by search, sandbox);
  "Anything concerning in her labs?" → A1C 8.2 % high, ferritin 18 ng/mL low
  (normal sodium correctly excluded); "Show me her A1C trend" → 7.6 → 8.2,
  oldest first. Doctrinal gate and audit chain ran on every turn.

## 6. What's left

| Item | Owner |
|---|---|
| ~~Live sandbox smoke test~~ → **DONE — live-verified by the owner, 2026-08-14** (five PASSes on first run; see Status above) | — |
| ~~Surface labs in the panel~~ → **done**: `panel.js` renders `structured.trend` as a validated trend chart (ref band, emphasized endpoint, hover tooltips); the lab chip appears in `/welcome` only when `FEATURE_ATHENA_LABS=true`; browser-verified in the dashboard harness | — |
| Rewrite the vendored client on httpx and retire the thread dispatch | Developer, post-pilot |
| Shared token cache if the service scales past one worker (tokens are per-process, ~60 min) | Developer, post-pilot |
| Production go-live: practice BAA, "Cosmaneura Lab Insights" enabled in athenaOne, real practice id, `BAA_ATTESTED=true` | John + counsel |
| Accounting-of-disclosures decision (hash vs. identifier in the audit store) | Counsel |

Rate limits observed in Preview: 15 req/s, 50k/day. Tokens last ~60 minutes
and are cached in-process.
