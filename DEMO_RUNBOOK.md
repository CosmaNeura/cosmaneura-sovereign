# Demo runbook — CosmaNeura Sovereign

Quick, day-of guide for the live demo. Deeper detail in [`DEMO.md`](./DEMO.md).

## 1. Before the demo (2 min prep)

```bash
cd cosmaneura-sovereign
az account show      # confirm you're logged in (if not: az login)
./tools/run_demo_web.sh
```

You should see:
```
→ loaded .env.demo.local
→ Magisterium doctrinal gate: enabled
→ starting… opening http://localhost:8000/demo
```
The browser opens to **http://localhost:8000/demo**. Do a **dry run** (one question
of each type) before the boss arrives so everything is warm.

⚠️ **If something fails:** kill stale servers and relaunch —
`pkill -f uvicorn` then `./tools/run_demo_web.sh`. The fallback error
("service not enabled") almost always means an old server without the keys.

## 2. The script (5 questions, ~3-4 min) — all tested

Use the UI **chips** (already loaded) or type:

| # | Ask | What it shows |
|---|---|---|
| 1 | *Find a faithful Catholic pediatrician in California* | Real doctors, cards with a real **Book appointment** link to mycatholicdoctor |
| 2 | *I have a skin problem, find me a Catholic dermatologist in New Hampshire* | Understands "skin" → dermatology: **it reasons about specialty, not random** |
| 3 | *What recruiting clinical trials exist for pancreatic cancer?* | **Real, verifiable** trials from ClinicalTrials.gov (frontier medicine) |
| 4 | *I was just diagnosed with type 2 diabetes (A1C 7.2)* | Plain-language explanation + questions for the doctor (never diagnoses) |
| 5 | *Is IVF permitted by the Catholic Church?* | **The differentiator:** Magisterium answers with citations (Dignitas Personae) |

**Suggested order:** start tangible (1–2, finding doctors with booking buttons),
step up to capabilities (3–4), and **close with #5** (faith/doctrine — the "wow").

## 3. What to avoid
- Specialties with no provider in that state (e.g. the only dermatologist is in
  AL/ID/KS/MO/NH).
- Emergencies ("chest pain") → short-circuits to a 911 message (correct, but off-topic).
- Don't fire 90+ questions in a row (Magisterium free tier is 90/week — plenty for a demo).

## 4. Closing line
> *"One assistant that finds faithful Catholic doctors, explains frontier medicine
> with real trials, helps you understand your health — and everything passes through
> the doctrinal authority of the Church. Medicine and faith in one conversation."*

## 5. If the boss asks "is this real or made up?"
- **Doctors:** click **Book appointment →** → opens the real page on mycatholicdoctor.com.
- **Trials:** click **View trial →** → opens the official NIH registry with the same NCT ID.
- **Doctrine:** the citations are to real magisterial documents.

## Context the boss may want
- **All three product agents work** (referral, concierge, engagement) + the doctrinal gate.
- Runs on **Azure OpenAI** (existing credits) and **HIPAA-grade clinical de-identification**
  (Azure Health Data Services) for the provider/PHI mode.
- **Ready to deploy** (Terraform is written) — this local demo is the real product.

## Under the hood (if asked)
- **Referral** routes by state → language → specialty, prioritizing who's actually
  available in the patient's state.
- **Pure-logistics** questions skip the doctrinal gate (fast); **moral-weight** ones
  (IVF, contraception, mental health) pass through Magisterium.
- **Provider mode** de-identifies PHI (names, locations, dates) before anything
  reaches a non-BAA service. Use synthetic patient data in the demo regardless.
