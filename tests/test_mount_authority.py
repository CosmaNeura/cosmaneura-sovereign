"""Mount authority regression tests (offline, credential-free)."""
import asyncio
import uuid
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from app.config import AuthMode, Persona, Settings
from app.schemas import (AgentResult, ChatRequest, DoctrinalSensitivity,
                         DoctrinalVerdict, Intent)
from app.security.auth import Principal
from app.security.mount import (MountContext, MountResolution,
                                MountResolutionError, MountResolveRequest,
                                MountResolver, PinnedResolver,
                                PlatformResolver)
from app.sovereign.orchestrator import INTENT_CAPABILITIES, Sovereign


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
                    "persona": "provider",
                    "message": "I have crushing chest pain right now",
                }
                for headers in ({}, {"Authorization": "Bearer invalid-token"}):
                    response = client.post("/chat", json=body, headers=headers)
                    assert response.status_code == 200
                    assert response.json()["intent"] == "emergency"
                    assert "911" in response.json()["answer"]

                denied = client.post("/chat", json={
                    "persona": "patient",
                    "message": "hello",
                })
                assert denied.status_code == 401


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
        mount_surface="provider_assistant",
        mount_auth_tier="provider",
        mount_capabilities=["education:answer"],
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
        mount_surface="provider_assistant",
        mount_auth_tier="provider",
        mount_capabilities=["education:answer"],
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
        mount_surface="provider_assistant",
        mount_auth_tier="provider",
        mount_capabilities=["education:answer", "journey-digest:write"],
    )
    now = int(time.time())
    token = jwt.encode(
        {"sub": "verified-member", "roles": ["member"],
         "iat": now, "exp": now + 3600},
        secret,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    with patch.object(main_module, "_settings", settings):
        with patch.object(main_module, "_verifier", TokenVerifier(settings)):
            with patch.object(main_module, "_sovereign", Sovereign(settings)):
                client = TestClient(main_module.app)
                responses = (
                    client.post("/chat", json={
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
        assert response.json() == {"detail": "Assistant surface is not available."}
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


def test_intent_capability_map_is_exhaustive_and_emergency_is_pre_context():
    assert set(INTENT_CAPABILITIES) == set(Intent) - {Intent.EMERGENCY}


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


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
    print("All mount authority tests passed.")
