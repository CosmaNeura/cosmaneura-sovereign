# athenahealth Lab Results — Integration Notes

Status: **integrated, tested offline, and historically live-verified against
the athenahealth Preview sandbox** — on 2026-08-14 the owner ran the earlier
direct-client smoke with real Developer Console credentials: OAuth token issued,
Donna Sandboxtest resolved (`a-195900.E-60178`), 76 lab reports / 129
discrete values retrieved (entered-in-error filtered), abnormal summary
returned 14 analytes (low platelets, high fasting glucose, high BUN and
creatinine, low eGFR), A1C 7.2 %. All five adapter stages passed on the first
run. This historical direct smoke does not prove the provider-host record-grant
boundary; that end-to-end smoke remains a deployment gate.

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

The only callable governed operation is `labs.read`. Patient-name search is
not part of that contract and is refused at every tier. Trend and abnormal
summaries are deterministic projections of the one bounded read result, not
additional EHR calls.

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

## 3. Authorization — signed open-record grant

The browser never chooses an Athena record and a raw `record_ref` is never
authority. The provider host performs the boundary check that Sovereign cannot:

1. The platform authenticates the Clerk session and re-resolves the exact
   doctor-owned appointment and patient for the open Notes view.
2. It requires an active synthetic-only designation and an active mapping to
   athenahealth Preview practice `195900`, under database locks.
3. It issues a 60-second Fernet grant with a closed claim set bound to the
   verified Clerk subject, appointment, patient, sandbox tier, Preview practice,
   and vendor record. The vendor identifier exists only inside ciphertext in
   the browser.
4. Sovereign authenticates the grant with its provider-only Key Vault secret,
   requires `actor_ref` to equal the verified JWT `sub`, and derives the vendor
   record server-side. Only then can it construct the fixed `LabsReadRequest`.

Tampered, expired, wrong-actor, wrong-practice, non-synthetic, production-tier,
extra/missing-claim, and raw/name-like values all fail before the lab source.
Name search is forbidden unconditionally. Revocation prevents new grants; an
already issued synthetic Preview grant has at most the documented 60-second
lifetime.

The pre/post audit contains actor, tenant, turn, purpose, category, adapter,
bounded window, and a record digest, never values or the vendor identifier.
For this pinned pilot it is operational evidence only; V7/V8 centralized
durability remains explicitly deferred under the accepted build plan.

## 4. Configuration

```bash
FEATURE_ATHENA_LABS=true          # whole capability, off by default
ATHENA_CLIENT_ID=…                # from Key Vault / 1Password, never committed
ATHENA_CLIENT_SECRET=…
SOVEREIGN_RECORD_GRANT_SECRET=…   # canonical Fernet key; provider vault only
ATHENA_ENV=preview                # preview | production
ATHENA_PRACTICE_ID=195900
ATHENA_DEPARTMENT_ID=1
LABS_ALLOW_PATIENT_SEARCH=false   # required; name search is always forbidden
```

With `MOCK_MODE=true` (the default) the lane returns deterministic stub labs
that reproduce the documented sandbox quirks — a missing LOINC, a
`status: unknown` report, and an `entered-in-error` report that must never
surface — so the whole lane is exercisable with zero credentials.

## 5. Verified

- `tests/test_labs.py` plus `tests/test_record_grant.py`: the bundle's tool
  assertions preserved
  through integration (voided-report filtering, abnormal-only filter, trend by
  LOINC *and* by name substring, latest-value grouping), plus flag-off,
  credential-less, production-search-refused, BAA-attestation, routing, and
  audit gates, exact grant claims, tamper/expiry/actor failures, secret
  redaction, public-secret absence, and raw-record refusal.
- The direct Preview adapter smoke remains useful for credential/network
  validation. The release smoke must instead obtain a fresh platform grant
  for a designated synthetic chart and exercise `/chat` through the provider
  panel.

## 6. What's left

| Item | Owner |
|---|---|
| Provision the Clerk JWT template and approved provider metadata; prove correct-role and wrong-role tokens | Operator |
| Seed the same generated Fernet key into the platform provider vault and provider Sovereign vault; prove it is absent from public | Operator |
| Designate the demo platform patient as synthetic-only and map its opaque Preview `195900` record using the guarded CLI | Operator |
| Run the end-to-end provider panel smoke and the paired deployment rollback rehearsal | Operator + developer |
| Rewrite the vendored client on httpx and retire the thread dispatch | Developer, post-pilot |
| Shared token cache if the service scales past one worker (tokens are per-process, ~60 min) | Developer, post-pilot |
| Real-data / production EHR authorization | Explicitly out of scope for this pilot |
| V7/V8 centralized durable audit | Deferred/report-only per build prompt v2 |

Rate limits observed in Preview: 15 req/s, 50k/day. Tokens last ~60 minutes
and are cached in-process.
