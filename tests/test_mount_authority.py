"""Mount authority regression tests (offline, credential-free)."""
import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

from app.config import AuthMode, MountProfile, Persona, Settings
from app.clients.platform_conversation import ConversationReceipt
from app.clients.platform_usage import UsageDeliveryError, UsageWriteReceipt
from app.schemas import (AgentResult, ChatRequest, ChatResponse,
                         DoctrinalSensitivity, DoctrinalVerdict, Intent,
                         StandardPresentation)
from app.security.auth import Principal
from app.security.mount import (MountBootstrap, MountBootstrapRequest,
                                MountContext, MountResolution,
                                MountResolutionError, MountResolveRequest,
                                MountResolver, PinnedResolver,
                                PlatformResolver)
from app.sovereign.orchestrator import INTENT_CAPABILITIES, Sovereign

EMBED_HEADERS = {"X-Cosmaneura-Embed-Version": "1.0"}


def _run(coro):
    return asyncio.run(coro)


def _provider_principal():
    return Principal(
        subject="provider-public-mount-test",
        persona=Persona.PROVIDER,
        roles=["provider"],
    )


async def _fixed_route(intent):
    return intent, DoctrinalSensitivity.LOW, "fixed test route"


class _MustNotRunAgent:
    async def run(self, request, profile):
        raise AssertionError("capability gate must run before the agent")


async def _must_not_arbitrate(**kwargs):
    raise AssertionError("capability gate must run before arbitration")


def _assert_public_denies(intent: Intent):
    sov = Sovereign()

    async def route(request):
        return await _fixed_route(intent)

    sov.router.route = route
    sov.agents[intent] = _MustNotRunAgent()
    sov.arbiter.review = _must_not_arbitrate
    request = ChatRequest(
        persona=Persona.PROVIDER,
        message="Use the provider workflow for this record.",
        metadata={
            "surface": "provider_assistant",
            "capabilities": ["ehr:labs:read", "plan-workbench:run"],
            "patient_ref": "body-claimed-record",
        },
    )
    response = _run(sov.handle(request, principal=_provider_principal()))
    assert response.intent == intent
    assert response.persona == Persona.PATIENT
    assert response.safety_notice == "capability_denied"
    assert "isn't available" in response.answer


def test_public_denies_lab_review_before_agent_execution():
    _assert_public_denies(Intent.LAB_REVIEW)


def test_public_denies_plan_workbench_before_agent_execution():
    _assert_public_denies(Intent.PLAN_WORKBENCH)


def test_public_denies_visit_prep_before_agent_execution():
    _assert_public_denies(Intent.VISIT_PREP)


def test_public_denies_ethics_consult_before_agent_execution():
    _assert_public_denies(Intent.ETHICS_CONSULT)


def test_public_body_and_verified_provider_claims_cannot_enable_phi_profile():
    sov = Sovereign()
    observed = {}

    async def route(request):
        return await _fixed_route(Intent.ENGAGEMENT)

    class _ProfileSpy:
        async def run(self, request, profile):
            observed["persona"] = request.persona
            observed["phi_allowed"] = profile.phi_allowed
            observed["hipaa"] = profile.hipaa
            return AgentResult(agent="profile_spy", content="safe public answer")

    async def approve(**kwargs):
        return DoctrinalVerdict(conforms=True, rationale="ok")

    sov.router.route = route
    sov.agents[Intent.ENGAGEMENT] = _ProfileSpy()
    sov.arbiter.review = approve
    response = _run(sov.handle(
        ChatRequest(
            persona=Persona.PROVIDER,
            message="Explain this condition.",
            metadata={"capabilities": ["ehr:labs:read"], "phi": True},
        ),
        principal=_provider_principal(),
    ))
    assert response.persona == Persona.PATIENT
    assert observed == {
        "persona": Persona.PATIENT,
        "phi_allowed": False,
        "hipaa": False,
    }


def test_body_authority_hints_never_reach_router_or_agent():
    sov = Sovereign()
    observed = {}

    async def route(request):
        observed["router"] = request.metadata
        return await _fixed_route(Intent.ENGAGEMENT)

    class _MetadataSpy:
        async def run(self, request, profile):
            observed["agent"] = request.metadata
            return AgentResult(agent="metadata_spy", content="safe public answer")

    async def approve(**kwargs):
        return DoctrinalVerdict(conforms=True, rationale="ok")

    sov.router.route = route
    sov.agents[Intent.ENGAGEMENT] = _MetadataSpy()
    sov.arbiter.review = approve
    response = _run(sov.handle(ChatRequest(
        persona=Persona.PROVIDER,
        message="Explain this condition.",
        metadata={
            "surface": "provider_assistant",
            "auth-tier": "provider",
            "capabilities": ["ehr:labs:read"],
            "tenant_id": "body-tenant",
            "actor_id": "body-actor",
            "mount_context_token": "body-token",
            "persona": "provider",
            "safe_context_ref": "snapshot_123456",
        },
    )))
    assert response.persona == Persona.PATIENT
    assert observed == {
        "router": {"safe_context_ref": "snapshot_123456"},
        "agent": {"safe_context_ref": "snapshot_123456"},
    }


def test_emergency_short_circuits_before_mount_resolution():
    class _ExplodingResolver(MountResolver):
        def __init__(self):
            self.calls = 0

        async def resolve(self, request):
            self.calls += 1
            raise AssertionError("emergency must not resolve a mount")

    resolver = _ExplodingResolver()
    sov = Sovereign(mount_resolver=resolver)
    response = _run(sov.handle(ChatRequest(
        persona=Persona.PROVIDER,
        message="I have crushing chest pain right now",
    )))
    assert response.intent == Intent.EMERGENCY
    assert resolver.calls == 0


def test_http_emergency_precedes_missing_or_invalid_auth():
    from fastapi.testclient import TestClient

    from app import main as main_module
    from app.security.auth import TokenVerifier

    settings = Settings(
        auth_mode=AuthMode.DEV_HS256,
        auth_dev_secret="test-secret-at-least-thirty-two-bytes",
    )
    verifier = TokenVerifier(settings)
    sovereign = Sovereign(settings)
    with patch.object(main_module, "_settings", settings):
        with patch.object(main_module, "_verifier", verifier):
            with patch.object(main_module, "_sovereign", sovereign):
                client = TestClient(main_module.app)
                body = {
                    "client_turn_id": str(uuid.uuid4()),
                    "persona": "provider",
                    "message": "I have crushing chest pain right now",
                }
                for headers in ({}, {"Authorization": "Bearer invalid-token"}):
                    response = client.post("/chat", json=body, headers=headers)
                    assert response.status_code == 200
                    assert response.json()["intent"] == "emergency"
                    assert response.json()["presentation"]["mode"] == "emergency"
                    assert "911" in response.json()["answer"]

                denied = client.post(
                    "/chat",
                    json={"client_turn_id": str(uuid.uuid4()),
                          "persona": "patient", "message": "hello"},
                    headers=EMBED_HEADERS,
                )
                assert denied.status_code == 401


