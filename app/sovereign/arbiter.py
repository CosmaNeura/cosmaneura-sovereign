"""
The doctrinal arbiter — Magisterium as the Sovereign's final authority.

Second half of the Sovereign's job (Hobbes: the sovereign is the final judge).
Every candidate answer that touches faith/morals passes through here before the
user sees it. Magisterium plays two roles:

  1. ANSWER  — for purely doctrinal questions, Magisterium *is* the response.
  2. REVIEW  — for agent-produced medical answers with doctrinal weight, the
     arbiter asks Magisterium whether the draft conforms to Catholic teaching
     and the USCCB Ethical and Religious Directives, and to supply corrected
     guidance if not. The orchestrator then passes, revises, or blocks.

PHI rule: Magisterium is NOT a business associate, so only de-identified text is
ever sent here. The orchestrator guarantees this before calling `review`.
"""
from __future__ import annotations

import re
from typing import Optional

from app.clients.llm import LLMClient
from app.clients.magisterium import MagisteriumClient
from app.config import Settings, get_settings
from app.schemas import DoctrinalSensitivity, DoctrinalVerdict, Intent

_REVIEW_SYSTEM = (
    "You are the doctrinal reviewer for a Catholic healthcare assistant. Judge "
    "ONLY doctrinal and moral conformance with the teaching of the Catholic Church "
    "and the USCCB Ethical and Religious Directives.\n"
    "The draft CONFORMS unless it actively suggests, endorses, or normalizes "
    "something contrary to Catholic moral teaching (e.g. contraception, abortion, "
    "IVF, direct sterilization, euthanasia). If it conforms, say so plainly.\n"
    "Do NOT mark it non-conforming for missing disclaimers, unverified details, "
    "incompleteness, tone, or safety caveats — those are out of your scope. A "
    "neutral list of faithful Catholic providers conforms. Only when it genuinely "
    "does not conform, explain the problem briefly and give a corrected, "
    "doctrinally faithful version that still helps the person."
)

_ANSWER_SYSTEM = (
    "You are answering a question about Catholic teaching for a healthcare context. "
    "Answer faithfully and pastorally, citing magisterial sources, and relate it to "
    "the USCCB Ethical and Religious Directives where relevant."
)
_REVIEW_REFUSAL_RE = re.compile(
    r"\b(?:cannot|can['’]t|unable to|not able to)\s+"
    r"(?:assess|evaluate|review|revise)\b|"
    r"\b(?:outside|beyond)\s+(?:my|its|the)\s+(?:scope|expertise)\b",
    re.IGNORECASE,
)


class DoctrinalArbiter:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        magisterium: Optional[MagisteriumClient] = None,
        llm: Optional[LLMClient] = None,
    ):
        self.s = settings or get_settings()
        self.magisterium = magisterium or MagisteriumClient(self.s)
        self.llm = llm or LLMClient(self.s)

    async def answer_doctrinal(self, question: str) -> DoctrinalVerdict:
        """Direct doctrinal answer (Magisterium speaks)."""
        text, citations = await self.magisterium.ask(
            system=_ANSWER_SYSTEM,
            user_content=question,
            usage_purpose="doctrinal_answer",
        )
        return DoctrinalVerdict(conforms=True, rationale="Direct doctrinal answer.",
                                revised_content=text, citations=citations)

    async def review(
        self, *, deidentified_draft: str, intent: Intent,
        sensitivity: DoctrinalSensitivity,
    ) -> DoctrinalVerdict:
        """
        Conformity check on an agent-produced draft.

        Tiered for latency/cost:
          * NONE  -> trust the draft (pure logistics), no Magisterium call.
          * LOW/HIGH -> ask Magisterium to review and, if needed, correct.
        """
        if sensitivity == DoctrinalSensitivity.NONE:
            return DoctrinalVerdict(conforms=True,
                                    rationale="No doctrinal sensitivity; passed.")

        review_text, citations = await self.magisterium.ask(
            system=_REVIEW_SYSTEM,
            user_content=(f"DRAFT ANSWER:\n{deidentified_draft}\n\n"
                          "Does this conform? If not, give the corrected version."),
            usage_purpose="doctrinal_review",
        )

        # An explicit topic-scope refusal is not a conformance judgment. Do not
        # let an erroneous positive parser flag manufacture one. This is only a
        # negative gate; all other assessments retain the normal verdict parse.
        if _REVIEW_REFUSAL_RE.search(review_text):
            return DoctrinalVerdict(
                conforms=False, rationale=review_text,
                revised_content=review_text, citations=citations,
            )

        # Use the fast model only to turn Magisterium's prose verdict into a flag.
        flag = await self.llm.classify_json(
            system=("You are a doctrinal reviewer adjudication parser. Read the "
                    "reviewer's assessment and decide if the draft conforms."),
            prompt=(f"Reviewer assessment:\n{review_text}\n\n"
                    "Fields: conforms (bool), needs_revision (bool)."),
            usage_purpose="doctrinal_verdict_parse",
        )
        # FAIL CLOSED: if the verdict can't be parsed (malformed output, or a
        # prompt injection in upstream text breaking the parser), treat the
        # draft as NON-conforming and surface Magisterium's review instead.
        # An orthodoxy gate that fails open is not a gate.
        if not flag:
            conforms = False
        else:
            conforms = bool(flag.get("conforms", False))
        return DoctrinalVerdict(
            conforms=conforms,
            rationale=review_text,
            revised_content=None if conforms else review_text,
            citations=citations,
        )
