"""
End-to-end demo. Runs the whole Sovereign pipeline with zero credentials
(MOCK_MODE defaults to true), so you can see routing, agent calls, the PHI
firewall, and doctrinal arbitration working before wiring real keys.

    python run_demo.py
"""
import asyncio

from app.config import Persona, Settings
from app.schemas import ChatMessage, ChatRequest
from app.sovereign.orchestrator import Sovereign

SCENARIOS = [
    ("Referral (patient)", ChatRequest(
        persona=Persona.PATIENT,
        message="I'd like to see a Catholic primary care doctor in Texas, ideally telehealth.",
        metadata={"state": "TX"})),
    ("Concierge (patient)", ChatRequest(
        persona=Persona.PATIENT,
        message="What frontier trials or companies are working on pancreatic cancer right now?",
    )),
    ("Engagement (provider, PHI)", ChatRequest(
        persona=Persona.PROVIDER,
        message="Help me explain this patient's new type 2 diabetes diagnosis before the visit.",
        metadata={"health_history": "Pt John Doe, DOB 04/12/1971, MRN 88231, A1c 8.2, "
                                    "email john@example.com, phone 603-555-0142."})),
    ("Doctrinal (patient)", ChatRequest(
        persona=Persona.PATIENT,
        message="Does the Church permit IVF for a married couple struggling with infertility?",
    )),
    ("Emergency (patient)", ChatRequest(
        persona=Persona.PATIENT,
        message="I'm having crushing chest pain and trouble breathing right now.",
    )),
]


async def main() -> None:
    # Demo enables the provider persona so the provider/PHI scenario runs. In a
    # real deployment this is gated by ALLOW_PROVIDER_PERSONA and bound to authn.
    sov = Sovereign(Settings(allow_provider_persona=True))
    for label, req in SCENARIOS:
        print("\n" + "=" * 78)
        print(f"SCENARIO: {label}")
        print("USER:", req.message)
        resp = await sov.handle(req)
        print(f"INTENT: {resp.intent.value} | PERSONA: {resp.persona.value} "
              f"| trace={resp.trace_id}")
        if resp.safety_notice:
            print(f"SAFETY: {resp.safety_notice}")
        print("ANSWER:\n", resp.answer)
        if resp.citations:
            print("CITATIONS:", [c.source for c in resp.citations])
        if resp.structured.get("matches"):
            print("MATCHES:", [m["name"] for m in resp.structured["matches"]])


if __name__ == "__main__":
    asyncio.run(main())
