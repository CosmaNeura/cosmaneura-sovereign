"""Pipeline tests that run in mock mode (no credentials needed)."""
import asyncio

from app.compliance.phi import deidentify, assert_no_phi, PHIBoundaryViolation
from app.config import Persona
from app.schemas import ChatRequest, Intent
from app.sovereign.orchestrator import Sovereign


def _run(coro):
    return asyncio.run(coro)


def test_phi_scrubbing_catches_direct_identifiers():
    txt = "Contact John at john@example.com or 603-555-0142, SSN 123-45-6789, MRN 88231."
    res = deidentify(txt)
    assert res.found_phi
    assert "EMAIL" in res.redacted_types
    assert "PHONE" in res.redacted_types
    assert "john@example.com" not in res.text


def test_assert_no_phi_blocks_non_baa_transmission():
    try:
        assert_no_phi("patient email is jane@example.com")
        assert False, "should have raised"
    except PHIBoundaryViolation:
        pass


def test_emergency_short_circuits():
    sov = Sovereign()
    resp = _run(sov.handle(ChatRequest(
        persona=Persona.PATIENT, message="I have crushing chest pain right now")))
    assert resp.intent == Intent.EMERGENCY
    assert "911" in resp.answer


def test_referral_routes_and_returns_matches():
    sov = Sovereign()
    resp = _run(sov.handle(ChatRequest(
        persona=Persona.PATIENT,
        message="find a catholic doctor specialist near me in TX",
        metadata={"state": "TX"})))
    assert resp.intent == Intent.REFERRAL
    assert resp.structured.get("matches")


def test_default_sovereign_mode_is_hybrid():
    from app.config import SovereignMode, get_settings
    assert get_settings().sovereign_mode == SovereignMode.HYBRID


def test_literal_mode_consults_magisterium_on_every_request():
    """In LITERAL mode even a pure-logistics referral routes via Magisterium."""
    from app.config import Settings, SovereignMode

    settings = Settings(sovereign_mode=SovereignMode.LITERAL)  # mock_mode stays True
    sov = Sovereign(settings)

    calls = {"n": 0}
    original_ask = sov.magisterium.ask

    async def counting_ask(*args, **kwargs):
        calls["n"] += 1
        return await original_ask(*args, **kwargs)

    sov.magisterium.ask = counting_ask

    resp = _run(sov.handle(ChatRequest(
        persona=Persona.PATIENT,
        message="find a catholic doctor specialist near me in TX",
        metadata={"state": "TX"})))

    assert resp.intent == Intent.REFERRAL          # routing still correct
    assert calls["n"] >= 1                          # Magisterium was consulted to route


def test_doctrinal_mcp_guard_blocks_phi():
    """The doctrinal MCP path is non-BAA / non-ZDR, so identifiers must be refused."""
    from app.clients.llm import LLMClient
    from app.compliance.phi import PHIBoundaryViolation
    from app.config import Settings

    llm = LLMClient(Settings(magisterium_mcp_enabled=True))  # mock_mode stays True
    try:
        _run(llm.complete(system="x", prompt="patient email is jane@example.com",
                          use_doctrinal_mcp=True))
        assert False, "should have refused identifiers on the MCP path"
    except PHIBoundaryViolation:
        pass


def test_concierge_consults_doctrinal_mcp_only_when_enabled():
    from app.config import Settings

    msg = "what experimental clinical trials and biotech companies exist for ALS"

    on = Sovereign(Settings(magisterium_mcp_enabled=True))
    r_on = _run(on.handle(ChatRequest(persona=Persona.PATIENT, message=msg)))
    assert r_on.intent == Intent.CONCIERGE
    assert r_on.structured.get("doctrinal_mcp_consulted") is True

    off = Sovereign(Settings())  # default: MCP disabled
    r_off = _run(off.handle(ChatRequest(persona=Persona.PATIENT, message=msg)))
    assert r_off.structured.get("doctrinal_mcp_consulted") is False


# --- security regression tests ---------------------------------------------