def test_platform_http_emergency_precedes_raw_header_parsing_and_errors_are_value_free():
    from fastapi.testclient import TestClient

    from app import main as main_module

    sentinel = "secret-header-sentinel"
    settings = Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
        auth_mode=AuthMode.DISABLED,
        platform_agent_api_enabled=True,
        platform_agent_api_base_url="https://platform.example",
        platform_agent_api_scope="api://platform-app/.default",
        platform_workload_client_id="provider-uai-client-id",
    )
    sovereign = Sovereign(settings)
    with patch.object(main_module, "_settings", settings):
        with patch.object(main_module, "_sovereign", sovereign):
            client = TestClient(main_module.app)
            emergency = client.post(
                "/chat",
                json={
                    "client_turn_id": str(uuid.uuid4()),
                    "persona": "provider",
                    "message": "I have crushing chest pain right now",
                },
                headers={
                    **EMBED_HEADERS,
                    "Authorization": f"Basic {sentinel}",
                    "X-Cosmaneura-Client-Turn-ID": sentinel,
                },
            )
            assert emergency.status_code == 200
            assert emergency.json()["intent"] == "emergency"
            assert emergency.json()["presentation"]["mode"] == "emergency"

            bad_auth = client.post(
                "/chat",
                json={"client_turn_id": str(uuid.uuid4()),
                      "persona": "provider", "message": "hello"},
                headers={
                    **EMBED_HEADERS,
                    "Authorization": f"Basic {sentinel}",
                },
            )
            assert bad_auth.status_code == 401
            assert sentinel not in bad_auth.text

            bad_turn = client.post(
                "/chat",
                json={
                    "client_turn_id": str(uuid.uuid4()),
                    "persona": "provider",
                    "message": "hello",
                },
                headers={
                    **EMBED_HEADERS,
                    "Authorization": "Bearer opaque-mount-token-123456",
                    "X-Cosmaneura-Client-Turn-ID": sentinel,
                },
            )
            assert bad_turn.status_code == 422
            assert sentinel not in bad_turn.text

            oversized_auth = client.post(
                "/chat",
                json={"client_turn_id": str(uuid.uuid4()),
                      "persona": "provider", "message": "hello"},
                headers={
                    **EMBED_HEADERS,
                    "Authorization": "Bearer " + sentinel + "x" * 8200,
                },
            )
            assert oversized_auth.status_code == 401
            assert sentinel not in oversized_auth.text

            oversized_turn = client.post(
                "/chat",
                json={
                    "client_turn_id": str(uuid.uuid4()),
                    "persona": "provider",
                    "message": "hello",
                },
                headers={
                    **EMBED_HEADERS,
                    "Authorization": "Bearer opaque-mount-token-123456",
                    "X-Cosmaneura-Client-Turn-ID": sentinel + "x" * 8192,
                },
            )
            assert oversized_turn.status_code == 422
            assert sentinel not in oversized_turn.text

            emergency_with_oversized_headers = client.post(
                "/chat",
                json={
                    "client_turn_id": str(uuid.uuid4()),
                    "persona": "provider",
                    "message": "I have crushing chest pain right now",
                },
                headers={
                    "Authorization": "Bearer " + sentinel + "x" * 8200,
                    "X-Cosmaneura-Client-Turn-ID": sentinel + "x" * 8192,
                },
            )
            assert emergency_with_oversized_headers.status_code == 200
            assert emergency_with_oversized_headers.json()["intent"] == "emergency"


def test_actual_body_limit_rejects_no_length_stream_and_conflicting_lengths():
    from app import main as main_module

    sentinel = "chunked-body-sentinel-phi"

    class OversizedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sentinel.encode()
            yield b"x" * (256 * 1024)

    async def exercise():
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://sovereign.example",
        ) as client:
            chunked = await client.post(
                "/chat",
                headers={
                    **EMBED_HEADERS,
                    "content-type": "application/json",
                },
                content=OversizedBody(),
            )
            conflicting = await client.post(
                "/chat",
                headers=[
                    ("content-type", "application/json"),
                    ("content-length", "2"),
                    ("content-length", "3"),
                ],
                content=b"{}",
            )
            mixed_framing = await client.post(
                "/chat",
                headers=[
                    ("content-type", "application/json"),
                    ("content-length", "2"),
                    ("transfer-encoding", "chunked"),
                ],
                content=b"{}",
            )
            return chunked, conflicting, mixed_framing

    chunked, conflicting, mixed_framing = _run(exercise())
    assert chunked.status_code == 413
    assert chunked.json()["code"] == "VALIDATION_FAILED"
    assert sentinel not in chunked.text
    assert conflicting.status_code == 400
    assert conflicting.json()["code"] == "VALIDATION_FAILED"
    assert mixed_framing.status_code == 400
    assert mixed_framing.json()["code"] == "VALIDATION_FAILED"


def test_http_validation_errors_never_reflect_submitted_phi_or_values():
    from fastapi.testclient import TestClient

    from app import main as main_module

    sentinel = "PHI-SENTINEL-Jane-Doe-555-0100"
    client = TestClient(main_module.app)
    invalid_requests = (
        {
            "client_turn_id": str(uuid.uuid4()),
            "persona": "provider",
            "message": sentinel * 400,
        },
        {
            "client_turn_id": str(uuid.uuid4()),
            "persona": "provider",
            "message": "hello",
            "session_id": sentinel + "@example.com",
        },
        {
            "client_turn_id": str(uuid.uuid4()),
            "persona": "not-a-persona-" + sentinel,
            "message": "hello",
        },
        {
            "client_turn_id": str(uuid.uuid4()),
            "message": "hello",
            "metadata": {"note_template": sentinel},
        },
    )
    for body in invalid_requests:
        response = client.post("/chat", json=body)
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_FAILED"
        assert response.json()["status"] == 422
        assert response.json()["retryable"] is False
        assert response.headers["content-type"].startswith("application/problem+json")
        assert sentinel not in response.text

    malformed = client.post(
        "/chat",
        content='{"persona":"provider","message":"' + sentinel,
        headers={"Content-Type": "application/json"},
    )
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "VALIDATION_FAILED"
    assert sentinel not in malformed.text

    too_large = client.post(
        "/chat",
        content='{"message":"' + sentinel + "x" * (256 * 1024),
        headers={"Content-Type": "application/json"},
    )
    assert too_large.status_code == 413
    assert sentinel not in too_large.text


def test_platform_digest_fails_before_header_parsing_or_orchestrator():
    from fastapi.testclient import TestClient

    from app import main as main_module

    class _NoDigest:
        async def digest(self, *_args, **_kwargs):
            raise AssertionError("platform digest must fail before orchestration")

    settings = Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
        auth_mode=AuthMode.DISABLED,
        platform_agent_api_enabled=True,
        platform_agent_api_base_url="https://platform.example",
        platform_agent_api_scope="api://platform-app/.default",
        platform_workload_client_id="provider-uai-client-id",
    )
    sentinel = "digest-secret-header-sentinel"
    with patch.object(main_module, "_settings", settings):
        with patch.object(main_module, "_sovereign", _NoDigest()):
            response = TestClient(main_module.app).post(
                "/digest",
                json={"persona": "provider", "sessions": ["synthetic note"]},
                headers={
                    **EMBED_HEADERS,
                    "Authorization": f"Basic {sentinel}",
                    "X-Cosmaneura-Client-Turn-ID": sentinel,
                },
            )
    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    assert response.json()["retryable"] is False
    assert sentinel not in response.text


def test_embed_version_is_required_echoed_and_never_reflects_values():
    from fastapi.testclient import TestClient

    from app import main as main_module

    client = TestClient(main_module.app)
    sentinel = "PHI-version-sentinel-Jane-Doe"
    for headers in (
        {},
        {"X-Cosmaneura-Embed-Version": sentinel},
    ):
        response = client.get("/welcome", headers=headers)
        assert response.status_code == 426
        assert response.json()["code"] == "EMBED_VERSION_UNSUPPORTED"
        assert response.json()["retryable"] is False
        assert response.headers["x-cosmaneura-embed-version"] == "1.0"
        assert response.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert sentinel not in response.text

    success = client.get("/welcome", headers=EMBED_HEADERS)
    assert success.status_code == 200
    assert success.headers["x-cosmaneura-embed-version"] == "1.0"


