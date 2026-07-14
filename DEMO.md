# Live demo — doctor-matching chat

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
