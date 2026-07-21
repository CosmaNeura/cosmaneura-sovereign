"""
The reasoning LLM used by the Sovereign orchestrator and the three agents.

Defaults to Anthropic's Messages API (raw HTTP, so the only dependency is
httpx). This path is BAA-eligible for the provider/HIPAA build when used on a
qualifying first-party API tier with Zero Data Retention enabled.

`mock_mode` (default ON) returns deterministic stubs so the entire pipeline
runs end-to-end with no credentials. Swap a real key + set MOCK_MODE=false to
go live. To run inference under a hyperscaler BAA instead, point this module
at Amazon Bedrock or Google Vertex — the public interface stays the same.
"""
from __future__ import annotations

import json
from typing import Optional

import httpx

from app.compliance.netsec import assert_safe_outbound_url, safe_error_message
from app.compliance.phi import assert_no_phi
from app.config import Settings, get_settings


class LLMClient:
    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or get_settings()

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        use_doctrinal_mcp: bool = False,
    ) -> str:
        model = model or self.s.reasoning_model

        # The Magisterium MCP server is NOT a business associate, and Anthropic's
        # MCP connector is NOT ZDR-eligible. So any text routed through it must be
        # de-identified first. This guard runs even in mock mode so the invariant
        # is always enforced and testable; it refuses on detected identifiers.
        if use_doctrinal_mcp:
            assert_no_phi(prompt)

        if self.s.mock_mode:
            if use_doctrinal_mcp:
                return ("[MOCK MAGISTERIUM MCP] Doctrinal advisory: frame this in keeping "
                        "with the USCCB Ethical and Religious Directives; do not endorse "
                        "morally illicit options (e.g. IVF, embryo-destructive research, "
                        "direct sterilization, euthanasia).")
            return _mock_completion(system=system, prompt=prompt)

        if self.s.llm_provider == "azure_openai":
            # `model` is the Azure *deployment* name. The inline doctrinal MCP
            # connector is Anthropic-only, so it's simply not attached here; the
            # always-on Magisterium REST arbitration gate still applies.
            return await self._complete_azure_openai(
                system=system, prompt=prompt, deployment=model,
                max_tokens=max_tokens, temperature=temperature,
            )

        if self.s.llm_provider != "anthropic":
            raise NotImplementedError(
                f"llm_provider={self.s.llm_provider!r} not wired. Add a branch here."
            )
        if not self.s.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set and mock_mode is off.")

        # The MCP connector only attaches on the Anthropic first-party path; it is
        # unsupported on Bedrock/Vertex. When unavailable it is simply omitted --
        # the final REST arbitration gate still guarantees doctrinal review.
        attach_mcp = (
            use_doctrinal_mcp
            and self.s.magisterium_mcp_enabled
            and self.s.llm_provider == "anthropic"
        )

        headers = {
            "x-api-key": self.s.anthropic_api_key,
            "anthropic-version": self.s.anthropic_version,
            "content-type": "application/json",
        }
        if attach_mcp:
            headers["anthropic-beta"] = self.s.anthropic_mcp_beta
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        if attach_mcp:
            assert_safe_outbound_url(self.s.magisterium_mcp_url)
            server = {"type": "url", "url": self.s.magisterium_mcp_url, "name": "magisterium"}
            if self.s.magisterium_mcp_token:
                server["authorization_token"] = self.s.magisterium_mcp_token
            body["mcp_servers"] = [server]
            body["tools"] = [{"type": "mcp_toolset", "mcp_server_name": "magisterium"}]
        try:
            async with httpx.AsyncClient(timeout=self.s.request_timeout_seconds) as client:
                resp = await client.post(
                    f"{self.s.anthropic_base_url}/v1/messages", headers=headers, json=body
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            # Never surface the raw error: in provider mode the request body may
            # contain PHI that httpx can echo back in the exception string.
            raise RuntimeError(safe_error_message(exc, context="LLM inference")) from None
        # Messages API returns a list of content blocks; join the text blocks.
        # (With MCP attached, mcp_tool_use / mcp_tool_result blocks are also
        # present; we keep only the model's final text.)
        return "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        ).strip()

    async def _complete_azure_openai(
        self, *, system: str, prompt: str, deployment: str,
        max_tokens: int, temperature: float,
    ) -> str:
        if not (self.s.azure_openai_endpoint and self.s.azure_openai_api_key):
            raise RuntimeError(
                "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY are required "
                "when llm_provider=azure_openai and mock_mode is off."
            )
        endpoint = self.s.azure_openai_endpoint.rstrip("/")
        url = (f"{endpoint}/openai/deployments/{deployment}/chat/completions"
               f"?api-version={self.s.azure_openai_api_version}")
        headers = {"api-key": self.s.azure_openai_api_key, "content-type": "application/json"}
        body = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        try:
            async with httpx.AsyncClient(timeout=self.s.request_timeout_seconds) as client:
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            # Provider mode may carry PHI in the body; never echo the raw error.
            raise RuntimeError(safe_error_message(exc, context="LLM inference")) from None
        choices = data.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message", {}).get("content") or "").strip()

    async def classify_json(self, *, system: str, prompt: str) -> dict:
        """Helper for routing: ask for strict JSON and parse it safely."""
        raw = await self.complete(
            system=system + "\nRespond with ONLY a JSON object, no prose, no code fences.",
            prompt=prompt,
            model=self.s.fast_model,
            max_tokens=300,
            temperature=0.0,
        )
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}