def test_platform_chat_requires_exact_header_body_turn_and_echoes_it():
    from fastapi.testclient import TestClient

    from app import main as main_module

    settings = Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
        auth_mode=AuthMode.DISABLED,
        platform_agent_api_enabled=True,
        platform_agent_api_base_url="https://platform.example",
        platform_agent_api_scope="api://platform-app/.default",
        platform_workload_client_id="provider-uai-client-id",
    )

    class CapturingSovereign:
        def __init__(self):
            self.calls = []

        async def handle(self, request, **kwargs):
            self.calls.append((request, kwargs))
            return ChatResponse(
                answer="synthetic answer",
                intent=Intent.SMALL_TALK,
                persona=Persona.PROVIDER,
                citations=[],
                doctrinal_review=None,
                structured={},
                safety_notice=None,
                presentation=StandardPresentation(
                    mode="standard", suppress=[], human_contact=None,
                ),
            )

    sovereign = CapturingSovereign()
    turn_id = str(uuid.uuid4())
    base_headers = {
        **EMBED_HEADERS,
        "Authorization": "Bearer opaque-mount-context-token",
    }
    body = {
        "client_turn_id": turn_id,
        "persona": "provider",
        "message": "synthetic question",
    }
    with patch.object(main_module, "_settings", settings):
        with patch.object(main_module, "_sovereign", sovereign):
            client = TestClient(main_module.app)
            success = client.post(
                "/chat",
                json=body,
                headers={
                    **base_headers,
                    "X-Cosmaneura-Client-Turn-ID": turn_id,
                },
            )
            mismatch = client.post(
                "/chat",
                json=body,
                headers={
                    **base_headers,
                    "X-Cosmaneura-Client-Turn-ID": str(uuid.uuid4()),
                },
            )
            missing_body = client.post(
                "/chat",
                json={"persona": "provider", "message": "synthetic question"},
                headers={
                    **base_headers,
                    "X-Cosmaneura-Client-Turn-ID": turn_id,
                },
            )

    assert success.status_code == 200
    assert success.json()["client_turn_id"] == turn_id
    assert success.headers["x-cosmaneura-embed-version"] == "1.0"
    assert sovereign.calls[0][1]["mount_context_token"] == (
        "opaque-mount-context-token"
    )
    assert sovereign.calls[0][1]["principal"] is None
    assert sovereign.calls[0][1]["client_turn_id"] == uuid.UUID(turn_id)
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "VALIDATION_FAILED"
    assert missing_body.status_code == 422
    assert missing_body.json()["code"] == "VALIDATION_FAILED"
    assert len(sovereign.calls) == 1


def test_platform_welcome_uses_sole_bearer_resolution_and_hard_hides_labs():
    from fastapi.testclient import TestClient

    from app import main as main_module

    settings = Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
        auth_mode=AuthMode.DISABLED,
        platform_agent_api_enabled=True,
        platform_agent_api_base_url="https://platform.example",
        platform_agent_api_scope="api://platform-app/.default",
        platform_workload_client_id="provider-uai-client-id",
        feature_athena_labs=True,
    )

    class CapturingSovereign:
        def __init__(self):
            self.calls = []

        async def bootstrap_mount_context(self, **kwargs):
            self.calls.append(kwargs)
            return MountBootstrap(
                context=MountContext(
                    surface="provider_assistant",
                    auth_tier="provider",
                    capabilities=("visit-prep:run", "ehr:labs:read"),
                    tenant_id="tenant_test",
                    actor_id="authority_actor_123456",
                ),
                trace_id=uuid.uuid4().hex,
            )

    sovereign = CapturingSovereign()
    with patch.object(main_module, "_settings", settings):
        with patch.object(main_module, "_sovereign", sovereign):
            response = TestClient(main_module.app).get(
                "/welcome?persona=patient",
                headers={
                    **EMBED_HEADERS,
                    "Authorization": "Bearer opaque-mount-context-token",
                    # The browser never needs this server-to-server header.
                    "X-Cosmaneura-Mount-Context": "ignored-second-token",
                },
            )

    assert response.status_code == 200
    assert response.json()["persona"] == "provider"
    assert {path["intent"] for path in response.json()["paths"]} == {
        "visit_prep"
    }
    assert "labs_tier" not in response.json()
    assert "labs_label" not in response.json()
    assert response.headers["x-cosmaneura-embed-version"] == "1.0"
    assert len(sovereign.calls) == 1
    assert set(sovereign.calls[0]) == {"mount_context_token"}
    assert sovereign.calls[0]["mount_context_token"] == (
        "opaque-mount-context-token"
    )


def test_platform_welcome_checks_version_before_credentials_or_bootstrap():
    from fastapi.testclient import TestClient

    from app import main as main_module

    class MustNotBootstrap:
        async def bootstrap_mount_context(self, **_kwargs):
            raise AssertionError("version gate must precede authority bootstrap")

    settings = Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
        auth_mode=AuthMode.DISABLED,
        platform_agent_api_enabled=True,
        platform_agent_api_base_url="https://platform.example",
        platform_agent_api_scope="api://platform-app/.default",
        platform_workload_client_id="provider-uai-client-id",
    )
    sentinel = "secret-version-and-credential-sentinel"
    with patch.object(main_module, "_settings", settings):
        with patch.object(main_module, "_sovereign", MustNotBootstrap()):
            response = TestClient(main_module.app).get(
                "/welcome",
                headers={
                    "X-Cosmaneura-Embed-Version": sentinel,
                    "Authorization": f"Basic {sentinel}",
                },
            )

    assert response.status_code == 426
    assert response.json()["code"] == "EMBED_VERSION_UNSUPPORTED"
    assert response.headers["x-cosmaneura-embed-version"] == "1.0"
    assert sentinel not in response.text


def test_platform_welcome_bootstrap_failures_are_value_free():
    from fastapi.testclient import TestClient

    from app import main as main_module

    class RejectingSovereign:
        async def bootstrap_mount_context(self, *, mount_context_token):
            code = (
                "MOUNT_CONTEXT_REQUIRED"
                if mount_context_token is None
                else "CONTEXT_INVALID"
            )
            raise MountResolutionError(
                status_code=401,
                code=code,
                retryable=False,
            )

    settings = Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
        auth_mode=AuthMode.DISABLED,
        platform_agent_api_enabled=True,
        platform_agent_api_base_url="https://platform.example",
        platform_agent_api_scope="api://platform-app/.default",
        platform_workload_client_id="provider-uai-client-id",
    )
    sentinel = "secret-mount-token-sentinel-123456"
    with patch.object(main_module, "_settings", settings):
        with patch.object(main_module, "_sovereign", RejectingSovereign()):
            client = TestClient(main_module.app)
            missing = client.get("/welcome", headers=EMBED_HEADERS)
            invalid = client.get(
                "/welcome",
                headers={
                    **EMBED_HEADERS,
                    "Authorization": f"Bearer {sentinel}",
                },
            )

    assert missing.status_code == invalid.status_code == 401
    assert missing.json()["code"] == "CONTEXT_REQUIRED"
    assert invalid.json()["code"] == "CONTEXT_INVALID"
    assert sentinel not in invalid.text
    assert invalid.headers["x-cosmaneura-embed-version"] == "1.0"


