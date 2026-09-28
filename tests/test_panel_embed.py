"""Versioned panel delivery and ratified browser-contract regressions."""
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas import (EmbedChatRequest, EmbedChatResponse,
                         SessionPrepRequest)


ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = ROOT / "app" / "panel.js"
DEMO_PATHS = (ROOT / "app" / "demo.html", ROOT / "app" / "demo_dashboard.html")
PANEL_HARNESS_PATH = ROOT / "tests" / "panel_contract_harness.js"


def _panel_source() -> str:
    return PANEL_PATH.read_text()


def test_versioned_panel_and_deprecated_alias_are_byte_identical():
    client = TestClient(app)
    versioned = client.get("/embed/v1/panel.js")
    legacy = client.get("/panel.js")

    assert versioned.status_code == legacy.status_code == 200
    assert versioned.content == legacy.content == PANEL_PATH.read_bytes()
    assert "javascript" in versioned.headers["content-type"]
    assert "deprecation" not in versioned.headers
    assert legacy.headers["deprecation"] == "true"
    assert legacy.headers["link"] == (
        '</embed/v1/panel.js>; rel="successor-version"'
    )


def test_mount_validates_https_or_explicit_same_origin_loopback():
    panel = _panel_source()

    assert "exactServiceOrigin(opts.sovereignUrl, opts.allowLoopbackHttp)" in panel
    assert '["localhost", "127.0.0.1", "[::1]"]' in panel
    assert "window.location.origin === parsed.origin" in panel
    assert 'parsed.protocol !== "https:" && !loopback' in panel
    assert "parsed.username || parsed.password" in panel
    assert 'parsed.pathname !== "/" || parsed.search || parsed.hash' in panel
    assert "parsed.origin !== value" in panel
    assert 'typeof opts.getContext !== "function"' in panel
    assert 'typeof opts.onHumanContact !== "function"' in panel
    assert 'fetch(url + "/welcome", {' in panel
    assert '"/welcome?' not in panel
    assert '"x-cosmaneura-embed-version": embedVersion' in panel
    assert "opts.persona" not in panel
    assert 'state.persona = w.persona === "provider"' in panel
    assert 'value.persona !== "provider"' in panel
    assert "Sovereign named beta · unavailable" in panel
    assert "setNamedBetaAccess(false)" in panel
    assert "setNamedBetaAccess(true)" in panel
    assert 'typeof opts.onAccessChange === "function"' in panel
    assert "input.readOnly = state.localFixture" in panel
    assert "fmt.disabled = disabled || state.localFixture" in panel
    assert "welcomeData().then" not in panel


def test_presentation_is_governed_and_never_inferred_from_intent():
    panel = _panel_source()

    assert "Educational information — not medical advice." not in panel
    assert "cnp-disc" not in panel
    assert "function governedPresentation(value)" in panel
    assert 'presentation.mode === "emergency"' in panel
    assert 'd.intent === "emergency"' not in panel
    assert 'd.intent === "sensitive_disclosure"' not in panel
    assert 'suppressed.has("disclaimer")' in panel
    assert 'suppressed.has("guided_paths")' in panel
    assert 'suppressed.has("feature_actions")' in panel
    assert "SUPPRESSION_NAMES.some" in panel
    assert "return opts.onHumanContact(contact)" in panel
    assert "fallback.textContent = contact.fallback_text" in panel


def test_record_switch_is_null_safe_and_rehydrates_governed_history_only():
    panel = _panel_source()

    assert "generation: 0" in panel
    assert "recordReady: false" in panel
    assert "var generation = ++state.generation" in panel
    assert "var recordController = new AbortController()" in panel
    assert "state.recordController = recordController" in panel
    assert "signal: recordController.signal" in panel
    assert "controller = new AbortController()" in panel
    assert "signal: controller.signal" in panel
    assert "abort(state.chatController); abort(state.recordController)" in panel
    assert "abort(state.welcomeController)" in panel
    assert "var headers = await freshBearerHeaders()" in panel
    assert "var welcome = await welcomeData(headers, generation)" in panel
    assert "var suppliedContext = await opts.getContext()" in panel
    assert "conversation ? conversation.history : governedHistory(history)" in panel
    assert "governedConversation(history)" in panel
    assert "Array.isArray(loaded)" not in panel
    assert "loaded.messages" in panel
    assert "presentation: m.presentation" in panel
    assert "opts.onHistoryUpdated(recordId, loaded)" in panel
    assert 'thread.innerHTML = ""; chips.innerHTML = ""' in panel
    assert "state.recordReady = false" in panel
    assert "state.clientId = null" in panel
    assert '!state.recordReady || state.clientId === null' in panel
    assert 'problem.code === "RECORD_CONTEXT_MISMATCH"' in panel
    assert "loadThreadFor(null)" in panel
    assert "opts.saveHistory(" not in panel
    assert "state.history.push" not in panel
    assert "state.history.slice" not in panel
    assert "state.clientId !== recordId" in panel
    assert panel.index("var welcome = await welcomeData") < panel.index(
        "var suppliedContext = await opts.getContext()"
    ) < panel.index("await opts.loadHistory")
    assert "!state.accessGranted || !state.recordReady" in panel