def test_provider_persona_rejected_unless_enabled():
    """Body-supplied persona=provider must not unlock the PHI path by default."""
    sov = Sovereign()  # default: allow_provider_persona = False
    resp = _run(sov.handle(ChatRequest(
        persona=Persona.PROVIDER,
        message="patient summary",
        metadata={"health_history": "sensitive"})))
    assert resp.safety_notice == "provider_persona_disabled"


def test_provider_persona_allowed_when_enabled():
    from app.config import Settings
    sov = Sovereign(Settings(allow_provider_persona=True))
    resp = _run(sov.handle(ChatRequest(
        persona=Persona.PROVIDER,
        message="help me understand this patient's chart")))
    assert resp.safety_notice != "provider_persona_disabled"


def test_request_field_length_limits_enforced():
    from pydantic import ValidationError
    try:
        ChatRequest(persona=Persona.PATIENT, message="x" * 10_001)
        assert False, "should have rejected oversized message"
    except ValidationError:
        pass


def test_deidentify_bounds_huge_input():
    from app.compliance.phi import deidentify, _MAX_SCRUB_CHARS
    huge = "a" * (_MAX_SCRUB_CHARS + 5000)
    res = deidentify(huge)            # must return quickly, not hang
    assert len(res.text) <= _MAX_SCRUB_CHARS


def test_ssrf_guard_blocks_metadata_and_loopback():
    from app.compliance.netsec import assert_safe_outbound_url, UnsafeOutboundURL
    for bad in ["http://169.254.169.254/latest/meta-data/",
                "http://127.0.0.1:8000/", "file:///etc/passwd",
                "http://localhost/admin"]:
        try:
            assert_safe_outbound_url(bad)
            assert False, f"should have blocked {bad}"
        except UnsafeOutboundURL:
            pass
    # A public https URL passes.
    assert_safe_outbound_url("https://clinicaltrials.gov/api/v2")


def test_audit_chain_links_records():
    from app.compliance.audit import AuditLogger
    from app.config import ComplianceProfile, Settings
    log = AuditLogger(Settings())
    prof = ComplianceProfile(Persona.PATIENT)
    # record() is side-effecting; just verify the chain advances deterministically.
    start = log._prev_chain
    log.record(trace_id="t1", profile=prof, event="a")
    mid = log._prev_chain
    log.record(trace_id="t1", profile=prof, event="b")
    end = log._prev_chain
    assert start != mid != end and len(end) == 64


# --- second-pass security regression tests ----------------------------------

def test_literal_mode_scrubs_before_magisterium():
    """Literal-mode routing must never send raw identifiers to Magisterium."""
    from app.config import Settings, SovereignMode

    sov = Sovereign(Settings(sovereign_mode=SovereignMode.LITERAL))
    seen: list[str] = []
    original_ask = sov.magisterium.ask

    async def spy(*args, **kwargs):
        seen.append(kwargs.get("user_content", ""))
        return await original_ask(*args, **kwargs)

    sov.magisterium.ask = spy
    _run(sov.handle(ChatRequest(
        persona=Persona.PATIENT,
        message="find a specialist for me, my email is jane@example.com "
                "and phone 603-555-0142",
        metadata={"state": "NH"})))
    assert seen, "Magisterium should have been consulted in literal mode"
    joined = " ".join(seen)
    assert "jane@example.com" not in joined
    assert "603-555-0142" not in joined
    assert "[REDACTED:EMAIL]" in joined


def test_magisterium_client_refuses_raw_phi():
    """The belt-and-suspenders guard in the client itself (any mode)."""
    from app.clients.magisterium import MagisteriumClient
    from app.compliance.phi import PHIBoundaryViolation
    from app.config import Settings

    client = MagisteriumClient(Settings())
    try:
        _run(client.ask(system=None, user_content="patient SSN 123-45-6789"))
        assert False, "should have refused identifiers"
    except PHIBoundaryViolation:
        pass