def test_welcome_cors_allows_browser_headers_but_not_internal_authority_header():
    from fastapi.testclient import TestClient
    from starlette.middleware.cors import CORSMiddleware

    from app import main as main_module

    origin = "https://dashboard.example"
    cors_app = CORSMiddleware(
        main_module.app,
        allow_origins=[origin],
        allow_credentials=True,
        allow_methods=main_module._CORS_ALLOW_METHODS,
        allow_headers=main_module._CORS_ALLOW_HEADERS,
        expose_headers=main_module._CORS_EXPOSE_HEADERS,
    )
    client = TestClient(cors_app)
    allowed = client.options(
        "/welcome",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": (
                "authorization,x-cosmaneura-embed-version"
            ),
        },
    )
    internal = client.options(
        "/welcome",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-cosmaneura-mount-context",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == origin
    assert internal.status_code == 400
    assert "x-cosmaneura-mount-context" not in {
        value.strip().casefold()
        for value in allowed.headers["access-control-allow-headers"].split(",")
    }


def test_sovereign_bootstrap_has_trace_and_context_but_no_turn_or_writers():
    class MustNotResolve(MountResolver):
        async def resolve(self, _request):
            raise AssertionError("welcome must not open turn-bearing resolution")

    class CapturingBootstrapper:
        def __init__(self):
            self.request = None

        async def bootstrap(self, request):
            self.request = request
            return MountBootstrap(
                context=MountContext(
                    surface="provider_assistant",
                    auth_tier="provider",
                    capabilities=("visit-prep:run",),
                    tenant_id="tenant_test",
                    actor_id="authority_actor_123456",
                ),
                trace_id=request.trace_id,
            )

    class MustNotWrite:
        def __getattribute__(self, name):
            if name.startswith("__"):
                return super().__getattribute__(name)
            raise AssertionError("welcome must not invoke a durable writer")

    settings = Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
        auth_mode=AuthMode.DISABLED,
        platform_agent_api_enabled=True,
        platform_agent_api_base_url="https://platform.example",
        platform_agent_api_scope="api://platform-app/.default",
        platform_workload_client_id="provider-uai-client-id",
    )
    bootstrapper = CapturingBootstrapper()
    writer = MustNotWrite()
    sovereign = Sovereign(
        settings,
        mount_resolver=MustNotResolve(),
        mount_bootstrapper=bootstrapper,  # type: ignore[arg-type]
        platform_audit_client=writer,  # type: ignore[arg-type]
        platform_conversation_client=writer,  # type: ignore[arg-type]
        platform_usage_client=writer,  # type: ignore[arg-type]
    )
    result = _run(sovereign.bootstrap_mount_context(
        mount_context_token="opaque-mount-token-1234567890",
    ))

    assert result.context.surface == "provider_assistant"
    assert bootstrapper.request is not None
    assert set(bootstrapper.request.__dict__) == {
        "trace_id",
        "mount_context_token",
    }
    assert len(bootstrapper.request.trace_id) == 32
    assert "opaque-mount-token-1234567890" not in repr(bootstrapper.request)


def test_client_turn_uuid_is_fresh_and_not_derived_from_session_id():
    class _CapturingResolver(MountResolver):
        def __init__(self):
            self.request = None

        async def resolve(self, request):
            self.request = request
            return MountResolution(
                context=MountContext(
                    surface="public",
                    auth_tier="anonymous",
                    capabilities=("education:answer",),
                    tenant_id=None,
                    actor_id=None,
                ),
                client_turn_id=request.client_turn_id,
                trace_id=request.trace_id,
            )

    resolver = _CapturingResolver()
    sov = Sovereign(mount_resolver=resolver)
    _run(sov.handle(ChatRequest(
        persona=Persona.PATIENT,
        message="hello",
        session_id="legacy-session-key",
    )))
    assert isinstance(resolver.request.client_turn_id, uuid.UUID)
    assert str(resolver.request.client_turn_id) != "legacy-session-key"
    assert resolver.request.trace_id != "legacy-session-key"
    assert 16 <= len(resolver.request.trace_id) <= 128


def test_platform_client_turn_requires_canonical_lowercase_uuid4():
    from app.main import _client_turn_uuid

    value = str(uuid.uuid4())
    assert _client_turn_uuid(value, required=True) == uuid.UUID(value)
    for invalid in (
        value.upper(),
        str(uuid.uuid1()),
        "{" + value + "}",
        "not-a-uuid",
    ):
        with pytest.raises(MountResolutionError) as caught:
            _client_turn_uuid(invalid, required=True)
        assert caught.value.status_code == 400
        assert caught.value.retryable is False


def test_provider_turn_writes_one_hash_only_platform_observation():
    turn_id = uuid.uuid4()

    class _ProviderResolver(MountResolver):
        async def resolve(self, request):
            assert request.mount_context_token == "mount-context-token-123456"
            assert request.client_turn_id == turn_id
            return MountResolution(
                context=MountContext(
                    surface="provider_assistant",
                    auth_tier="provider",
                    capabilities=("education:answer",),
                    tenant_id="tenant_test",
                    actor_id="authority_actor_123456",
                ),
                client_turn_id=request.client_turn_id,
                trace_id=request.trace_id,
                interaction_ref="interaction_ref_123456",
            )

    class _AuditClient:
        def __init__(self):
            self.calls = []

        async def write(self, *, turn, observation):
            self.calls.append((turn, observation))

    class _ConversationClient:
        async def write_user_accepted(self, *, turn, message):
            return ConversationReceipt(
                client_turn_id=turn.client_turn_id,
                turn_ref="conversation_turn_123456",
                phase="user_accepted",
                state="accepted",
                recorded_at=datetime(2026, 8, 19, tzinfo=UTC),
                replayed=False,
            )

        async def write_final_response(self, *, turn, expected_turn_ref, **kwargs):
            assert expected_turn_ref == "conversation_turn_123456"
            return ConversationReceipt(
                client_turn_id=turn.client_turn_id,
                turn_ref=expected_turn_ref,
                phase="final_response",
                state="completed",
                recorded_at=datetime(2026, 8, 19, tzinfo=UTC),
                replayed=False,
            )

    class _UsageClient:
        async def report(self, **kwargs):
            raise AssertionError("patched model path must have no usage calls")

    audit_client = _AuditClient()
    settings = Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
        auth_mode=AuthMode.DISABLED,
        platform_agent_api_enabled=True,
        platform_agent_api_base_url="https://platform.example",
        platform_agent_api_scope="api://platform-app/.default",
        platform_workload_client_id="provider-uai-client-id",
    )
    sov = Sovereign(
        settings,
        mount_resolver=_ProviderResolver(),
        platform_audit_client=audit_client,  # type: ignore[arg-type]
        platform_conversation_client=_ConversationClient(),  # type: ignore[arg-type]
        platform_usage_client=_UsageClient(),  # type: ignore[arg-type]
    )

    async def route(request):
        return await _fixed_route(Intent.SMALL_TALK)

    async def complete(**kwargs):
        return "answer sentinel"

    sov.router.route = route
    sov.llm.complete = complete
    response = _run(sov.handle(
        ChatRequest(persona=Persona.PROVIDER, message="question sentinel"),
        principal=_provider_principal(),
        mount_context_token="mount-context-token-123456",
        client_turn_id=turn_id,
    ))

    assert response.intent == Intent.SMALL_TALK
    assert len(audit_client.calls) == 1
    audit_turn, observation = audit_client.calls[0]
    assert audit_turn.interaction_ref == "interaction_ref_123456"
    assert observation.observation_status == "completed"
    assert observation.route == observation.intent == "small_talk"
    assert len(observation.content_sha256 or "") == 64
    serialized = str(observation.model_dump(mode="json"))
    assert "question sentinel" not in serialized
    assert "answer sentinel" not in serialized