def test_provider_turn_transport_uses_fresh_bearer_and_explicit_exact_retry():
    panel = _panel_source()

    assert "getMountContext" not in panel
    assert "x-cosmaneura-mount-context" not in panel.lower()
    assert 'opts.embedVersion !== "1.0"' in panel
    assert 'typeof opts.getAuthToken !== "function"' in panel
    assert '"authorization": "Bearer " + token' in panel
    assert 'headers["x-cosmaneura-client-turn-id"] = attempt.clientTurnId' in panel
    assert "client_turn_id: clientTurnId" in panel
    assert "fetchWithOneExactNetworkRetry" not in panel
    assert "await fetch(attempt.url" in panel
    assert "headers: requestHeaders" in panel
    assert "body: attempt.body" in panel
    assert "Retry this exact request" in panel
    assert "runAttempt(attempt, b)" in panel
    assert "forceRefresh" not in panel
    assert "var refreshedHeaders = await freshBearerHeaders()" in panel
    assert "var refreshedContext = await opts.getContext()" in panel
    assert "state.retryAttempt = attempt" in panel
    assert "input.value = attempt.text" in panel
    assert "error instanceof TypeError" in panel
    assert 'problem.retryable === true' in panel
    assert 'problem.retryable === false' in panel
    assert "d.client_turn_id !== attempt.clientTurnId" in panel
    assert "attempt.initialHeaders = null" in panel
    assert "initialHeaders: initialHeaders" in panel


def test_post_bootstrap_auth_loss_revokes_provider_panel_state():
    panel = _panel_source()

    assert "function lockNamedBeta()" in panel
    assert "state.generation += 1" in panel
    assert 'thread.innerHTML = ""; chips.innerHTML = ""' in panel
    assert "setGovernedChrome(null); setNamedBetaAccess(false)" in panel
    assert "if (r.status === 401 || r.status === 403)" in panel
    assert "error.authenticationUnavailable" in panel


def test_non_2xx_safety_fallback_is_validated_and_not_committed_as_success():
    panel = _panel_source()

    assert "problem && problem.safety_fallback" in panel
    assert 'fallback.presentation.mode !== "sensitive_disclosure"' in panel
    assert 'response.status !== 503 || value.code !== "SERVICE_UNAVAILABLE"' in panel
    assert "problem.safety_fallback.answer" in panel
    assert "problem.safety_fallback.presentation" in panel
    assert "state.history.push" not in panel
    assert "opts.onHistoryUpdated(attempt" not in panel


def test_destroy_aborts_work_and_removes_record_tooltips():
    panel = _panel_source()

    assert "destroy: destroy" in panel
    assert "state.destroyed = true" in panel
    assert "abort(state.welcomeController)" in panel
    assert "clearOwnedTips()" in panel
    assert "clearEventHandlers()" in panel
    assert "removeEventListener(item.event, item.handler)" in panel
    assert "node.onclick = null" in panel
    assert 'state.history = null; state.clientId = null' in panel
    assert 'root.innerHTML = ""' in panel
    assert "tipHost.appendChild(tip); ownedTips.push(tip)" in panel
    assert "root.remove(); style.remove()" in panel


def test_browser_context_and_chat_exclude_the_server_owned_session_prep_ref():
    panel = _panel_source()

    assert '"clientId", "thread_ref", "digest_ref", "terminology", "care_model"' in panel
    context_governor = panel[
        panel.index("function governedContext(value, recordId)"):
        panel.index("function governedChatMetadata(context, outputFormat)")
    ]
    assert "context_snapshot_ref" not in context_governor
    assert '["thread_ref", "digest_ref"].forEach' in context_governor
    assert "meta[key] = context[key]" in panel
    chat_projection = panel[
        panel.index("function governedChatMetadata(context, outputFormat)"):
        panel.index("function applyWelcomeUX(w)")
    ]
    assert '"clientId", "thread_ref", "digest_ref", "terminology", "care_model"' in (
        chat_projection
    )
    assert "context_snapshot_ref" not in chat_projection
    assert "Object.assign(meta, ctx)" not in panel
    assert "...ctx" not in panel
    request_builder_start = panel.index(
        "var meta = governedChatMetadata(context, fmt.value)"
    )
    request_builder = panel[
        request_builder_start:
        panel.index("var clientTurnId = freshClientTurnId()", request_builder_start)
    ]
    assert "context_snapshot_ref" not in request_builder
    for forbidden in (
        "journey_digest", "prior_sessions", "case_context", "labs",
        "patient_metrics", "calorie_target", "macro_split", "note_template",
        "patient_profile", "intake_summary", "record_ref", "analyte",
        "patient_ref", "patient_name",
    ):
        assert forbidden not in request_builder
    assert "history: historyForRequest" not in panel
    assert "persona: requestPersona" not in panel
    assert "session_id:" not in panel
    assert 'sandboxBadge.textContent = "Environment not verified"' in panel
    assert "applyEnvironmentLabel(w)" in panel
    assert 'path.intent === "lab_review"' in panel