# ---------------------------------------------------------------------------
# Mock brain — keeps the demo deterministic and dependency-free.
# Heuristics here are intentionally crude; they only exist so the *plumbing*
# is observable. Real routing/answers come from the live model.
# ---------------------------------------------------------------------------
def _mock_completion(*, system: str, prompt: str) -> str:
    low = prompt.lower()
    if "classify the user request" in system.lower():
        # Classify on the user's words only -- ignore metadata and any injected
        # doctrinal-authority guidance (LITERAL mode) so the mock stays stable.
        text = low
        for line in prompt.splitlines():
            if line.lower().startswith("user:"):
                text = line.lower()
                break
        intent = "engagement"
        sensitivity = "low"
        moral = any(w in text for w in ["contracept", "abortion", "ivf", "euthanasia",
                                        "end of life", "end-of-life", "steriliz", "moral",
                                        "ethical", "church teach", "permit", "is it a sin"])
        referral = (any(k in text for k in ["see a ", "find a", "primary care", "specialist",
                                            "telehealth", "refer", "appointment", "book "])
                    or ("doctor" in text and any(k in text for k in ["in ", "near", "want", "looking"]))
                    or ("catholic" in text and ("doctor" in text or "provider" in text)))
        clinician_case = any(k in text for k in ["my patient", "our patient",
                                                 "ethics consult", "ethics committee",
                                                 "religious directives", "erds"])
        prep = any(k in text for k in ["next session", "next visit", "session prep",
                                       "talking points", "organize my next",
                                       "prepare for my next", "plan my next"])
        if any(w in text for w in ["chest pain", "suicid", "overdose", "can't breathe",
                                   "cant breathe", "stroke", "kill myself"]):
            intent, sensitivity = "emergency", "none"
        elif clinician_case and (moral or "licit" in text or "may we" in text
                                 or "allowed" in text):
            intent, sensitivity = "ethics_consult", "high"
        elif prep:
            intent, sensitivity = "visit_prep", "low"
        elif moral and any(w in text for w in ["permit", "church", "sin", "teach", "moral", "ethical"]):
            intent, sensitivity = "doctrinal", "high"
        elif referral:
            intent, sensitivity = "referral", "none"        # pure logistics -> skip gate
        elif any(w in text for w in ["trial", "biotech", "frontier", "experimental",
                                     "company", "companies", "research"]):
            intent, sensitivity = "concierge", "low"
        if moral:
            sensitivity = "high"
        return json.dumps({"intent": intent, "doctrinal_sensitivity": sensitivity,
                           "rationale": "mock heuristic classification"})
    if "doctrinal reviewer" in system.lower():
        return json.dumps({"conforms": True, "rationale": "[MOCK] No conflict detected."})
    # Generic agent / assembly response.
    return ("[MOCK MODEL OUTPUT] This is a stubbed response so you can watch the "
            "Sovereign pipeline route, call tools, and arbitrate. Set MOCK_MODE=false "
            "with real keys to get genuine model output.")