def test_exact_top_level_retry_never_reruns_model_or_tool_work():
    turn_id = uuid.uuid4()

    class Resolver(MountResolver):
        async def resolve(self, request):
            return MountResolution(
                context=MountContext(
                    surface="provider_assistant",
                    auth_tier="provider",
                    capabilities=("education:answer", "ehr:labs:read"),
                    tenant_id="tenant_test",
                    actor_id="authority_actor_123456",
                ),
                client_turn_id=request.client_turn_id,
                trace_id=request.trace_id,
                interaction_ref="interaction_ref_123456",
                interaction_reused=True,
            )

    class Conversation:
        async def write_user_accepted(self, *, turn, message):
            return ConversationReceipt(
                client_turn_id=turn.client_turn_id,
                turn_ref="conversation_turn_123456",
                phase="user_accepted",
                state="accepted",
                recorded_at=datetime(2026, 8, 19, tzinfo=UTC),
                replayed=True,
            )

    class MustNotRun:
        async def write(self, **_kwargs):
            raise AssertionError("audit must not run for an accepted retry")

        async def report(self, **_kwargs):
            raise AssertionError("usage must not run for an accepted retry")

    settings = Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
        auth_mode=AuthMode.DISABLED,
        platform_agent_api_enabled=True,
        platform_agent_api_base_url="https://platform.example",
        platform_agent_api_scope="api://platform-app/.default",
        platform_workload_client_id="provider-uai-client-id",
    )
    sov = Sovereign(
        settings,
        mount_resolver=Resolver(),
        platform_audit_client=MustNotRun(),  # type: ignore[arg-type]
        platform_conversation_client=Conversation(),  # type: ignore[arg-type]
        platform_usage_client=MustNotRun(),  # type: ignore[arg-type]
    )

    async def must_not_route(_request):
        raise AssertionError("accepted retry must stop before routing")

    sov.router.route = must_not_route
    with pytest.raises(MountResolutionError) as caught:
        _run(sov.handle(
            ChatRequest(persona=Persona.PROVIDER, message="review labs"),
            mount_context_token="mount-context-token-123456",
            client_turn_id=turn_id,
        ))
    assert caught.value.status_code == 503
    assert caught.value.code == "CANONICAL_TURN_INCOMPLETE"
    assert caught.value.retryable is False


def test_initial_platform_activation_hard_disables_labs_despite_capability():
    turn_id = uuid.uuid4()

    class Resolver(MountResolver):
        async def resolve(self, request):
            return MountResolution(
                context=MountContext(
                    surface="provider_assistant",
                    auth_tier="provider",
                    capabilities=("education:answer", "ehr:labs:read"),
                    tenant_id="tenant_test",
                    actor_id="authority_actor_123456",
                ),
                client_turn_id=request.client_turn_id,
                trace_id=request.trace_id,
                interaction_ref="interaction_ref_123456",
            )

    class Conversation:
        def __init__(self):
            self.final_calls = []

        async def write_user_accepted(self, *, turn, message):
            return ConversationReceipt(
                client_turn_id=turn.client_turn_id,
                turn_ref="conversation_turn_123456",
                phase="user_accepted",
                state="accepted",
                recorded_at=datetime(2026, 8, 19, tzinfo=UTC),
                replayed=False,
            )

        async def write_final_response(self, **kwargs):
            self.final_calls.append(kwargs)
            turn = kwargs["turn"]
            return ConversationReceipt(
                client_turn_id=turn.client_turn_id,
                turn_ref=kwargs["expected_turn_ref"],
                phase="final_response",
                state="completed",
                recorded_at=datetime(2026, 8, 19, tzinfo=UTC),
                replayed=False,
            )

    class Audit:
        def __init__(self):
            self.calls = []

        async def write(self, **kwargs):
            self.calls.append(kwargs)

    class UsageMustNotRun:
        async def report(self, **_kwargs):
            raise AssertionError("no model call may occur in this test")

    class LabsMustNotRun:
        async def run(self, *_args, **_kwargs):
            raise AssertionError("initial activation must never enter labs")

    settings = Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
        auth_mode=AuthMode.DISABLED,
        platform_agent_api_enabled=True,
        platform_agent_api_base_url="https://platform.example",
        platform_agent_api_scope="api://platform-app/.default",
        platform_workload_client_id="provider-uai-client-id",
        feature_athena_labs=True,
    )
    conversation = Conversation()
    audit = Audit()
    sov = Sovereign(
        settings,
        mount_resolver=Resolver(),
        platform_audit_client=audit,  # type: ignore[arg-type]
        platform_conversation_client=conversation,  # type: ignore[arg-type]
        platform_usage_client=UsageMustNotRun(),  # type: ignore[arg-type]
    )
    sov.agents[Intent.LAB_REVIEW] = LabsMustNotRun()

    async def route(_request):
        return Intent.LAB_REVIEW, DoctrinalSensitivity.LOW, "fixed"

    sov.router.route = route
    response = _run(sov.handle(
        ChatRequest(persona=Persona.PROVIDER, message="review the synthetic labs"),
        mount_context_token="mount-context-token-123456",
        client_turn_id=turn_id,
    ))

    assert response.intent == Intent.LAB_REVIEW
    assert response.safety_notice == "capability_denied"
    assert len(conversation.final_calls) == 1
    assert conversation.final_calls[0]["outcome"] == "refused"
    assert len(audit.calls) == 1


def test_terminal_observation_uses_ordered_model_usage_refs_and_stays_pending_on_failure():
    turn_id = uuid.uuid4()

    class Resolver(MountResolver):
        async def resolve(self, request):
            return MountResolution(
                context=MountContext(
                    surface="provider_assistant",
                    auth_tier="provider",
                    capabilities=("education:answer",),
                    tenant_id="tenant_test",
                    actor_id="authority_actor_123456",
                ),
                client_turn_id=request.client_turn_id,
                trace_id=request.trace_id,
                interaction_ref="interaction_ref_123456",
            )

    class Conversation:
        def __init__(self):
            self.final_calls = 0

        async def write_user_accepted(self, *, turn, message):
            return ConversationReceipt(
                client_turn_id=turn.client_turn_id,
                turn_ref="conversation_turn_123456",
                phase="user_accepted",
                state="accepted",
                recorded_at=datetime(2026, 8, 19, tzinfo=UTC),
                replayed=False,
            )

        async def write_final_response(self, **kwargs):
            self.final_calls += 1
            turn = kwargs["turn"]
            return ConversationReceipt(
                client_turn_id=turn.client_turn_id,
                turn_ref="conversation_turn_123456",
                phase="final_response",
                state="completed",
                recorded_at=datetime(2026, 8, 19, tzinfo=UTC),
                replayed=False,
            )

    class Audit:
        def __init__(self):
            self.observations = []

        async def write(self, *, turn, observation):
            self.observations.append(observation)

    class Usage:
        def __init__(self, fail):
            self.fail = fail
            self.requests = []

        async def report(self, *, turn, request):
            self.requests.append(request)
            if self.fail:
                raise UsageDeliveryError("unavailable", retryable=True)
            return UsageWriteReceipt(
                record_ref=f"model_usage_{request.call_sequence:06d}",
                recorded_at=datetime(2026, 8, 19, tzinfo=UTC),
            )

    def exercise(fail):
        settings = Settings(
            mount_profile=MountProfile.PROVIDER_ASSISTANT,
            mount_tenant_id="tenant_test",
            auth_mode=AuthMode.DISABLED,
            platform_agent_api_enabled=True,
            platform_agent_api_base_url="https://platform.example",
            platform_agent_api_scope="api://platform-app/.default",
            platform_workload_client_id="provider-uai-client-id",
        )
        audit = Audit()
        usage = Usage(fail)
        conversation = Conversation()
        sov = Sovereign(
            settings,
            mount_resolver=Resolver(),
            platform_audit_client=audit,  # type: ignore[arg-type]
            platform_conversation_client=conversation,  # type: ignore[arg-type]
            platform_usage_client=usage,  # type: ignore[arg-type]
        )

        async def route(_request):
            return await _fixed_route(Intent.SMALL_TALK)

        sov.router.route = route
        response = _run(sov.handle(
            ChatRequest(persona=Persona.PROVIDER, message="hello"),
            mount_context_token="mount-context-token-123456",
            client_turn_id=turn_id,
        ))
        return response, audit, usage, conversation

    response, audit, usage, conversation = exercise(False)
    assert response.intent == Intent.SMALL_TALK
    assert conversation.final_calls == 1
    assert [request.call_sequence for request in usage.requests] == [1]
    assert len(audit.observations) == 1
    assert [(reference.kind, reference.ref) for reference in (
        audit.observations[0].platform_refs
    )] == [
        ("conversation_turn", "conversation_turn_123456"),
        ("model_usage", "model_usage_000001"),
    ]

    response, audit, usage, conversation = exercise(True)
    assert response.intent == Intent.SMALL_TALK
    assert conversation.final_calls == 1
    assert len(usage.requests) == 1
    assert audit.observations == []


