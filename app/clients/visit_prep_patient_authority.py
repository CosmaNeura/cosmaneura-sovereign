"""Core owns patient authority and the usage ledger; no provider mount is made."""

import asyncio
import json
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from app.clients.platform_audit import ManagedIdentityTokenProvider
from app.clients.platform_usage import UsageWriteReceipt
from app.clients.visit_prep_clarification import ClarificationUnavailable


@dataclass(frozen=True)
class PatientAnalysisSubject:
    client_turn_id: UUID
    trace_id: str


class PatientAuthorityClient:
    def __init__(self, settings, *, grant, client=None):
        url = urlsplit(settings.platform_agent_api_base_url or "")
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ClarificationUnavailable()
        self.origin = settings.platform_agent_api_base_url.rstrip("/")
        self.token_provider = ManagedIdentityTokenProvider(
            client_id=settings.platform_workload_client_id or "",
            scope=settings.platform_agent_api_scope or "",
        )
        self.grant = grant
        self.client = client

    async def _post(self, path, payload):
        async with asyncio.timeout(4):
            token = await self.token_provider()
            if not token or any(c.isspace() for c in token):
                raise ClarificationUnavailable()
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Visit-Prep-Analysis-Grant": self.grant,
                "Accept-Encoding": "identity",
            }

            async def send(client):
                async with client.stream(
                    "POST",
                    f"{self.origin}/service/v1/visit-prep/patient-analysis/{path}",
                    json=payload,
                    headers=headers,
                ) as response:
                    if (
                        response.status_code != 200
                        or response.headers.get("content-encoding", "identity")
                        != "identity"
                    ):
                        raise ClarificationUnavailable()
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        body.extend(part)
                        if len(body) > 4096:
                            raise ClarificationUnavailable()
                return json.loads(body)

            if self.client:
                return await send(self.client)
            async with httpx.AsyncClient(
                timeout=4, trust_env=False, follow_redirects=False
            ) as client:
                return await send(client)

    async def claim(self, topic_digest):
        receipt = await self._post("claim", {"topic_digest": topic_digest})
        if set(receipt) != {"analysis_id", "model_profile"}:
            raise ClarificationUnavailable()
        analysis = UUID(receipt["analysis_id"])
        if analysis.version != 4 or str(analysis) != receipt["analysis_id"]:
            raise ClarificationUnavailable()
        return PatientAnalysisSubject(analysis, str(analysis)), receipt["model_profile"]

    async def report(self, *, turn, request):
        return UsageWriteReceipt.model_validate(
            await self._post("usage", request.model_dump(mode="json"))
        )
