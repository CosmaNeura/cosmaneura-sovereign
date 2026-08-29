"""Versioned panel delivery and ratified browser-contract regressions."""
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas import EmbedChatRequest, EmbedChatResponse


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


def test_mount_validates_exact_https_origin_and_required_governed_callbacks():
    panel = _panel_source()

    assert "var url = exactHttpsOrigin(opts.sovereignUrl)" in panel
    assert 'parsed.protocol !== "https:"' in panel
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
    assert "var loaded = history === null ? null : governedHistory(history)" in panel
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


def test_platform_turn_transport_uses_fresh_bearer_and_explicit_exact_retry():
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
    assert "if (r.status === 401) attempt.forceRefresh = true" in panel
    assert "? { forceRefresh: true }" in panel
    assert "freshBearerHeaders(refreshRequest)" in panel
    assert "attempt.forceRefresh = false" in panel
    assert "state.retryAttempt = attempt" in panel
    assert "input.value = attempt.text" in panel
    assert "error instanceof TypeError" in panel
    assert 'problem.retryable === true' in panel
    assert 'problem.retryable === false' in panel
    assert "d.client_turn_id !== attempt.clientTurnId" in panel
    assert "opts.getContext(), freshBearerHeaders(refreshRequest)" in panel
    assert "attempt.initialHeaders = null" in panel
    assert "initialHeaders: pair[1]" in panel


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


def test_initial_v1_context_is_refs_only_and_raw_clinical_bridges_are_absent():
    panel = _panel_source()

    assert '"clientId", "thread_ref", "context_snapshot_ref", "digest_ref"' in panel
    assert "meta[key] = context[key]" in panel
    assert "Object.assign(meta, ctx)" not in panel
    assert "...ctx" not in panel
    request_builder = panel[
        panel.index("var meta = { output_format: fmt.value }"):
        panel.index("var clientTurnId = freshClientTurnId()")
    ]
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
            unknown="forbidden",
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


def test_demo_surfaces_are_inert_named_beta_boundaries():
    for path in DEMO_PATHS:
        source = path.read_text()
        normalized = source.casefold()

        assert "named beta" in normalized
        assert "unavailable" in normalized
        assert "<form" not in normalized
        assert "<textarea" not in normalized
        assert "fetch(" not in source
        assert "CosmaNeuraPanel.mount" not in source


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
