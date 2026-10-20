"""
Agent 3 — Medical Engagement ("the Interpreter").

Brief: help the patient (and provider) understand their health history and
conditions, raising their information level and framing it to ELEVATE the
doctor-patient encounter. Explicitly NOT a diagnostic tool.

What it produces:
  * A plain-language explanation of the conditions/terms the user mentions.
  * "Good questions to ask your doctor" and what to bring to the visit.
  * Never a diagnosis, never a treatment decision, never a risk score.

PHI handling — this is the agent most likely to touch PHI:
  * PROVIDER persona: the patient history may be PHI. It is sent ONLY to the
    BAA-covered reasoning LLM. It is NEVER sent to Magisterium or any public
    API. The doctrinal gate downstream receives a de-identified summary only.
  * PATIENT persona: still treated as sensitive; same containment applies.
"""
from __future__ import annotations

from app.agents.base import BaseAgent
from app.clients.conversation_read import CONVERSATION_RULES, conversation_prompt
from app.clients.provider_question_contract import PROVIDER_CONTEXT_RULES, ProviderQuestionResult
from app.config import ComplianceProfile, Persona
from app.schemas import AgentResult, ChatRequest, Citation

_SYSTEM = (
    "You are a medical-education assistant that helps a person understand their own "
    "health history and conditions in plain language, so their next appointment is "
    "more productive. Hard rules:\n"
    "  - You are NOT a diagnostic tool. Do not diagnose, stage, score risk, or "
    "    recommend treatments.\n"
    "  - Explain terms simply, note what's generally well established vs. uncertain, "
    "    and ALWAYS frame next steps as questions for their licensed clinician.\n"
    "  - End with 3-5 specific, respectful questions the person could ask their "
    "    doctor, and what records/symptoms to bring.\n"
    "  - Be warm and non-alarming."
)

_PROVIDER_RECORD_SYSTEM = (
    "You help a clinician answer a specific question about the server-supplied "
    "reviewed-note context. Answer that question first, concisely, with dated "
    "source attribution. Do not diagnose, stage, score risk, recommend treatments, "
    "or turn historical plans into new instructions. Preserve uncertainty and "
    "documented factual status. Add medical education, a question list, or records "
    "to bring only when the clinician requests them. Be clear and non-alarming."
)


class EngagementAgent(BaseAgent):
    name = "engagement_agent"

    async def run(self, request: ChatRequest, profile: ComplianceProfile, *,
                  provider_context: ProviderQuestionResult | None = None) -> AgentResult:
        # NB: we deliberately do NOT scrub here — this agent's LLM call is the
        # BAA-covered path, and the explanation needs the real context. The
        # orchestrator guarantees this content is not forwarded to non-BAA hops.
        #
        # And we deliberately do NOT pass use_doctrinal_mcp here: this prompt may
        # carry PHI, while Magisterium's MCP server is non-BAA and the MCP
        # connector is not ZDR-eligible. Doctrinal review for this path happens
        # only at the orchestrator's arbitration gate, on a de-identified summary.
        self.require_baa_covered_llm(profile)

        history = "" if provider_context else request.metadata.get("health_history") or ""
        audience = "clinician preparing for a visit" if profile.persona == Persona.PROVIDER \
            else "patient preparing for their own visit"
        fmt, fmt_rules = self.resolve_output_format(request)

        content = await self.llm.complete(
            system=(_PROVIDER_RECORD_SYSTEM if provider_context else _SYSTEM)
            + CONVERSATION_RULES + (PROVIDER_CONTEXT_RULES if provider_context else ""),
            prompt=(f"Audience: {audience}\n"
                    f"Person's message: {request.message}\n"
                    f"Known history/context: {history}\n\n"
                    + conversation_prompt(request.history)
                    + (f"Reviewed-note context: {provider_context.prompt_text()}\n"
                       if provider_context else "")
                    + f"Output format requirement: {fmt_rules}\n"
                    + ("Answer the specific record question first, using only the "
                       "server-supplied reviewed-note context. Add education or a question list only if requested."
                       if provider_context else
                       "Produce the plain-language explanation and the question list.")),
            max_tokens=1100,
        )
        return AgentResult(
            agent=self.name,
            content=content,
            citations=[] if provider_context else [
                Citation(source="MedlinePlus (suggested reference)",
                         url="https://medlineplus.gov/")
            ],
            data={"output_format": fmt},
        )
