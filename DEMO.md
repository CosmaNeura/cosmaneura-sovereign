# Demos

Two of them:

1. **Embedded panel demo** (below) — the provider experience: the Sovereign
   panel mounted inside a stand-in Cosmas dashboard. Zero credentials.
2. **Live doctor-matching chat** (further down) — the patient experience
   against real Azure OpenAI + the scraped directory.

---

## Embedded panel demo (no credentials, no setup)

```bash
make install          # once
make demo-dashboard   # then open http://localhost:8080/demo/dashboard
```

What it shows, and why each part matters:

| On screen | What it proves |
|---|---|
| A single **C** button, bottom-right (above the “Report a Bug” pill) | Not a plugin, not an iframe, quiet by default — it speaks only when opened, and `fabBottom` keeps it clear of controls the host already owns |
| **“▸ show context sent to assistant”** in the top strip | The host decides what flows (note, journey digest, care model, terminology, labs, metrics). The panel never scrapes the page |
| Ask “*help me prep my next session*” → **Insert into note** | Scott's daily workflow, native: no pasting into consumer ChatGPT, and the answer lands in the note |
| Switch to **M. Torres** in the notes list | Context *and* conversation thread switch with the record — one thread per patient |
| The note stays fully readable with the panel open | The panel reflows the host instead of covering it (`shiftContent`) — you can read the note you are inserting into |
| Ask about labs/levers → **✓ Verified calculations** | Sandy's trust fix: BMI, resting energy and macros are computed in Python (`app/agents/calculators.py`) and locked to the clinician's stated calorie range — the model may quote them but never invent them |
| Choose **Note text** in the format selector | Clean text for the EHR paste (Maura's Athena workflow); **Document** adds a download |
| Type anything urgent (e.g. chest pain) | Emergency short-circuit renders as a distinct red card, before any other processing |
| “✓ Reviewed for conformity with Catholic teaching” | The Magisterium arbitration gate, visible to the clinician |

All records are **synthetic**, and the Sovereign runs in `MOCK_MODE`, so the
assistant's prose is a visible stub (`[MOCK MODEL OUTPUT]`) — the *plumbing* is
real, the words are not. Point it at live models (see
`SETUP_AND_HANDOFF.md` §12) to get genuine output.

The harness lives at `app/demo_dashboard.html` and is served only when
`DEMO_UI_ENABLED=true`. It mounts the same `app/panel.js` the real dashboard
will use, so it doubles as the reference integration for the Cosmas developer.

---

## Live demo — doctor-matching chat

A simple in-browser chat to show the referral assistant working live (real Azure
OpenAI + the scraped MyCatholicDoctor directory).

## Run it (one command)

```bash
MAGISTERIUM_API_KEY='sk_...' ./tools/run_demo_web.sh
```

(You can also `export MAGISTERIUM_API_KEY=...` once, then just run
`./tools/run_demo_web.sh`.) Without the key, doctor-finding still works but
doctrinal questions are disabled.

Prereqs: `az login` (CosmaNeura subscription), `make install` (the `.venv`), and a
scraped data file (`.venv/bin/python tools/scrape_mycatholicdoctor.py` if missing).
The script fetches the Azure OpenAI key, starts the server, and opens
**http://localhost:8000/demo**.

## What it does
- Type a request → the Sovereign routes it, matches providers from the scraped
  directory, ranks them, and gpt-4.1 writes the recommendation.
- Matched clinicians show as cards with a **Book appointment** link.

## Suggested prompts

**Doctor-finding (logistics — always on):**
- *Find a faithful Catholic pediatrician in California*
- *I need a Catholic primary care doctor in Ohio who speaks Spanish*
- *I have a skin problem, find me a Catholic dermatologist in New Hampshire*
- *I'm looking for a Catholic family doctor in Florida*

**Doctrinal (the Catholic differentiator — needs the Magisterium key):**
- *Is IVF permitted by the Catholic Church?*
- *Is contraception morally acceptable according to the Church?*
- *What does the Church teach about end-of-life care?*

## Good to know (for the demo)
- **Doctor-finding** routes by state, language, and specialty (a "skin" request
  surfaces the dermatologist available in that state). Pure logistics skip the
  doctrinal gate, so they're fast.
- **Doctrinal questions** are answered directly by **Magisterium** with real
  citations (e.g. Humanae Vitae, Dignitas Personae) — this is the "faithful
  Catholic" differentiator. Requires `MAGISTERIUM_API_KEY`.
- **Mental-health / fertility referrals** pass through the doctrinal review gate,
  so the assistant may add a note in keeping with Catholic teaching before the
  provider list.
- This is a local demo on real data; it is **not** the hardened production build
  (no auth, no BAA attestation, no WORM audit — see `SETUP_AND_HANDOFF.md`).

## Stop it
`Ctrl-C` in the terminal running the script.