def test_provider_pinned_mount_requires_verified_actor_each_request():
    resolver = PinnedResolver(MountContext(
        surface="provider_assistant",
        auth_tier="provider",
        capabilities=("education:answer",),
        tenant_id="tenant_test",
        actor_id=None,
    ))
    try:
        _run(resolver.resolve(MountResolveRequest(
            client_turn_id=uuid.uuid4(),
            trace_id="trace-provider-test",
        )))
        assert False, "provider mount must require a verified actor"
    except MountResolutionError as exc:
        assert exc.status_code == 401
        assert exc.code == "VERIFIED_ACTOR_REQUIRED"

    try:
        _run(resolver.resolve(MountResolveRequest(
            client_turn_id=uuid.uuid4(),
            trace_id="trace-provider-test",
            verified_actor_id="   ",
            verified_actor_is_provider=True,
        )))
        assert False, "a blank verified subject is not a known provider actor"
    except MountResolutionError as exc:
        assert exc.status_code == 401
        assert exc.code == "VERIFIED_ACTOR_REQUIRED"


def test_provider_baa_tripwire_runs_before_router_model():
    settings = Settings(
        mock_mode=False,
        baa_attested=False,
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
    )
    sov = Sovereign(settings)
    calls = {"router": 0}

    async def route(request):
        calls["router"] += 1
        return await _fixed_route(Intent.ENGAGEMENT)

    sov.router.route = route
    try:
        _run(sov.handle(
            ChatRequest(persona=Persona.PATIENT, message="private chart text"),
            principal=_provider_principal(),
        ))
        assert False, "unattested provider content must stop before routing"
    except RuntimeError as exc:
        assert "BAA" in str(exc)
    assert calls["router"] == 0


def test_provider_mount_rejects_verified_non_provider_actor():
    settings = Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
    )
    sov = Sovereign(settings)
    member = Principal(
        subject="verified-member-not-provider",
        persona=Persona.PATIENT,
        roles=["member"],
    )
    try:
        _run(sov.handle(
            ChatRequest(persona=Persona.PROVIDER, message="open the chart"),
            principal=member,
        ))
        assert False, "a non-provider identity must not enter the provider mount"
    except MountResolutionError as exc:
        assert exc.status_code == 403
        assert exc.code == "PROVIDER_ACTOR_REQUIRED"


def test_http_provider_mount_normalizes_non_provider_denial():
    import time

    import jwt
    from fastapi.testclient import TestClient

    from app import main as main_module
    from app.security.auth import TokenVerifier

    secret = "test-secret-at-least-thirty-two-bytes"
    settings = Settings(
        auth_mode=AuthMode.DEV_HS256,
        auth_dev_secret=secret,
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
    )
    now = int(time.time())
    token = jwt.encode(
        {"sub": "verified-member", "roles": ["member"],
         "iat": now, "exp": now + 3600},
        secret,
        algorithm="HS256",
    )
    headers = {
        **EMBED_HEADERS,
        "Authorization": f"Bearer {token}",
    }
    with patch.object(main_module, "_settings", settings):
        with patch.object(main_module, "_verifier", TokenVerifier(settings)):
            with patch.object(main_module, "_sovereign", Sovereign(settings)):
                client = TestClient(main_module.app)
                responses = (
                    client.post("/chat", json={
                        "client_turn_id": str(uuid.uuid4()),
                        "persona": "provider",
                        "message": "open the chart",
                    }, headers=headers),
                    client.post("/digest", json={
                        "persona": "provider",
                        "sessions": ["private chart note"],
                    }, headers=headers),
                )
    for response in responses:
        assert response.status_code == 403
        assert response.json()["code"] == "CAPABILITY_DENIED"
        assert response.json()["retryable"] is False
        assert "PROVIDER_ACTOR_REQUIRED" not in response.text


def test_provider_actor_binding_is_isolated_per_request():
    resolver = PinnedResolver(MountContext(
        surface="provider_assistant",
        auth_tier="provider",
        capabilities=("education:answer",),
        tenant_id="tenant_test",
        actor_id=None,
    ))

    async def resolve(subject):
        return await resolver.resolve(MountResolveRequest(
            client_turn_id=uuid.uuid4(),
            trace_id=uuid.uuid4().hex,
            verified_actor_id=subject,
            verified_actor_is_provider=subject is not None,
        ))

    first = _run(resolve("provider-a"))
    second = _run(resolve("provider-b"))
    assert first.context.actor_id != second.context.actor_id
    try:
        _run(resolve(None))
        assert False, "actor from a prior request must never carry forward"
    except MountResolutionError as exc:
        assert exc.code == "VERIFIED_ACTOR_REQUIRED"


def test_pinned_resolver_never_accepts_configured_actor():
    try:
        PinnedResolver(MountContext(
            surface="provider_assistant",
            auth_tier="provider",
            capabilities=("education:answer",),
            tenant_id="tenant_test",
            actor_id="actor_pinned_not_allowed",
        ))
        assert False, "actor must be derived from verified request identity"
    except ValueError:
        pass


def test_mount_context_is_closed_and_rejects_public_ehr_grant():
    base = {
        "surface": "public",
        "auth_tier": "anonymous",
        "capabilities": ["education:answer"],
        "tenant_id": None,
        "actor_id": None,
    }
    for invalid in (
        {**base, "persona": "provider"},
        {**base, "capabilities": ["education:answer", "education:answer"]},
        {**base, "capabilities": ["ehr:labs:read"]},
    ):
        try:
            MountContext.model_validate(invalid)
            assert False, "invalid mount context should fail closed"
        except ValidationError:
            pass


def test_intent_capability_map_excludes_only_static_short_circuits():
    assert set(INTENT_CAPABILITIES) == set(Intent) - {
        Intent.EMERGENCY,
        Intent.SENSITIVE_DISCLOSURE,
    }


def test_platform_resolver_calls_ratified_endpoint_and_parses_exact_response():
    turn_id = uuid.uuid4()
    mount_token = "opaque-mount-token-1234567890"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/service/v1/agent/mount-context/resolve"
        assert request.headers["Authorization"] == "Bearer workload-token"
        assert request.headers["X-Cosmaneura-Mount-Context"] == mount_token
        assert request.headers["X-Cosmaneura-Client-Turn-ID"] == str(turn_id)
        assert request.headers["X-Trace-ID"] == "sovereign-trace-1234"
        return httpx.Response(
            200,
            json={
                "surface": "public",
                "auth_tier": "anonymous",
                "capabilities": ["education:answer"],
                "tenant_id": None,
                "actor_id": None,
            },
            headers={
                "X-Cosmaneura-Interaction-Ref": "interaction_ref_123456",
                "X-Cosmaneura-Client-Turn-ID": str(turn_id),
                "X-Trace-ID": "platform-trace-1234",
                "X-Cosmaneura-Interaction-Reused": "false",
            },
        )

    async def scenario():
        async def workload_token():
            return "workload-token"

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            resolver = PlatformResolver(
                platform_base_url="https://platform.example",
                workload_token_provider=workload_token,
                client=client,
            )
            return await resolver.resolve(MountResolveRequest(
                client_turn_id=turn_id,
                trace_id="sovereign-trace-1234",
                mount_context_token=mount_token,
            ))

    result = _run(scenario())
    assert result.context.persona_hint == Persona.PATIENT
    assert result.interaction_ref == "interaction_ref_123456"
    assert result.trace_id == "platform-trace-1234"
    assert result.interaction_reused is False


