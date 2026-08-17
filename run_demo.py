"""
End-to-end demo. Runs the whole Sovereign pipeline with zero credentials
(MOCK_MODE defaults to true), so you can see routing, agent calls, the PHI
firewall, and doctrinal arbitration working before wiring real keys.

    python run_demo.py
"""
import asyncio

from app.config import AuthMode, MountProfile, Persona, Settings
from app.schemas import ChatRequest
from app.security.auth import Principal
from app.sovereign.orchestrator import Sovereign

SCENARIOS = [
    ("Referral (patient)", MountProfile.PUBLIC, ChatRequest(
        persona=Persona.PATIENT,
        message="I'd like to see a Catholic primary care doctor in Texas, ideally telehealth.",
        metadata={"state": "TX"})),
    ("Concierge (patient)", MountProfile.PUBLIC, ChatRequest(
        persona=Persona.PATIENT,
        message="What frontier trials or companies are working on pancreatic cancer right now?",
    )),
    ("Engagement (provider, PHI)", MountProfile.PROVIDER_ASSISTANT, ChatRequest(
        persona=Persona.PROVIDER,
        message="Help me explain this patient's new type 2 diabetes diagnosis before the visit.",
        metadata={"health_history": "Pt John Doe, DOB 04/12/1971, MRN 88231, A1c 8.2, "
                                    "email john@example.com, phone 603-555-0142."})),
    ("Ethics consult (provider)", MountProfile.PROVIDER_ASSISTANT, ChatRequest(
        persona=Persona.PROVIDER,
        message="My patient with a high-risk pregnancy is asking about tubal ligation "
                "at delivery. Is that permitted under the ERDs, and what alternatives "
                "may I discuss with her?",
        metadata={"case_context": "G4P3, prior classical cesarean; cardiology advises "
                                  "against future pregnancy."})),
    ("Visit prep (provider)", MountProfile.PROVIDER_ASSISTANT, ChatRequest(
        persona=Persona.PROVIDER,
        message="Help me organize my next session with this client — a few talking "
                "points and a short outline. We're using internal family systems.",
        metadata={"prior_sessions": "[Transcript excerpt] Client explored resentment "
                                    "toward father; agreed to a journaling exercise "
                                    "before next meeting.",
                  "care_model": "internal family systems",
                  "terminology": "client/therapist"})),
    ("Plan workbench (provider)", MountProfile.PROVIDER_ASSISTANT, ChatRequest(
        persona=Persona.PROVIDER,
        message="Her labs are back and the standard of care isn't working — help me "
                "troubleshoot which levers to adjust. She's motivated for a "
                "structured diet protocol.",
        metadata={"labs": "A1c 8.9 (was 8.2), LDL 160, TSH 2.1, ferritin 18",
                  "case_context": "T2DM 6 yrs, metformin + SGLT2i, adherent; "
                                  "clinician suspects iron deficiency compounding fatigue.",
                  "patient_metrics": {"sex": "female", "age": 52,
                                      "weight_lb": 200, "height_in": 65},
                  "calorie_target": "1500-1600",
                  "output_format": "note"})),
    ("Doctrinal (patient)", MountProfile.PUBLIC, ChatRequest(
        persona=Persona.PATIENT,
        message="Does the Church permit IVF for a married couple struggling with infertility?",
    )),
    ("Emergency (patient)", MountProfile.PUBLIC, ChatRequest(
        persona=Persona.PATIENT,
        message="I'm having crushing chest pain and trouble breathing right now.",
    )),
]


async def main() -> None:
    public = Sovereign(Settings(mount_profile=MountProfile.PUBLIC))
    provider = Sovereign(Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_demo",
        auth_mode=AuthMode.DEV_HS256,
        auth_dev_secret="local-demo-only-not-an-external-credential",
    ))
    # The demo invokes the service layer directly, so this Principal stands in
    # for the verified token produced by the HTTP authentication boundary.
    provider_principal = Principal(
        subject="provider-demo-actor",
        persona=Persona.PROVIDER,
        roles=["provider"],
    )
    for label, mount_profile, req in SCENARIOS:
        print("\n" + "=" * 78)
        print(f"SCENARIO: {label}")
        print("USER:", req.message)
        if mount_profile == MountProfile.PROVIDER_ASSISTANT:
            resp = await provider.handle(req, principal=provider_principal)
        else:
            resp = await public.handle(req)
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
