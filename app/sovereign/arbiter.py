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
_HISTORICAL_POLICY_REVIEW_QUESTION = (
    "Does this reporting policy conform to Catholic teaching and the USCCB "
    "Ethical and Religious Directives? State your doctrinal judgment and its "
    "rationale. If it does not conform, identify the moral conflict. Evaluate "
    "the reporting policy only; do not assess clinical accuracy or rewrite "
    "clinician-note wording."
)
REVIEW_ASSESSMENT_UNAVAILABLE = (
    "The required review was unavailable. No reviewed answer is available. "
    "Please ask a new question later."
)
_REVIEW_REFUSAL_RE = re.compile(
    r"\b(?:cannot|can['’]t|unable to|not able to|not qualified to|not equipped to|"
    r"do not|don['’]t)\s+"
    r"(?:(?:assess|evaluate|review|revise|edit|analy[sz]e|answer|respond|comment)\b|"
    r"(?:provide|offer|give|perform|make)\s+(?:[\w'-]+\s+){0,6}"
    r"(?:assessments?|evaluations?|reviews?|revisions?|analys(?:is|es)|answers?|"
    r"responses?|opinions?|feedback|"
    r"(?:medical|clinical)\s+(?:[\w'-]+\s+){0,3}(?:advice|guidance|assistance))\b)|"
    r"\b(?:outside|beyond)\s+(?:my|its|the)\s+(?:scope|expertise)\b",
    re.IGNORECASE,
)
# These complete sentences describe the intentionally excluded clinical task,
# not refusal of the policy question. Keep recognition narrow: a mixed or
# ambiguous limitation must retain the existing negative gate.
_CLINICAL_SCOPE_LIMITATION_RE = re.compile(
    r"(?:(?:however|but)\s*,?\s*)?"
    r"(?:(?:I|we)\s+(?:cannot|can['’]t|am unable to|are unable to|"
    r"am not able to|are not able to)|do not|don['’]t)\s+"
    r"(?:assess|evaluate|review)\s+(?:the\s+)?clinical\s+(?:accuracy|correctness)"
    r"(?:\s+or\s+(?:rewrite|edit|revise)\s+(?:the\s+)?"
    r"(?:clinician[- ]note|medical[- ]document)\s+wording)?[.!]?",
    re.IGNORECASE,
)
_AFFIRMATIVE_POLICY_JUDGMENT_RE = re.compile(
    r"(?:this|the)\s+(?:neutral\s+)?(?:historical[- ](?:reporting\s+)?|reporting\s+)?"
    r"policy\s+(?:conforms(?:\s+to\s+(?:Catholic teaching|the teaching of the Catholic Church)"
    r"(?:\s+and\s+(?:the\s+)?(?:USCCB\s+)?(?:ERDs|Ethical and Religious Directives))?)?"
    r"|is\s+consistent\s+with\s+Catholic teaching)[.!]?",
    re.IGNORECASE,
)
_UNRESOLVED_SCOPE_LIMITATION_RE = re.compile(
    r"\b(?:cannot|can['’]t|unable|not able|not qualified|not equipped|do not|don['’]t|"
    r"will not|won['’]t|refuse|decline|outside|beyond)\b|"
    r"\b(?:does not|doesn['’]t|not)\s+(?:conform|consistent)\b|\bnon[- ]conform",
    re.IGNORECASE,
)


