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
from app.clients.actual_record_review_frame_contract import is_actual_record_review_frame
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
_ACTUAL_RECORD_REVIEW_SYSTEM = (
    "You are the required final doctrinal reviewer for a Catholic healthcare assistant. "
    "Evaluate the moral use and posture of the represented particular answer against "
    "Catholic teaching and the USCCB Ethical and Religious Directives. The covered "
    "selector's observations are fallible interpretations, not a doctrinal verdict "
    "or proof that moral risk is absent. Do not assume conformance or certify future "
    "excerpts. If this supplied context is insufficient for the required judgment, "
    "refuse rather than guess. Clinical accuracy and medical-document editing are "
    "outside this review task."
)
_ACTUAL_RECORD_REVIEW_QUESTION = (
    "Does this represented particular answer's moral use and posture conform to "
    "Catholic teaching and the USCCB Ethical and Religious Directives? State your "
    "judgment and rationale about this answer. If it does not conform, identify "
    "the moral conflict. If the represented context does not support a judgment, "
    "say so. Do not evaluate a policy for all future excerpts, assess clinical "
    "accuracy, or rewrite clinician-note wording."
)
REVIEW_ASSESSMENT_UNAVAILABLE = (
    "The required review was unavailable. No reviewed answer is available. "
    "Please ask a new question later."
)
_REVIEW_REFUSAL_RE = re.compile(
    r"\b(?:cannot|can['’]t|unable to|not able to|not qualified to|not equipped to|"
    r"do not|don['’]t)\s+"
    r"(?:(?:assess|evaluate|review|judge|determine|revise|edit|analy[sz]e|answer|respond|comment)\b|"
    r"(?:provide|offer|give|perform|make)\s+(?:[\w'-]+\s+){0,6}"
    r"(?:assessments?|evaluations?|reviews?|revisions?|analys(?:is|es)|answers?|"
    r"responses?|opinions?|feedback|"
    r"(?:medical|clinical)\s+(?:[\w'-]+\s+){0,3}(?:advice|guidance|assistance))\b)|"
    r"\b(?:outside|beyond)\s+(?:my|its|the)\s+(?:scope|expertise)\b",
    re.IGNORECASE,
)
# These complete clauses describe the intentionally excluded clinical task,
# not refusal of the bounded moral question. Keep recognition narrow: a mixed or
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
_AFFIRMATIVE_ANSWER_JUDGMENT_RE = re.compile(
    r"(?:this|the)\s+(?:(?:represented|particular|displayed)\s+){0,2}answer\s+"
    r"(?:conforms(?:\s+to\s+(?:Catholic teaching|the teaching of the Catholic Church)"
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
_CONDITIONAL_POLICY_RATIONALE_RE = re.compile(
    r"\b(?:if|unless|provided|assuming|conditional(?:ly)?|subject\s+to|"
    r"(?:as|so)\s+long\s+as|on\s+condition(?:\s+that)?|"
    r"contingent\s+(?:on|upon)|depending\s+on|only\s+(?:when|after))\b",
    re.IGNORECASE,
)


def _affirmative_policy_clause(text: str, *, represented_answer: bool = False) -> bool:
    """Recognize the explicit corresponding judgment, without granting conformance.

    A non-refusing rationale may follow an explicit judgment. The unchanged
    verdict parser still has to accept the reviewer's complete assessment.
    """
    judgment = (_AFFIRMATIVE_ANSWER_JUDGMENT_RE if represented_answer
                else _AFFIRMATIVE_POLICY_JUDGMENT_RE)
    if judgment.fullmatch(text):
        return True
    parts = re.split(r"\s+because\s+", text, maxsplit=1, flags=re.IGNORECASE)
    return (len(parts) == 2
            and judgment.fullmatch(parts[0].rstrip(",;")) is not None
            and bool(parts[1].strip())
            and not _UNRESOLVED_SCOPE_LIMITATION_RE.search(parts[1])
            and not _CONDITIONAL_POLICY_RATIONALE_RE.search(parts[1]))


def _refuses_required_review(review_text: str, *, historical_policy: bool,
                            represented_answer: bool = False) -> bool:
    """Preserve refusals without mistaking the narrow clinical exclusion for one.

    Only a bounded policy or closed actual-answer wrapper uses this distinction.
    An explicit corresponding judgment and entirely clinical limitation clauses
    merely
    permit the normal strict parser; they never grant conformance themselves.
    All other refusal matches, including mixed doctrine/clinical refusals, deny.
    """
    bounded_context = historical_policy or represented_answer
    if bounded_context and _CONDITIONAL_POLICY_RATIONALE_RE.search(review_text):
        # A condition anywhere in the assessment qualifies the judgment, even
        # when it is separate from the affirmative or clinical-only clause.
        return True
    if not _REVIEW_REFUSAL_RE.search(review_text):
        return False
    if not bounded_context:
        return True
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|[\r\n]+", review_text)
                 if part.strip()]
    clauses = []
    for sentence in sentences:
        parts = re.split(r"\s*[,;]\s*(?:but|however)\s*,?\s*|\s+but\s+", sentence,
                         maxsplit=1, flags=re.IGNORECASE)
        if (len(parts) == 2 and _affirmative_policy_clause(parts[0], represented_answer=represented_answer)
                and _CLINICAL_SCOPE_LIMITATION_RE.fullmatch(parts[1])):
            clauses.extend(parts)
        else:
            clauses.append(sentence)
    if not any(_affirmative_policy_clause(part, represented_answer=represented_answer) for part in clauses):
        return True
    return any(_UNRESOLVED_SCOPE_LIMITATION_RE.search(part)
               and not _CLINICAL_SCOPE_LIMITATION_RE.fullmatch(part) for part in clauses)


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
        represented_record_answer: bool = False,
    ) -> DoctrinalVerdict:
        """
        Conformity check on an agent-produced draft.

        Tiered for latency/cost:
          * NONE  -> trust the draft (pure logistics), no Magisterium call.
          * LOW/HIGH -> ask Magisterium to review and, if needed, correct.

        The orchestrator selects represented_record_answer only after a private
        binding validates the actual neutral answer, question and full context.
        Its closed LOW/ENGAGEMENT frame asks about this particular moral use.
        The legacy historical_record_policy option retains its separate policy
        question. Neither internal option grants conformance or changes parsing.
        """
        actual_context = represented_record_answer is True
        if actual_context and (
            intent != Intent.ENGAGEMENT or sensitivity != DoctrinalSensitivity.LOW
            or historical_record_policy is not False
            or not is_actual_record_review_frame(deidentified_draft)
        ):
            raise ValueError("actual record review requires a closed LOW engagement context")
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
        elif actual_context:
            user_content = (f"REPRESENTED ACTUAL ANSWER CONTEXT:\n{deidentified_draft}\n\n"
                            + _ACTUAL_RECORD_REVIEW_QUESTION)
        review_text, citations = await self.magisterium.ask(
            system=_ACTUAL_RECORD_REVIEW_SYSTEM if actual_context else _REVIEW_SYSTEM,
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
        if _refuses_required_review(review_text, historical_policy=historical_policy,
                                   represented_answer=actual_context):
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
