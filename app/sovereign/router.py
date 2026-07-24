"""
The Sovereign's router.

First half of the Sovereign's job (Hobbes: the sovereign distributes the task).
A cheap, fast model classifies each request into an Intent and a
DoctrinalSensitivity tier. The sensitivity tier is what lets us "harness
Magisterium to avoid conflict or speed up answers": pure logistics skip heavy
doctrinal arbitration; faith/morals/medical-ethics content gets the full gate.
"""
from __future__ import annotations

from typing import Optional

from app.clients.llm import LLMClient
from app.clients.magisterium import MagisteriumClient
from app.compliance.phi import deidentify
from app.config import Settings, SovereignMode, get_settings
from app.schemas import ChatRequest, DoctrinalSensitivity, Intent

_SYSTEM = (
    "Classify the user request for a Catholic healthcare assistant. "
    "Choose exactly one intent and one doctrinal_sensitivity.\n"
    "intent options: referral (wants to see/find a doctor), concierge (frontier "
    "medicine, trials, biotech, companies), engagement (understand their own "
    "history/conditions), ethics_consult (a clinician asks whether or how a "
    "specific case, procedure, or care plan squares with Catholic teaching / the "
    "Ethical and Religious Directives), visit_prep (a clinician asks for help "
    "preparing, planning, or organizing an upcoming patient session or visit "
    "from prior notes), plan_workbench (a clinician wants to troubleshoot or "
    "refine a care plan from labs/history — investigative analysis separate "
    "from the SOAP note, possibly including diet or lifestyle protocols), "
    "doctrinal (a question about Catholic teaching itself), "
    "small_talk, emergency (acute medical danger).\n"
    "doctrinal_sensitivity: high (touches faith, morals, or medical ethics such as "
    "contraception, abortion, IVF, sterilization, euthanasia, end-of-life), "
    "low (health topic with possible moral dimension), none (pure logistics)."
)

# Used only in LITERAL mode: Magisterium itself is asked to weigh in on every
# request before it is routed. Its prose is then distilled into the structured
# routing decision below.
_LITERAL_ROUTE_SYSTEM = (
    "You are the doctrinal authority overseeing a Catholic healthcare assistant. "
    "A user request has arrived. Briefly note any faith/morals/medical-ethics "
    "dimension it carries (contraception, abortion, IVF, sterilization, "
    "euthanasia, end-of-life, etc.) and whether it is a question of Catholic "
    "teaching itself, a request to find care, a question about frontier medicine "
    "or trials, a request to understand one's own health, or simple conversation."
)


class Router:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        llm: Optional[LLMClient] = None,
        magisterium: Optional[MagisteriumClient] = None,
    ):
        self.s = settings or get_settings()
        self.llm = llm or LLMClient(self.s)
        self.magisterium = magisterium or MagisteriumClient(self.s)

    async def route(self, request: ChatRequest) -> tuple[Intent, DoctrinalSensitivity, str]:
        # Recent turns matter: "tell me more about that" misclassifies in
        # isolation. Include a bounded window of history in the routing
        # context (LITERAL mode de-identifies the whole context before it
        # reaches Magisterium, history included).
        lines = [f"[history {m.role}]: {m.content[:300]}"
                 for m in request.history[-6:]]
        lines.append(f"User: {request.message}")
        lines.append(f"Metadata: {request.metadata}")
        context = "\n".join(lines)

        if self.s.sovereign_mode == SovereignMode.LITERAL:
            # LITERAL sovereign: Magisterium is consulted on *every* request as
            # the task-distributor, not just the final judge. Magisterium is NOT
            # a business associate, so the routing context is DE-IDENTIFIED
            # before it leaves the trust boundary (the hybrid path's doctrinal
            # hops already scrub; this keeps literal mode to the same invariant).
            scrubbed_context = deidentify(context).text
            guidance, _ = await self.magisterium.ask(
                system=_LITERAL_ROUTE_SYSTEM, user_content=scrubbed_context
            )
            result = await self.llm.classify_json(
                system=_SYSTEM,
                prompt=f"{context}\nDoctrinal authority's read: {guidance}",
            )
        else:  # HYBRID (default): fast LLM distributes the task.
            result = await self.llm.classify_json(system=_SYSTEM, prompt=context)

        return self._parse(result)

    @staticmethod
    def _parse(result: dict) -> tuple[Intent, DoctrinalSensitivity, str]:
        try:
            intent = Intent(result.get("intent", "engagement"))
        except ValueError:
            intent = Intent.ENGAGEMENT
        try:
            sensitivity = DoctrinalSensitivity(result.get("doctrinal_sensitivity", "low"))
        except ValueError:
            sensitivity = DoctrinalSensitivity.LOW
        return intent, sensitivity, result.get("rationale", "")