def test_v1_request_schema_is_closed_requires_turn_and_makes_persona_optional():
    turn_id = "123e4567-e89b-42d3-a456-426614174000"
    request = EmbedChatRequest(
        client_turn_id=turn_id,
        message="synthetic",
        metadata={"thread_ref": "thread_opaque"},
    )
    assert request.persona is None

    with pytest.raises(ValidationError):
        EmbedChatRequest(message="synthetic")
    with pytest.raises(ValidationError):
        EmbedChatRequest(
            client_turn_id=turn_id,
            message="synthetic",
            metadata={"note_template": "sentinel PHI"},
        )
    with pytest.raises(ValidationError):
        EmbedChatRequest(
            client_turn_id=turn_id,
            message="synthetic",
            metadata={"context_snapshot_ref": "context_synthetic_001"},
        )
    with pytest.raises(ValidationError):
        EmbedChatRequest(
            client_turn_id=turn_id,
            message="synthetic",
            unknown="forbidden",
        )

    session_prep = SessionPrepRequest(client_turn_id=turn_id)
    assert session_prep.model_dump(mode="json") == {"client_turn_id": turn_id}
    with pytest.raises(ValidationError):
        SessionPrepRequest(
            client_turn_id=turn_id,
            context_snapshot_ref="context_synthetic_001",
        )


def test_v1_response_schema_requires_governed_presentation_and_closed_fields():
    schema = EmbedChatResponse.model_json_schema()
    assert set(schema["required"]) >= {
        "answer", "intent", "client_turn_id", "citations",
        "doctrinal_review", "structured", "safety_notice", "presentation",
    }
    assert schema["additionalProperties"] is False
    for name in (
        "StandardPresentation",
        "EmergencyPresentation",
        "SensitiveDisclosurePresentation",
        "HumanContactAction",
    ):
        assert schema["$defs"][name]["additionalProperties"] is False
    sensitive = schema["$defs"]["SensitiveDisclosurePresentation"]
    assert set(sensitive["required"]) == {"mode", "suppress", "human_contact"}
    assert sensitive["properties"]["suppress"]["minItems"] == 7
    assert sensitive["properties"]["suppress"]["maxItems"] == 7
    assert sensitive["properties"]["suppress"]["uniqueItems"] is True


def test_panel_first_send_exact_retry_success_and_record_reset_runtime():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    subprocess.run(
        [node, str(PANEL_HARNESS_PATH), str(PANEL_PATH)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_session_prep_automatic_completion_and_interrupted_delivery_runtime():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    subprocess.run(
        [node, str(ROOT / "tests/session_prep_completion_harness.js"), str(PANEL_PATH)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_session_prep_announcements_exclude_the_clinical_brief_body():
    panel = _panel_source()
    assert '<div class="cnp-prep-progress" role="status" aria-live="polite">' in panel
    assert '<div class="cnp-prep-view" hidden>' in panel
    assert '<div class="cnp-prep-view" role="status"' not in panel


def test_demo_surfaces_keep_entry_closed_and_mount_only_with_a_pasted_token():
    landing, dashboard = (path.read_text() for path in DEMO_PATHS)

    assert "named beta" in landing.casefold()
    assert 'href="/demo/dashboard"' in landing
    assert "valid provider fixture token" in landing
    assert '<script src="/embed/v1/panel.js"></script>' in dashboard
    assert "window.CosmaNeuraPanel.mount" in dashboard
    assert 'type="password"' in dashboard
    assert "allowLoopbackHttp:true" in dashboard
    assert "await panel.setRecord(fixtureId)" in dashboard
    assert "synthetic_walkthrough_01" in dashboard
    assert "localStorage" not in dashboard
    assert "sessionStorage" not in dashboard
    assert "location.hash" not in dashboard


def test_host_surface_has_no_overclaimed_or_writeback_copy():
    source = "\n".join(path.read_text() for path in (*DEMO_PATHS, PANEL_PATH))
    normalized = source.casefold()

    for forbidden in (
        "soc 2",
        "ehr",
        "write-back",
        "soap",
        "transcript",
        "app.cosmaneura.com/notes",
        "local-demo-context-token",
        "insert into note",
        "saved to this visit",
        "public signup",
    ):
        assert forbidden not in normalized