def _refuses_required_review(review_text: str, *, historical_policy: bool) -> bool:
    """Preserve refusals without mistaking the narrow clinical exclusion for one.

    Only the validated historical-policy wrapper can use this distinction. An
    explicit policy judgment and entirely clinical limitation sentences merely
    permit the normal strict parser; they never grant conformance themselves.
    All other refusal matches, including mixed doctrine/clinical refusals, deny.
    """
    if not _REVIEW_REFUSAL_RE.search(review_text):
        return False
    if not historical_policy:
        return True
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|[\r\n]+", review_text)
                 if part.strip()]
    if not any(_AFFIRMATIVE_POLICY_JUDGMENT_RE.fullmatch(part) for part in sentences):
        return True
    return any(_UNRESOLVED_SCOPE_LIMITATION_RE.search(part)
               and not _CLINICAL_SCOPE_LIMITATION_RE.fullmatch(part) for part in sentences)


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
        historical_record_policy: bool = False,
    ) -> DoctrinalVerdict:
        """
        Conformity check on an agent-produced draft.

        Tiered for latency/cost:
          * NONE  -> trust the draft (pure logistics), no Magisterium call.
          * LOW/HIGH -> ask Magisterium to review and, if needed, correct.

        The orchestrator selects historical_record_policy only after validating
        neutral source excerpts. Its LOW/ENGAGEMENT wrapper asks for a policy
        judgment without asking the reviewer to edit medical-document wording.
        This internal option does not change the required review or verdict gate.
        """
        if sensitivity == DoctrinalSensitivity.NONE:
            return DoctrinalVerdict(conforms=True,
                                    rationale="No doctrinal sensitivity; passed.")

        user_content = (f"DRAFT ANSWER:\n{deidentified_draft}\n\n"
                        "Does this conform? If not, give the corrected version.")
        historical_policy = (historical_record_policy is True and intent == Intent.ENGAGEMENT
                             and sensitivity == DoctrinalSensitivity.LOW)
        if historical_policy:
            user_content = (f"HISTORICAL REPORTING POLICY:\n{deidentified_draft}\n\n"
                            + _HISTORICAL_POLICY_REVIEW_QUESTION)
        review_text, citations = await self.magisterium.ask(
            system=_REVIEW_SYSTEM,
            user_content=user_content,
            usage_purpose="doctrinal_review",
        )
        if type(review_text) is not str or not review_text.strip():
            # An absent assessment cannot be supplied by the parser. Never
            # coerce arbitrary values into a rationale or retain their citations.
            verdict = DoctrinalVerdict(
                conforms=False, rationale=REVIEW_ASSESSMENT_UNAVAILABLE,
                revised_content=REVIEW_ASSESSMENT_UNAVAILABLE, citations=[],
            )
            verdict._adjudication_disposition = "assessment_unavailable"
            return verdict

        # An explicit topic-scope refusal is not a conformance judgment. Do not
        # let an erroneous positive parser flag manufacture one. This is only a
        # negative gate; all other assessments retain the normal verdict parse.
        if _refuses_required_review(review_text, historical_policy=historical_policy):
            verdict = DoctrinalVerdict(
                conforms=False, rationale=review_text,
                revised_content=review_text, citations=citations,
            )
            verdict._adjudication_disposition = "scope_refused"
            return verdict

        # Use the fast model only to turn Magisterium's prose verdict into a flag.
        flag = await self.llm.classify_json(
            system=("You are a doctrinal reviewer adjudication parser. Read the "
                    "reviewer's assessment and decide if the draft conforms."),
            prompt=(f"Reviewer assessment:\n{review_text}\n\n"
                    "Fields: conforms (bool), needs_revision (bool)."),
            usage_purpose="doctrinal_verdict_parse",
            reject_duplicate_keys=True,
        )
        # The parser does not grant conformance through truthiness or coercion.
        # Only its closed, consistent boolean judgment is usable. A malformed
        # judgment remains denied and is distinct from an explicit negative.
        valid = (
            type(flag) is dict
            and {"conforms"} <= set(flag) <= {"conforms", "needs_revision"}
            and type(flag["conforms"]) is bool
            and ("needs_revision" not in flag or type(flag["needs_revision"]) is bool)
            and not (flag["conforms"] is True and flag.get("needs_revision") is True)
        )
        conforms = flag["conforms"] if valid else False
        verdict = DoctrinalVerdict(
            conforms=conforms,
            rationale=review_text,
            revised_content=None if conforms else review_text,
            citations=citations,
        )
        verdict._adjudication_disposition = (
            "invalid_verdict" if not valid else "conforms" if conforms else "nonconforms"
        )
        return verdict
