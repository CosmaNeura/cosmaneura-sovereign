"""
athenahealth sandbox smoke test — the "is the lab lane live?" one-command check.

    PYTHONPATH=. .venv/bin/python tools/smoke_labs.py

Needs ONLY two values in .env (from the athenahealth Developer Console app,
stored in your password manager — never in chat/email):

    ATHENA_CLIENT_ID=...
    ATHENA_CLIENT_SECRET=...

Everything else is forced by this script: it builds its own settings with the
lab feature ON and mock mode OFF, so you do NOT need Azure OpenAI keys, the
Magisterium key, or any other configuration to run it. It never calls an LLM —
it exercises the EMR client only, against athenahealth's shared Preview
sandbox (practice 195900, synthetic patient Donna Sandboxtest).

Five stages, each PASS/FAIL with a plain-language hint:
  1. Credentials present
  2. OAuth token from athenahealth (proves the client id/secret + network)
  3. Patient search finds Donna Sandboxtest
  4. Her lab reports come back (voided reports filtered)
  5. Abnormal summary + A1C trend behave on real sandbox data

All data touched is synthetic. Output is safe to copy-paste for help.
"""
from __future__ import annotations

import asyncio
import os
import sys

from app.clients.labs import LabsClient
from app.config import Settings

DONNA = "Donna Sandboxtest"


def _hint(err: str) -> str:
    e = err.lower()
    if "401" in e or "invalid_client" in e or "token request failed" in e:
        return ("Hint: athenahealth rejected the credentials. Re-copy "
                "ATHENA_CLIENT_ID / ATHENA_CLIENT_SECRET from the Developer "
                "Console app (watch for trailing spaces).")
    if "403" in e:
        return ("Hint: authenticated but not authorized — the Developer Console "
                "app may be missing the FHIR read scopes (Patient, "
                "DiagnosticReport, Observation, Binary).")
    if "429" in e:
        return "Hint: Preview rate limit (15 req/s, 50k/day). Wait a minute and rerun."
    if any(k in e for k in ("connection", "timed out", "timeout", "resolve",
                            "getaddrinfo", "ssl")):
        return ("Hint: network problem reaching api.preview.platform."
                "athenahealth.com — check VPN/proxy/firewall on this machine.")
    return "Hint: unrecognized — send this exact output to the developer (it is synthetic-data safe)."


def _fail(stage: str, exc: Exception) -> None:
    print(f"[FAIL] {stage}: {exc}")
    print("       " + _hint(str(exc)))


async def main() -> int:
    # Self-contained settings: lab feature ON, mock OFF, athena creds from env.
    # Nothing else in .env is read or required.
    s = Settings(mock_mode=False, feature_athena_labs=True)
    print(f"env={s.athena_env}  practice={s.athena_practice_id}")

    # 1) credentials present
    if not (s.athena_client_id and s.athena_client_secret):
        print("[FAIL] Credentials: ATHENA_CLIENT_ID / ATHENA_CLIENT_SECRET are "
              "not set. Put both in the .env file in this folder and rerun.")
        return 1
    print("[PASS] Credentials present")
    if s.athena_env == "production":
        print("[STOP] ATHENA_ENV=production — this smoke test is sandbox-only. "
              "Set ATHENA_ENV=preview and rerun.")
        return 1

    labs = LabsClient(s)

    # 2) token — force it early so auth fails loudly and alone.
    try:
        await asyncio.to_thread(labs._source().tokens.get_token)
        print("[PASS] OAuth token issued by athenahealth")
    except Exception as exc:  # noqa: BLE001 — every failure kind gets a hint
        _fail("OAuth token", exc)
        return 1

    # 3) patient search
    try:
        found = await labs.find_patient("Sandboxtest", given="Donna")
        match = next((m for m in found["matches"]
                      if "sandboxtest" in (m.get("name") or "").lower()), None)
        if not match:
            print(f"[FAIL] Patient search: 0 Sandboxtest matches "
                  f"(got {found['count']} results).")
            print("       Hint: check ATHENA_PRACTICE_ID (sandbox is 195900).")
            return 1
        pid = match["id"]
        print(f"[PASS] Found {match.get('name')} ({pid})")
    except Exception as exc:  # noqa: BLE001
        _fail("Patient search", exc)
        return 1

    # 4) lab reports
    try:
        res = await labs.lab_results(pid)
        n_reports = res["report_count"]
        n_values = sum(len(r["values"]) for r in res["reports"])
        if n_reports == 0:
            print("[FAIL] Lab reports: none returned for the sandbox patient.")
            print("       Hint: sandbox data shifts occasionally — try patient "
                  "search without a first name and pick another Sandboxtest.")
            return 1
        print(f"[PASS] {n_reports} lab reports, {n_values} discrete values "
              "(entered-in-error reports filtered)")
    except Exception as exc:  # noqa: BLE001
        _fail("Lab reports", exc)
        return 1

    # 5) abnormal summary + trend
    try:
        summary = await labs.abnormal_summary(pid)
        trend = await labs.analyte_trend(pid, "a1c")
        flags = [f"{row['analyte']}={row['latest']['value']}{row['latest']['unit'] or ''}"
                 f" ({row['latest']['flag']})" for row in summary["summary"][:5]]
        print(f"[PASS] Abnormal summary: {summary['abnormal_analytes']} analytes"
              + (" — " + "; ".join(flags) if flags else ""))
        if trend["point_count"]:
            pts = [f"{(p['observed_at'] or '')[:10]}: {p['value']}{p['unit'] or ''}"
                   for p in trend["points"]]
            print(f"[PASS] A1C trend: {trend['point_count']} points — " + " → ".join(pts))
        else:
            print("[WARN] A1C trend: 0 points (this patient may lack A1C data; "
                  "not a failure of the client).")
    except Exception as exc:  # noqa: BLE001
        _fail("Summary/trend", exc)
        return 1

    print("\nAll stages passed — the lab lane is live against the sandbox. "
          "Next: turn it on in the chatbot with FEATURE_ATHENA_LABS=true.")
    return 0


if __name__ == "__main__":
    # Load .env the same way the app does (pydantic-settings), no extra deps.
    sys.exit(asyncio.run(main()))