def test_arbiter_fails_closed_on_unparseable_verdict():
    from app.config import Settings
    from app.schemas import DoctrinalSensitivity, Intent as I
    from app.sovereign.arbiter import DoctrinalArbiter

    arb = DoctrinalArbiter(Settings())

    async def broken_parser(**kwargs):
        return {}  # simulates malformed/injected verdict output

    arb.llm.classify_json = broken_parser
    verdict = _run(arb.review(deidentified_draft="some draft", intent=I.CONCIERGE,
                              sensitivity=DoctrinalSensitivity.HIGH))
    assert verdict.conforms is False          # gate fails CLOSED
    assert verdict.revised_content            # Magisterium's review is surfaced


def test_phi_path_requires_baa_attestation():
    from app.agents.engagement_agent import EngagementAgent
    from app.config import ComplianceProfile, Settings

    # Non-mock provider deployment without BAA attestation must refuse.
    agent = EngagementAgent(Settings(mock_mode=False, baa_attested=False))
    profile = ComplianceProfile(Persona.PROVIDER)
    try:
        _run(agent.run(ChatRequest(persona=Persona.PROVIDER, message="chart help"),
                       profile))
        assert False, "should have refused without BAA attestation"
    except RuntimeError as e:
        assert "BAA" in str(e)


def test_emergency_overrides_provider_gate():
    """An emergency must get 911 guidance even when the persona is rejected."""
    sov = Sovereign()  # provider persona disabled by default
    resp = _run(sov.handle(ChatRequest(
        persona=Persona.PROVIDER,
        message="my patient in front of me has crushing chest pain right now")))
    assert resp.intent == Intent.EMERGENCY
    assert "911" in resp.answer


def test_partner_booking_url_https_only():
    from app.clients.partner_directory import PartnerDirectoryClient
    from app.config import Settings

    client = PartnerDirectoryClient(Settings())

    async def tainted_load():
        return [
            {"name": "Good", "is_catholic": True, "licensed_states": ["NH"],
             "booking_url": "https://ok.example/book"},
            {"name": "Evil", "is_catholic": True, "licensed_states": ["NH"],
             "booking_url": "javascript:alert(1)"},
        ]

    client._load = tainted_load
    out = _run(client.search(state="NH"))
    by_name = {p["name"]: p for p in out}
    assert by_name["Good"]["booking_url"] == "https://ok.example/book"
    assert by_name["Evil"]["booking_url"] is None


def test_session_id_charset_enforced():
    from pydantic import ValidationError
    try:
        ChatRequest(persona=Persona.PATIENT, message="hi",
                    session_id="bad id\nwith newline")
        assert False, "should have rejected unsafe session_id"
    except ValidationError:
        pass


# --- provider copilot: ethics consult + visit prep ---------------------------

def test_ethics_consult_routes_with_full_arbitration():
    from app.config import Settings
    sov = Sovereign(Settings(allow_provider_persona=True))
    resp = _run(sov.handle(ChatRequest(
        persona=Persona.PROVIDER,
        message="My patient is asking about tubal ligation at delivery. Is that "
                "permitted under the ERDs, and what alternatives may I discuss?")))
    assert resp.intent == Intent.ETHICS_CONSULT
    assert resp.doctrinal_review is not None       # the full gate actually ran
    assert any(c.source == "PubMed" for c in resp.citations)
    assert any(c.source == "USCCB" for c in resp.citations)


def test_ethics_consult_forces_full_gate_even_if_router_says_none():
    """The tiered gate must never skip arbitration on an ethics consult."""
    from app.config import Settings
    from app.schemas import DoctrinalSensitivity

    sov = Sovereign(Settings(allow_provider_persona=True))

    async def lax_route(request):
        return Intent.ETHICS_CONSULT, DoctrinalSensitivity.NONE, "lax router"
    sov.router.route = lax_route

    seen = {}
    original_review = sov.arbiter.review

    async def spy(**kwargs):
        seen["sensitivity"] = kwargs.get("sensitivity")
        return await original_review(**kwargs)
    sov.arbiter.review = spy

    _run(sov.handle(ChatRequest(persona=Persona.PROVIDER,
                                message="case question for the ethics consult")))
    assert seen["sensitivity"] == DoctrinalSensitivity.HIGH


