"""
Live smoke test — the "did the flip to real models work?" one-command check.

Run AFTER filling .env with real credentials (MOCK_MODE=false,
LLM_PROVIDER=azure_openai, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY,
REASONING_MODEL / FAST_MODEL set to the Azure *deployment names*):

    PYTHONPATH=. python tools/smoke_live.py

Uses SYNTHETIC prompts only — safe to run before any compliance gate. Steps:
  1. Ping the fast deployment (router/classifier path).
  2. Ping the reasoning deployment (agent drafting path).
  3. If MAGISTERIUM_API_KEY is set: run one full pipeline scenario
     (referral routing + arbitration) end-to-end.

Each step prints PASS/FAIL with a plain-language hint for the usual causes.
"""
from __future__ import annotations

import asyncio
import sys

from app.clients.llm import LLMClient
from app.config import Persona, Settings
from app.schemas import ChatRequest


def _hint(err: str) -> str:
    e = err.lower()
    if "401" in e or "unauthorized" in e or "key" in e:
        return "Hint: the API key looks wrong. Re-copy it from the Azure resource's Keys page."
    if "404" in e or "not found" in e:
        return ("Hint: Azure can't find that deployment name. REASONING_MODEL / "
                "FAST_MODEL must match the deployment names in Foundry's "
                "'Models + endpoints' list EXACTLY (they are addresses, not model IDs).")
    if "timed out" in e or "connect" in e or "resolve" in e:
        return "Hint: AZURE_OPENAI_ENDPOINT looks wrong or unreachable. Copy it from the deployment page."
    if "quota" in e or "429" in e:
        return "Hint: rate/quota limit. Check the deployment's quota (TPM) in Foundry."
    return "Hint: read the message above; if unclear, send it to the developer verbatim (it is PHI-safe)."


async def main() -> int:
    s = Settings()
    print(f"provider={s.llm_provider}  mock_mode={s.mock_mode}")
    print(f"reasoning deployment: {s.reasoning_model}")
    print(f"fast deployment:      {s.fast_model}\n")
    if s.mock_mode:
        print("MOCK_MODE is still true — set MOCK_MODE=false in .env and rerun.")
        return 1

    # Catch the most common endpoint mix-up before making any network call:
    # the Foundry *project* endpoint (…/api/projects/…) is for Microsoft's
    # agent SDK, not the Azure OpenAI protocol this service speaks.
    ep = s.azure_openai_endpoint or ""
    if s.llm_provider == "azure_openai":
        if "/api/projects/" in ep:
            print("[FAIL] AZURE_OPENAI_ENDPOINT is the Foundry *project* endpoint "
                  "(it contains /api/projects/).")
            print("       Use the Azure OpenAI endpoint instead: in Foundry open "
                  "Models + endpoints → click a deployment → copy the Target URI —")
            print("       the part before /openai/ is the endpoint, e.g. "
                  "https://<resource>.openai.azure.com")
            return 1
        if not ep.startswith("https://"):
            print("[FAIL] AZURE_OPENAI_ENDPOINT is empty or not an https:// URL.")
            return 1

    llm = LLMClient(s)
    failures = 0

    for label, model in (("fast", s.fast_model), ("reasoning", s.reasoning_model)):
        try:
            out = await llm.complete(system="Reply with exactly: OK",
                                     prompt="ping", model=model, max_tokens=5)
            print(f"[PASS] {label} deployment answered: {out[:40]!r}")
        except Exception as exc:  # noqa: BLE001 — report every failure kind
            failures += 1
            print(f"[FAIL] {label} deployment: {exc}")
            print("       " + _hint(str(exc)))

    if failures:
        return failures

    if not s.magisterium_api_key:
        print("\n[SKIP] Full-pipeline scenario: MAGISTERIUM_API_KEY not set. "
              "Both model deployments work; add the Magisterium key to test "
              "routing + doctrinal arbitration end-to-end.")
        return 0

    from app.sovereign.orchestrator import Sovereign
    try:
        sov = Sovereign(s)
        resp = await sov.handle(ChatRequest(
            persona=Persona.PATIENT,
            message="I'd like to find a Catholic primary care doctor in Texas.",
            metadata={"state": "TX"}))
        print(f"\n[PASS] Full pipeline: intent={resp.intent.value}, "
              f"doctrinal_review={'yes' if resp.doctrinal_review else 'tier-skipped'}, "
              f"trace={resp.trace_id}")
        print("First 200 chars of the live answer:\n" + resp.answer[:200])
    except Exception as exc:  # noqa: BLE001
        print(f"\n[FAIL] Full pipeline: {exc}")
        print("       " + _hint(str(exc)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
