"""Actual signed app-only receiver and metered client; invented Core/provider I/O."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import routes_visit_prep as route
from app.clients.llm import LLMClient
from app.clients.platform_usage import ProviderUsageFacts
from app.clients.visit_prep_patient_authority import PatientAuthorityClient
from app.config import Settings
from app.security import auth


def _scenario(monkeypatch):
    tenant, core, audience, analysis = [str(uuid4()) for _ in range(4)]
    settings = Settings(
        _env_file=None,
        mock_mode=False,
        llm_provider="azure_openai",
        baa_attested=True,
        reasoning_model="synthetic-deployment",
        visit_prep_patient_clarification_enabled=True,
        visit_prep_core_tenant_id=tenant,
        visit_prep_core_client_id=core,
        visit_prep_receiver_audience=audience,
        platform_agent_api_base_url="https://core.test.invalid",
        platform_workload_client_id=str(uuid4()),
        platform_agent_api_scope=f"{uuid4()}/.default",
    )
    monkeypatch.setattr(route, "get_settings", lambda: settings)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="invented-key", alg="RS256", use="sig")
    at = int(datetime.now(UTC).timestamp())
    claims = dict(
        iss=f"https://login.microsoftonline.com/{tenant}/v2.0",
        tid=tenant,
        aud=audience,
        sub="synthetic-core",
        azp=core,
        idtyp="app",
        roles=["VisitPrep.Clarify"],
        nbf=at - 10,
        iat=at - 10,
        exp=at + 300,
    )
    calls = []

    async def transport(request):
        if request.url.host == "login.microsoftonline.com":
            assert request.url.path == f"/{tenant}/discovery/v2.0/keys"
            return httpx.Response(200, content=json.dumps({"keys": [jwk]}).encode())
        assert request.url.host == "core.test.invalid"
        assert (
            request.headers["x-visit-prep-analysis-grant"]
            == "synthetic-encrypted-grant"
        )
        payload = json.loads(request.content)
        calls.append((request.url.path, payload))
        if request.url.path.endswith("/claim"):
            assert set(payload) == {"topic_digest"}
            return httpx.Response(
                200,
                json={
                    "analysis_id": analysis,
                    "model_profile": settings.reasoning_model,
                },
            )
        assert request.url.path.endswith("/usage")
        assert (
            payload["workstream"] == "visit_prep"
            and payload["purpose"] == "visit_prep_patient_clarification"
        )
        assert "mount_context_token" not in payload and "patient_words" not in payload
        return httpx.Response(
            200,
            json={
                "record_ref": "model_usage_synthetic0001",
                "recorded_at": datetime.now(UTC).isoformat(),
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        auth.httpx,
        "AsyncClient",
        lambda **options: real_client(
            **options, transport=httpx.MockTransport(transport)
        ),
    )

    async def token(_):
        return "synthetic-sovereign-token"

    monkeypatch.setattr(
        "app.clients.visit_prep_patient_authority.ManagedIdentityTokenProvider.__call__",
        token,
    )

    async def model(self, **kwargs):
        calls.append(("model", kwargs))
        words = json.loads(kwargs["prompt"])["topic"]["reported_words"]
        return json.dumps(
            dict(
                kind="organize",
                prompt_code=None,
                statements=[
                    dict(source="answer", start=0, end=len(words), quote=words)
                ],
            )
        ), ProviderUsageFacts(
            input_tokens=30, output_tokens=10, usage_source="provider_response"
        )

    monkeypatch.setattr(LLMClient, "_complete_unreported", model)
    app = FastAPI()
    app.include_router(route.router)
    topic = dict(
        contract_version="1",
        protection_profile="core_azure_deid_surrogate_2024_11_15_v1",
        actor_role="patient",
        kind="story",
        physician_prompt="Tell me your story.",
        reported_words="My invented concern.",
        turns=[],
    )

    def headers(**changes):
        token = jwt.encode(
            claims | changes, key, algorithm="RS256", headers={"kid": "invented-key"}
        )
        return {
            "Authorization": f"Bearer {token}",
            "X-Visit-Prep-Analysis-Grant": "synthetic-encrypted-grant",
        }

    return TestClient(app), topic, headers, calls, analysis, settings


def test_signed_core_request_claims_meters_and_returns_bounded_reply(monkeypatch):
    client, topic, headers, calls, analysis, _ = _scenario(monkeypatch)
    response = client.post(
        "/service/v1/visit-prep/clarification", headers=headers(), json=topic
    )
    assert response.status_code == 200, response.text
    assert response.json()["analysis_id"] == analysis
    assert response.json()["reply"]["statements"][0]["quote"] == topic["reported_words"]
    assert [call[0] for call in calls] == [
        "/service/v1/visit-prep/patient-analysis/claim",
        "model",
        "/service/v1/visit-prep/patient-analysis/usage",
    ]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "change",
    [
        dict(roles=["AgentApi.Access"]),
        dict(idtyp="user"),
        dict(scp="delegated"),
        dict(azp=str(uuid4())),
    ],
)
def test_provider_or_wrong_workload_does_not_reach_core_or_model(monkeypatch, change):
    client, topic, headers, calls, _, _ = _scenario(monkeypatch)
    response = client.post(
        "/service/v1/visit-prep/clarification", headers=headers(**change), json=topic
    )
    assert response.status_code == 503 and not calls
    assert "invented" not in response.text


def test_unrecorded_usage_never_returns_model_reply(monkeypatch):
    client, topic, headers, calls, _, _ = _scenario(monkeypatch)

    async def denied(self, **kwargs):
        raise RuntimeError("invented private upstream body")

    monkeypatch.setattr(PatientAuthorityClient, "report", denied)
    response = client.post(
        "/service/v1/visit-prep/clarification", headers=headers(), json=topic
    )
    assert response.status_code == 503 and "quote" not in response.text
    assert any(call[0] == "model" for call in calls)