def test_visit_prep_routes_and_returns_prep():
    from app.config import Settings
    sov = Sovereign(Settings(allow_provider_persona=True))
    resp = _run(sov.handle(ChatRequest(
        persona=Persona.PROVIDER,
        message="Help me organize my next session with this client - talking "
                "points and a short outline.",
        metadata={"prior_sessions": "Last time the client explored family history.",
                  "terminology": "client/therapist"})))
    assert resp.intent == Intent.VISIT_PREP
    assert resp.structured.get("feature") == "visit_prep"
    assert resp.structured.get("terminology") == "client/therapist"


def test_new_phi_agents_require_baa_attestation():
    from app.agents.ethics_agent import EthicsConsultAgent
    from app.agents.visit_prep_agent import VisitPrepAgent
    from app.config import ComplianceProfile, Settings

    profile = ComplianceProfile(Persona.PROVIDER)
    for cls in (EthicsConsultAgent, VisitPrepAgent):
        agent = cls(Settings(mock_mode=False, baa_attested=False))
        try:
            _run(agent.run(ChatRequest(persona=Persona.PROVIDER, message="case"),
                           profile))
            assert False, f"{cls.__name__} should refuse without BAA attestation"
        except RuntimeError as e:
            assert "BAA" in str(e)


def test_pubmed_client_refuses_raw_phi():
    """PubMed is a public non-BAA API; identifier-bearing queries must fail."""
    from app.clients.pubmed import PubMedClient
    from app.compliance.phi import PHIBoundaryViolation
    from app.config import Settings

    client = PubMedClient(Settings())
    try:
        _run(client.search("case report for jane@example.com"))
        assert False, "should have refused identifiers"
    except PHIBoundaryViolation:
        pass


# --- HTTP surface: welcome bootstrap ------------------------------------------

def test_welcome_serves_greeting_and_guided_paths():
    """Both UI surfaces bootstrap from /welcome — no auth, no LLM call."""
    from fastapi.testclient import TestClient
    from app.main import app as web_app

    client = TestClient(web_app)
    r = client.get("/welcome")
    assert r.status_code == 200
    w = r.json()
    assert w["persona"] == "patient"
    assert "Catholic" in w["greeting"]
    assert len(w["paths"]) == 4
    assert {p["intent"] for p in w["paths"]} == {"referral", "engagement",
                                                 "concierge", "doctrinal"}
    assert "911" in w["emergency_note"]
    assert w["disclaimer"]

    rp = client.get("/welcome", params={"persona": "provider"})
    intents = {p["intent"] for p in rp.json()["paths"]}
    assert "visit_prep" in intents and "ethics_consult" in intents


# --- authentication (JWT/OIDC) tests ----------------------------------------

def _dev_settings(**kw):
    from app.config import AuthMode, Settings
    base = dict(auth_mode=AuthMode.DEV_HS256, auth_dev_secret="devsecret")
    base.update(kw)
    return Settings(**base)


def _mint(secret="devsecret", roles=("patient",), ttl=3600, **extra):
    import time
    import jwt
    claims = {"sub": "u1", "roles": list(roles),
              "iat": int(time.time()), "exp": int(time.time()) + ttl}
    claims.update(extra)
    return jwt.encode(claims, secret, algorithm="HS256")


def test_auth_disabled_returns_no_principal():
    from app.config import Settings
    from app.security.auth import TokenVerifier, resolve_principal
    s = Settings()  # AUTH_MODE defaults to disabled
    assert resolve_principal(None, TokenVerifier(s), s) is None


def test_auth_required_when_enabled():
    from app.security.auth import AuthError, TokenVerifier, resolve_principal
    s = _dev_settings()
    try:
        resolve_principal(None, TokenVerifier(s), s)
        assert False, "missing token should be rejected when auth is enabled"
    except AuthError:
        pass


def test_valid_token_maps_provider_role_to_persona():
    from app.security.auth import TokenVerifier
    s = _dev_settings()
    p = TokenVerifier(s).verify(_mint(roles=["provider"]))
    assert p.persona == Persona.PROVIDER and p.subject == "u1"
    p2 = TokenVerifier(s).verify(_mint(roles=["member"]))
    assert p2.persona == Persona.PATIENT


