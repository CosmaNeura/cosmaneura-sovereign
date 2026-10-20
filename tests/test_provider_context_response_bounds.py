"""Reviewed-source transport budgets match Core without relaxing legacy reads."""

import asyncio
import copy
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.clients.provider_question import ProviderQuestionClient
from app.clients.provider_question_contract import ProviderQuestionArguments, ProviderQuestionRequest
from app.clients.session_prep import SessionPrepDeliveryError
from tests.test_provider_question_context import headers, turn
from tests.test_provider_reviewed_sources import reviewed_response
from tests.test_provider_source_readiness import source_only_response


def read(handler, version):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = ProviderQuestionClient(
                platform_base_url="https://platform.example", client=http,
                workload_token_provider=AsyncMock(return_value="synthetic-token"),
            )
            request = ProviderQuestionRequest(
                tool_version=version, arguments=ProviderQuestionArguments(
                    source_turn_ref="turn_synthetic_001",
                    detail="prep_summary" if version == "1.0.0" else "reviewed_sources",
                ),
            )
            return await client.read(turn=turn(), request=request)
    return asyncio.run(scenario())


@pytest.mark.parametrize("version", ["1.1.0", "1.2.0"])
def test_valid_unicode_reviewed_context_over_legacy_byte_cap_is_accepted(version):
    raw = reviewed_response() if version == "1.1.0" else source_only_response("unavailable")
    result = raw["result"]
    template = result["provenance"][0]
    result["provenance"] = []
    result["reviewed_sources"] = []
    for index in range(1, 6):
        provenance = copy.deepcopy(template)
        provenance.update(source_ref=f"note_synthetic_{index:03}", source_at=result["current_through"])
        result["provenance"].append(provenance)
        result["reviewed_sources"].append({
            "source_ref": provenance["source_ref"], "source_version": provenance["source_version"],
            "source_at": provenance["source_at"], "sections": {"Objective": "😀" * 15_000},
        })
    body = json.dumps(raw, ensure_ascii=False).encode()
    assert 256 * 1024 < len(body) < 600 * 1024
    response = read(lambda _: httpx.Response(200, headers=headers(), content=body), version)
    assert len(response.result.reviewed_sources) == 5


class NeverRead(httpx.AsyncByteStream):
    async def __aiter__(self):
        raise AssertionError("Oversized declared body must be rejected before reading")
        yield b""


@pytest.mark.parametrize("version,limit", [("1.0.0", 128 * 1024), ("1.1.0", 600 * 1024), ("1.2.0", 600 * 1024)])
def test_each_version_rejects_declared_oversize_before_body_read(version, limit):
    def handler(_):
        return httpx.Response(200, headers={**headers(), "content-length": str(limit + 1)}, stream=NeverRead())
    with pytest.raises(SessionPrepDeliveryError):
        read(handler, version)


def test_reviewed_context_stream_cannot_exceed_cap_without_content_length():
    class Oversize(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"x" * (600 * 1024 + 1)
            raise AssertionError("Reader must stop at its byte bound")

        async def aclose(self):
            self.closed = True

    stream = Oversize()
    with pytest.raises(SessionPrepDeliveryError):
        read(lambda _: httpx.Response(200, headers=headers(), stream=stream), "1.2.0")
    assert stream.closed
