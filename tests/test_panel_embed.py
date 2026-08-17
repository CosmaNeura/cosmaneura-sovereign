"""Versioned panel delivery and cross-record browser-safety regressions."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = ROOT / "app" / "panel.js"
DEMO_PATH = ROOT / "app" / "demo_dashboard.html"


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


def test_welcome_sends_no_persona_and_drives_mount_specific_ux():
    panel = _panel_source()

    assert 'fetch(url + "/welcome", {' in panel
    assert '"/welcome?' not in panel
    assert "opts.persona" not in panel
    assert 'state.persona = w.persona === "provider"' in panel
    assert "persona: requestPersona" in panel


def test_sensitive_response_hides_welcome_disclaimer_and_guided_chrome():
    panel = _panel_source()

    # There is no permanent disclaimer stapled beneath every response.
    assert "Educational information — not medical advice." not in panel
    assert "cnp-disc" not in panel
    # The ordinary welcome notice remains, but disappears with the chips as
    # soon as a sensitive-disclosure response is rendered.
    assert "cnp-welcome-disc" in panel
    assert 'setSensitiveChrome(d.intent === "sensitive_disclosure")' in panel
    assert "notice.hidden = sensitive" in panel
    assert "chips.hidden = sensitive" in panel


def test_record_switch_uses_generation_abort_and_record_bound_saves():
    panel = _panel_source()

    assert "generation: 0" in panel
    assert "var generation = ++state.generation" in panel
    assert "var recordController = new AbortController()" in panel
    assert "state.recordController = recordController" in panel
    assert "signal: recordController.signal" in panel
    assert "controller = new AbortController()" in panel
    assert "signal: controller.signal" in panel
    assert "abort(state.chatController); abort(state.recordController)" in panel
    assert "if (!currentGeneration(generation)) return" in panel
    assert "var loadedGeneration = await loadThreadFor(contextRecordId)" in panel
    assert "generation = loadedGeneration" in panel
    assert "loadedGeneration == null" in panel
    assert "state.clientId !== contextRecordId" in panel
    assert 'thread.innerHTML = ""; chips.innerHTML = ""' in panel
    assert "state.switching = true" in panel
    assert "input.disabled = disabled" in panel
    assert "opts.saveHistory(recordId, historySnapshot)" in panel
    assert "Promise.resolve().then(function ()" in panel
    assert "Promise.resolve(opts.saveHistory" not in panel
    assert "opts.saveHistory(state.clientId" not in panel
    assert "opts.onSave(d.answer || \"\", d, recordId)" in panel


def test_destroy_aborts_work_and_removes_record_tooltips():
    panel = _panel_source()

    assert "destroy: destroy" in panel
    assert "state.destroyed = true" in panel
    assert "abort(state.welcomeController)" in panel
    assert "clearOwnedTips()" in panel
    assert "clearEventHandlers()" in panel
    assert "removeEventListener(item.event, item.handler)" in panel
    assert "node.onclick = null" in panel
    assert 'state.history = []; state.clientId = null' in panel
    assert 'root.innerHTML = ""' in panel
    assert "tipHost.appendChild(tip); ownedTips.push(tip)" in panel
    assert "root.remove(); style.remove()" in panel


def test_context_allowlist_and_sandbox_marking_survive_embed_hardening():
    panel = _panel_source()

    assert '"record_ref", "analyte"' in panel
    assert "meta[k] = ctx[k]" in panel
    assert "Object.assign(meta, ctx)" not in panel
    assert "...ctx" not in panel
    assert 'w.labs_tier === "sandbox"' in panel
    assert 'd.structured.tier === "sandbox"' in panel
    assert "d.structured.sandbox_label" in panel
    assert 'sandboxBadge.textContent = "Environment not verified"' in panel
    assert "sandboxBadge.hidden = false" in panel
    assert "applyEnvironmentLabel(w)" in panel
    assert 'path.intent === "lab_review"' in panel
    assert 'w.persona === "patient"' in panel
    assert 'w.persona === "provider"' in panel
    assert "!hasLabsPath && !w.labs_tier && !w.labs_label" in panel


def test_dashboard_demo_pins_version_and_binds_initial_and_changed_records():
    demo = DEMO_PATH.read_text()

    assert '<script src="/embed/v1/panel.js"></script>' in demo
    assert "panel.setRecord(r.id);" in demo
    assert "panel.setRecord(currentId);" in demo
    assert 'persona: "provider"' not in demo
    assert "ctx.record_ref = r.record_ref" not in demo
    assert "static demo deliberately cannot mint one" in demo
    assert "ctx.patient_ref" not in demo
