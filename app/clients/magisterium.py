"""
Client for Magisterium AI — the doctrinal authority and final arbiter.

API shape verified from Magisterium's developer docs:
  POST {base}/chat/completions   (OpenAI-compatible)
  model: "magisterium-1"
  Authorization: Bearer <MAGISTERIUM_API_KEY>
Responses include `citations` and `related_questions` alongside the standard
OpenAI `choices` array.

IMPORTANT COMPLIANCE NOTE
-------------------------
Magisterium is NOT a HIPAA business associate. Never send PHI here. Callers in
the provider build must pass de-identified text only; the PHI firewall enforces
this upstream, but this client also refuses if asked to (belt and suspenders).
"""
from __future__ import annotations

import asyncio
from typing import Optional

import httpx

from app.compliance.netsec import assert_safe_outbound_url, safe_error_message
from app.compliance.phi import assert_no_phi
from app.config import Settings, get_settings
from app.clients.platform_usage import (
    CallKind,
    ProviderUsageFacts,
    begin_model_call,
    finish_model_call,
    provider_usage_facts,
)
from app.schemas import Citation


class MagisteriumClient:
    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or get_settings()

    async def ask(
        self,
        *,
        system: Optional[str],
        user_content: str,
        return_related_questions: bool = False,
        usage_call_kind: CallKind = "doctrinal_arbitration",
        usage_purpose: str = "doctrinal_review",
    ) -> tuple[str, list[Citation]]:
        """Single-turn doctrinal query. Returns (answer_text, citations)."""
        # Belt-and-suspenders PHI boundary: Magisterium is NOT a business
        # associate. Callers must pass de-identified text; this guard refuses if
        # likely identifiers slip through. Runs before the mock branch so the
        # invariant is enforced identically in every mode.
        assert_no_phi(user_content)

        span = begin_model_call(
            call_kind=usage_call_kind,
            provider="mock" if self.s.mock_mode else "magisterium",
            model=self.s.magisterium_model,
            deployment=None,
            purpose=usage_purpose,
        )
        try:
            answer, citations, facts = await self._ask_unreported(
                system=system,
                user_content=user_content,
                return_related_questions=return_related_questions,
            )
        except asyncio.CancelledError:
            await finish_model_call(span, status="cancelled")
            raise
        except Exception as error:
            await finish_model_call(
                span,
                status="failed",
                facts=getattr(error, "usage_facts", None),
            )
            raise
        await finish_model_call(span, status="succeeded", facts=facts)
        return answer, citations

    async def _ask_unreported(
        self,
        *,
        system: Optional[str],
        user_content: str,
        return_related_questions: bool,
    ) -> tuple[str, list[Citation], ProviderUsageFacts]:

        if self.s.mock_mode:
            return (
                "[MOCK MAGISTERIUM] A doctrinally grounded answer would appear here, "
                "with citations to magisterial sources (e.g. the Catechism, the USCCB "
                "Ethical and Religious Directives).",
                [Citation(source="USCCB", title="Ethical and Religious Directives for "
                          "Catholic Health Care Services", url="https://www.usccb.org/")],
                ProviderUsageFacts(),
            )

        if not self.s.magisterium_api_key:
            raise RuntimeError("MAGISTERIUM_API_KEY is not set and mock_mode is off.")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_content})

        headers = {
            "Authorization": f"Bearer {self.s.magisterium_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.s.magisterium_model,
            "messages": messages,
            "stream": False,
            "return_related_questions": return_related_questions,
        }
        assert_safe_outbound_url(self.s.magisterium_base_url)
        try:
            async with httpx.AsyncClient(timeout=self.s.request_timeout_seconds) as client:
                resp = await client.post(
                    f"{self.s.magisterium_base_url}/chat/completions",
                    headers=headers, json=body,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            error = RuntimeError(safe_error_message(exc, context="Magisterium"))
            error.usage_facts = provider_usage_facts(  # type: ignore[attr-defined]
                headers=exc.response.headers if exc.response is not None else None,
                usage=None,
                provider="magisterium",
            )
            raise error from None

        answer = data["choices"][0]["message"]["content"]
        citations = [
            Citation(
                source="Magisterium",
                title=c.get("document_title") or c.get("title"),
                url=c.get("url"),
            )
            for c in data.get("citations", []) or []
        ]
        return answer, citations, provider_usage_facts(
            headers=resp.headers,
            usage=data.get("usage"),
            provider="magisterium",
        )