def test_platform_bootstrap_calls_no_lineage_endpoint_with_exact_wire_contract():
    mount_token = "opaque-mount-token-1234567890"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/service/v1/agent/mount-context/bootstrap"
        assert request.content == b""
        assert request.headers["Authorization"] == "Bearer workload-token"
        assert request.headers["X-Cosmaneura-Mount-Context"] == mount_token
        assert request.headers["X-Trace-ID"] == "sovereign-trace-1234"
        lowered = {name.casefold() for name in request.headers}
        assert "x-cosmaneura-client-turn-id" not in lowered
        assert "x-cosmaneura-interaction-ref" not in lowered
        assert "x-idempotency-key" not in lowered
        assert "x-cosmaneura-embed-version" not in lowered
        return httpx.Response(
            200,
            json={
                "surface": "provider_assistant",
                "auth_tier": "provider",
                "capabilities": ["visit-prep:run"],
                "tenant_id": "tenant_test",
                "actor_id": "authority_actor_123456",
            },
            headers={"X-Trace-ID": "sovereign-trace-1234"},
        )

    async def scenario():
        async def workload_token():
            return "workload-token"

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as client:
            resolver = PlatformResolver(
                platform_base_url="https://platform.example",
                workload_token_provider=workload_token,
                client=client,
            )
            return await resolver.bootstrap(MountBootstrapRequest(
                trace_id="sovereign-trace-1234",
                mount_context_token=mount_token,
            ))

    result = _run(scenario())
    assert result.context.surface == "provider_assistant"
    assert result.context.actor_id == "authority_actor_123456"
    assert result.trace_id == "sovereign-trace-1234"
    assert set(result.__dict__) == {"context", "trace_id"}


@pytest.mark.parametrize(
    "forbidden_header",
    (
        "X-Cosmaneura-Client-Turn-ID",
        "X-Cosmaneura-Interaction-Ref",
        "X-Cosmaneura-Interaction-Reused",
        "X-Cosmaneura-Idempotency-Key",
        "X-Idempotency-Key",
        "Idempotency-Replayed",
    ),
)
def test_platform_bootstrap_rejects_lineage_headers(forbidden_header):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "surface": "provider_assistant",
                "auth_tier": "provider",
                "capabilities": ["visit-prep:run"],
                "tenant_id": "tenant_test",
                "actor_id": "authority_actor_123456",
            },
            headers={
                "X-Trace-ID": "sovereign-trace-1234",
                forbidden_header: "forbidden-lineage-value",
            },
        )

    async def scenario():
        async def workload_token():
            return "workload-token"

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as client:
            resolver = PlatformResolver(
                platform_base_url="https://platform.example",
                workload_token_provider=workload_token,
                client=client,
            )
            return await resolver.bootstrap(MountBootstrapRequest(
                trace_id="sovereign-trace-1234",
                mount_context_token="opaque-mount-token-1234567890",
            ))

    with pytest.raises(MountResolutionError) as caught:
        _run(scenario())
    assert caught.value.status_code == 503
    assert caught.value.code == "MOUNT_BOOTSTRAP_INVALID"
    assert "forbidden-lineage-value" not in str(caught.value)


def test_platform_bootstrap_rejects_non_exact_body_and_invalid_trace_value_free():
    sentinel = "upstream-PHI-and-token-sentinel"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "surface": "provider_assistant",
                "auth_tier": "provider",
                "capabilities": ["visit-prep:run"],
                "tenant_id": "tenant_test",
                "actor_id": "authority_actor_123456",
                "unexpected": sentinel,
            },
            headers={"X-Trace-ID": sentinel},
        )

    async def scenario():
        async def workload_token():
            return "workload-token"

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as client:
            resolver = PlatformResolver(
                platform_base_url="https://platform.example",
                workload_token_provider=workload_token,
                client=client,
            )
            return await resolver.bootstrap(MountBootstrapRequest(
                trace_id="sovereign-trace-1234",
                mount_context_token="opaque-mount-token-1234567890",
            ))

    with pytest.raises(MountResolutionError) as caught:
        _run(scenario())
    assert caught.value.code == "MOUNT_BOOTSTRAP_INVALID"
    assert sentinel not in str(caught.value)


@pytest.mark.parametrize(
    ("returned_trace", "content_type"),
    (
        ("different-valid-trace-1234", "application/json"),
        ("sovereign-trace-1234", "text/plain"),
    ),
)
def test_platform_bootstrap_rejects_trace_or_media_type_drift(
    returned_trace,
    content_type,
):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "surface": "provider_assistant",
                "auth_tier": "provider",
                "capabilities": ["visit-prep:run"],
                "tenant_id": "tenant_test",
                "actor_id": "authority_actor_123456",
            },
            headers={
                "X-Trace-ID": returned_trace,
                "Content-Type": content_type,
            },
        )

    async def scenario():
        async def workload_token():
            return "workload-token"

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as client:
            resolver = PlatformResolver(
                platform_base_url="https://platform.example",
                workload_token_provider=workload_token,
                client=client,
            )
            return await resolver.bootstrap(MountBootstrapRequest(
                trace_id="sovereign-trace-1234",
                mount_context_token="opaque-mount-token-1234567890",
            ))

    with pytest.raises(MountResolutionError) as caught:
        _run(scenario())
    assert caught.value.code == "MOUNT_BOOTSTRAP_INVALID"


def test_platform_bootstrap_preserves_only_safe_problem_category():
    sentinel = "upstream-PHI-body-sentinel"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "code": "CONTEXT_EXPIRED",
                "retryable": False,
                "detail": sentinel,
            },
        )

    async def scenario():
        async def workload_token():
            return "workload-token"

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as client:
            resolver = PlatformResolver(
                platform_base_url="https://platform.example",
                workload_token_provider=workload_token,
                client=client,
            )
            return await resolver.bootstrap(MountBootstrapRequest(
                trace_id="sovereign-trace-1234",
                mount_context_token="opaque-mount-token-1234567890",
            ))

    with pytest.raises(MountResolutionError) as caught:
        _run(scenario())
    assert caught.value.status_code == 401
    assert caught.value.code == "CONTEXT_EXPIRED"
    assert caught.value.retryable is False
    assert sentinel not in str(caught.value)


def test_platform_resolver_accepts_trusted_private_https_origin():
    async def workload_token():
        return "workload-token"

    resolver = PlatformResolver(
        platform_base_url="https://10.0.0.5",
        workload_token_provider=workload_token,
    )
    assert resolver._endpoint == (
        "https://10.0.0.5/service/v1/agent/mount-context/resolve"
    )
    assert resolver._bootstrap_endpoint == (
        "https://10.0.0.5/service/v1/agent/mount-context/bootstrap"
    )