def test_tampered_and_expired_tokens_rejected():
    from app.security.auth import AuthError, TokenVerifier
    s = _dev_settings()
    v = TokenVerifier(s)
    # wrong signing secret
    try:
        v.verify(_mint(secret="not-the-secret", roles=["provider"]))
        assert False
    except AuthError:
        pass
    # expired
    try:
        v.verify(_mint(roles=["provider"], ttl=-10))
        assert False
    except AuthError:
        pass


def test_oidc_mode_rejects_symmetric_algorithm_config():
    """alg-confusion guard: HS* not allowed when verifying against JWKS."""
    from app.config import AuthMode, Settings
    from app.security.auth import AuthError, TokenVerifier
    try:
        TokenVerifier(Settings(auth_mode=AuthMode.OIDC,
                               oidc_jwks_url="https://idp.example/jwks",
                               oidc_algorithms=["HS256"]))
        assert False, "HS256 must be refused in oidc mode"
    except AuthError:
        pass


def test_verified_persona_overrides_body_claim():
    """A patient token cannot unlock the PHI path by claiming provider in body."""
    from app.security.auth import Principal
    sov = Sovereign()  # allow_provider_persona False, but principal is authoritative
    patient_principal = Principal(subject="u1", persona=Persona.PATIENT, roles=["member"])
    resp = _run(sov.handle(
        ChatRequest(persona=Persona.PROVIDER, message="find me a clinic",
                    metadata={"health_history": "should be ignored"}),
        principal=patient_principal))
    # Treated as patient: not the provider-rejection path, and persona is patient.
    assert resp.persona == Persona.PATIENT
    assert resp.safety_notice != "provider_persona_disabled"


def test_verified_provider_allowed_without_dev_switch():
    """A verified provider identity does not need allow_provider_persona."""
    from app.security.auth import Principal
    sov = Sovereign()  # allow_provider_persona defaults False
    provider_principal = Principal(subject="dr1", persona=Persona.PROVIDER,
                                   roles=["provider"])
    resp = _run(sov.handle(
        ChatRequest(persona=Persona.PATIENT, message="help me read this chart"),
        principal=provider_principal))
    assert resp.persona == Persona.PROVIDER
    assert resp.safety_notice != "provider_persona_disabled"


if __name__ == "__main__":
    test_phi_scrubbing_catches_direct_identifiers()
    test_assert_no_phi_blocks_non_baa_transmission()
    test_emergency_short_circuits()
    test_referral_routes_and_returns_matches()
    test_default_sovereign_mode_is_hybrid()
    test_literal_mode_consults_magisterium_on_every_request()
    test_doctrinal_mcp_guard_blocks_phi()
    test_concierge_consults_doctrinal_mcp_only_when_enabled()
    test_provider_persona_rejected_unless_enabled()
    test_provider_persona_allowed_when_enabled()
    test_request_field_length_limits_enforced()
    test_deidentify_bounds_huge_input()
    test_ssrf_guard_blocks_metadata_and_loopback()
    test_audit_chain_links_records()
    test_literal_mode_scrubs_before_magisterium()
    test_magisterium_client_refuses_raw_phi()
    test_arbiter_fails_closed_on_unparseable_verdict()
    test_phi_path_requires_baa_attestation()
    test_emergency_overrides_provider_gate()
    test_partner_booking_url_https_only()
    test_session_id_charset_enforced()
    test_ethics_consult_routes_with_full_arbitration()
    test_ethics_consult_forces_full_gate_even_if_router_says_none()
    test_visit_prep_routes_and_returns_prep()
    test_new_phi_agents_require_baa_attestation()
    test_pubmed_client_refuses_raw_phi()
    test_welcome_serves_greeting_and_guided_paths()
    test_auth_disabled_returns_no_principal()
    test_auth_required_when_enabled()
    test_valid_token_maps_provider_role_to_persona()
    test_tampered_and_expired_tokens_rejected()
    test_oidc_mode_rejects_symmetric_algorithm_config()
    test_verified_persona_overrides_body_claim()
    test_verified_provider_allowed_without_dev_switch()
    print("All tests passed.")