def test_platform_provider_process_rejects_a_public_mount_resolution():
    class PublicResolver(MountResolver):
        async def resolve(self, request):
            return MountResolution(
                context=MountContext(
                    surface="public",
                    auth_tier="anonymous",
                    capabilities=("education:answer",),
                    tenant_id=None,
                    actor_id=None,
                ),
                client_turn_id=request.client_turn_id,
                trace_id=request.trace_id,
                interaction_ref="interaction_ref_123456",
            )

    settings = Settings(
        mount_profile=MountProfile.PROVIDER_ASSISTANT,
        mount_tenant_id="tenant_test",
        auth_mode=AuthMode.DISABLED,
        platform_agent_api_enabled=True,
        platform_agent_api_base_url="https://platform.example",
        platform_agent_api_scope="api://platform-app/.default",
        platform_workload_client_id="provider-uai-client-id",
    )
    sov = Sovereign(
        settings,
        mount_resolver=PublicResolver(),
        platform_audit_client=object(),  # type: ignore[arg-type]
        platform_conversation_client=object(),  # type: ignore[arg-type]
        platform_usage_client=object(),  # type: ignore[arg-type]
    )
    with pytest.raises(MountResolutionError) as caught:
        _run(sov.handle(
            ChatRequest(persona=Persona.PATIENT, message="hello"),
            mount_context_token="mount-context-token-123456",
            client_turn_id=uuid.uuid4(),
        ))
    assert caught.value.status_code == 403
    assert caught.value.code == "MOUNT_SURFACE_MISMATCH"


def test_platform_resolver_requires_interaction_reused_header():
    turn_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "surface": "public",
                "auth_tier": "anonymous",
                "capabilities": ["education:answer"],
                "tenant_id": None,
                "actor_id": None,
            },
            headers={
                "X-Cosmaneura-Interaction-Ref": "interaction_ref_123456",
                "X-Cosmaneura-Client-Turn-ID": str(turn_id),
                "X-Trace-ID": "platform-trace-1234",
            },
        )

    async def scenario():
        async def workload_token():
            return "workload-token"

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            resolver = PlatformResolver(
                platform_base_url="https://platform.example",
                workload_token_provider=workload_token,
                client=client,
            )
            return await resolver.resolve(MountResolveRequest(
                client_turn_id=turn_id,
                trace_id="sovereign-trace-1234",
                mount_context_token="opaque-mount-token-1234567890",
            ))

    try:
        _run(scenario())
        assert False, "missing required interaction-reused header must fail"
    except MountResolutionError as exc:
        assert exc.code == "MOUNT_RESOLUTION_INVALID"


def test_platform_resolver_rejects_invalid_response_trace_ids():
    turn_id = uuid.uuid4()

    async def workload_token():
        return "workload-token"

    for bad_trace in ("short", "invalid trace value", "x" * 129):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "surface": "public",
                    "auth_tier": "anonymous",
                    "capabilities": ["education:answer"],
                    "tenant_id": None,
                    "actor_id": None,
                },
                headers={
                    "X-Cosmaneura-Interaction-Ref": "interaction_ref_123456",
                    "X-Cosmaneura-Client-Turn-ID": str(turn_id),
                    "X-Trace-ID": bad_trace,
                    "X-Cosmaneura-Interaction-Reused": "false",
                },
            )

        async def scenario():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
            ) as client:
                resolver = PlatformResolver(
                    platform_base_url="https://platform.example",
                    workload_token_provider=workload_token,
                    client=client,
                )
                return await resolver.resolve(MountResolveRequest(
                    client_turn_id=turn_id,
                    trace_id="sovereign-trace-1234",
                    mount_context_token="opaque-mount-token-1234567890",
                ))

        try:
            _run(scenario())
            assert False, "invalid platform trace must fail closed"
        except MountResolutionError as exc:
            assert exc.code == "MOUNT_RESOLUTION_INVALID"


def test_platform_resolver_rejects_provider_context_without_actor():
    turn_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "surface": "provider_assistant",
                "auth_tier": "provider",
                "capabilities": ["education:answer"],
                "tenant_id": "tenant_test",
                "actor_id": None,
            },
            headers={
                "X-Cosmaneura-Interaction-Ref": "interaction_ref_123456",
                "X-Cosmaneura-Client-Turn-ID": str(turn_id),
                "X-Trace-ID": "platform-trace-1234",
                "X-Cosmaneura-Interaction-Reused": "false",
            },
        )

    async def scenario():
        async def workload_token():
            return "workload-token"

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            resolver = PlatformResolver(
                platform_base_url="https://platform.example",
                workload_token_provider=workload_token,
                client=client,
            )
            return await resolver.resolve(MountResolveRequest(
                client_turn_id=turn_id,
                trace_id="sovereign-trace-1234",
                mount_context_token="opaque-mount-token-1234567890",
            ))

    try:
        _run(scenario())
        assert False, "provider context without actor must fail closed"
    except MountResolutionError as exc:
        assert exc.code == "MOUNT_RESOLUTION_INVALID"


def test_platform_resolver_rejects_non_exact_body_without_leaking_token():
    turn_id = uuid.uuid4()
    mount_token = "secret-mount-token-1234567890"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "surface": "public",
                "auth_tier": "anonymous",
                "capabilities": ["education:answer"],
                "tenant_id": None,
                "actor_id": None,
                "unexpected": mount_token,
            },
            headers={
                "X-Cosmaneura-Interaction-Ref": "interaction_ref_123456",
                "X-Cosmaneura-Client-Turn-ID": str(turn_id),
                "X-Trace-ID": "platform-trace-1234",
            },
        )

    async def scenario():
        async def workload_token():
            return "workload-token"

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            resolver = PlatformResolver(
                platform_base_url="https://platform.example",
                workload_token_provider=workload_token,
                client=client,
            )
            return await resolver.resolve(MountResolveRequest(
                client_turn_id=turn_id,
                trace_id="sovereign-trace-1234",
                mount_context_token=mount_token,
            ))

    try:
        _run(scenario())
        assert False, "extra response fields must be rejected"
    except MountResolutionError as exc:
        assert exc.code == "MOUNT_RESOLUTION_INVALID"
        assert mount_token not in str(exc)


@pytest.mark.parametrize(
    ("status_code", "platform_code", "retryable", "public_status", "public_code"),
    (
        (401, "CONTEXT_EXPIRED", False, 401, "CONTEXT_EXPIRED"),
        (403, "SCOPE_DENIED", False, 409, "RECORD_CONTEXT_MISMATCH"),
        (409, "CLIENT_TURN_CONFLICT", False, 409, "VALIDATION_FAILED"),
        (429, "RATE_LIMITED", True, 429, "RATE_LIMITED"),
        (403, "WORKLOAD_NOT_ALLOWED", False, 503, "SERVICE_UNAVAILABLE"),
    ),
)
def test_platform_resolver_preserves_only_safe_problem_categories(
    status_code,
    platform_code,
    retryable,
    public_status,
    public_code,
):
    from app.main import _problem_for_mount_error

    sentinel = "upstream-PHI-body-sentinel"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={
                "code": platform_code,
                "retryable": retryable,
                "detail": sentinel,
            },
        )

    async def scenario():
        async def workload_token():
            return "workload-token"

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            resolver = PlatformResolver(
                platform_base_url="https://platform.example",
                workload_token_provider=workload_token,
                client=client,
            )
            return await resolver.resolve(MountResolveRequest(
                client_turn_id=uuid.uuid4(),
                trace_id="sovereign-trace-1234",
                mount_context_token="opaque-mount-token-1234567890",
            ))

    with pytest.raises(MountResolutionError) as caught:
        _run(scenario())
    assert caught.value.code == platform_code
    assert caught.value.retryable is retryable
    assert sentinel not in str(caught.value)
    assert _problem_for_mount_error(caught.value) == (
        public_status,
        public_code,
    )


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
    print("All mount authority tests passed.")
