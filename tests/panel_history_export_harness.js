"use strict";

// Exercise saved-answer export through the real public panel mount and history reader.
const fs = require("fs");
const vm = require("vm");
const { FakeElement, assert, find, response } = require("./panel_contract_harness");

const ANSWER = "# Fictional saved answer\r\n\r\nExact café 🩺 text & <draft>.\n" +
  "Sources: words in the answer are not saved citation metadata.\n";
const TURN_ID = "11111111-1111-4111-8111-111111111111";
const STANDARD = { mode: "standard", suppress: [], human_contact: null };
const SUPPRESSIONS = ["disclaimer", "intent_badge", "doctrinal_badge", "citations",
  "structured_cards", "feature_actions", "guided_paths"];

function history(status = "completed", presentation = STANDARD, id = TURN_ID) {
  const common = { turn_ref: "turn_fictional_export", client_turn_id: id,
    recorded_at: "2026-10-23T12:00:00Z" };
  const messages = [{ ...common, role: "user", content: "Draft a hypothetical ethics consult document.",
    presentation: null }];
  if (status === "completed") messages.push({ ...common, role: "assistant", content: ANSWER, presentation });
  return { schema_version: "1.0.0",
    history: { thread_ref: "thread_fictional_export", messages, next_cursor: null },
    turns: [{ ...common, state: status, outcome: status === "completed" ? "completed" : null }] };
}

async function main() {
  const downloads = [], blobs = [], requests = [];
  global.document = Object.assign(new FakeElement("document"), {
    body: new FakeElement("body"), head: new FakeElement("head"),
    createElement: tag => {
      const element = new FakeElement(tag);
      if (tag === "a") element.onclick = () => downloads.push({ name: element.download, href: element.href });
      return element;
    },
    createElementNS: (_namespace, tag) => new FakeElement(tag),
  });
  let uuid = 1, welcomeDenied = false, chatMode = "success";
  global.window = { innerWidth: 1200, location: { origin: "https://host.example" },
    crypto: { randomUUID: () => `00000000-0000-4000-8000-${String(uuid++).padStart(12, "0")}` } };
  URL.createObjectURL = blob => { blobs.push(blob); return `blob:fictional-export-${blobs.length}`; };
  global.fetch = async (url, options) => {
    requests.push({ url, options });
    if (url.endsWith("/welcome")) return response(welcomeDenied ? 403 : 200, {
      persona: "provider", greeting: "Hello", paths: [], disclaimer: "Fictional disclaimer",
    });
    if (url.endsWith("/embed/history")) {
      const body = JSON.parse(options.body);
      return response(200, history("completed", STANDARD, body.client_turn_id));
    }
    assert(url.endsWith("/chat"), "unexpected request");
    if (chatMode === "revoked") return response(403, {});
    if (chatMode === "lost-final") return response(503, {
      type: "https://contracts.cosmaneura.com/problems/service_unavailable",
      title: "Service unavailable", status: 503, code: "SERVICE_UNAVAILABLE", detail: "Unverified response",
      trace_id: "trace_fictional_export", retryable: false,
    });
    const body = JSON.parse(options.body);
    return response(200, { answer: ANSWER, intent: "ethics_consult", client_turn_id: body.client_turn_id,
      presentation: STANDARD, citations: [], doctrinal_review: null,
      structured: { output_format: body.metadata.output_format }, safety_notice: null });
  };
  vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));

  async function mount(envelope = history()) {
    const state = { record: "record_fictional_export", envelope };
    state.panel = window.CosmaNeuraPanel.mount({
      sovereignUrl: "https://sovereign.example", embedVersion: "1.0",
      getAuthToken: async () => "opaque-fictional-export-token-abcdefghijkl",
      getContext: async () => ({ clientId: state.record }), onHumanContact: async () => {},
      loadHistory: async () => state.envelope,
    });
    await state.panel.setRecord(state.record);
    return state;
  }
  const saveButton = panel => find(panel.root, node => node.textContent === "Save answer .md");
  const originalDownload = panel => find(panel.root, node => node.textContent === "Download .md");
  async function checkExactDownload(button, expectedName) {
    const beforeRequests = requests.length, beforeBlobs = blobs.length;
    await button.click();
    assert(blobs.length === beforeBlobs + 1, "export did not create exactly one Blob");
    const blob = blobs.at(-1);
    assert(blob.type === "text/markdown", "export media type changed");
    assert(Buffer.from(await blob.arrayBuffer()).equals(Buffer.from(ANSWER, "utf8")),
      "export changed exact canonical UTF-8 answer bytes");
    assert(downloads.at(-1).name === expectedName, "export filename implies missing original metadata");
    assert(requests.length === beforeRequests, "export issued a network or model request");
  }

  const restored = await mount();
  assert(saveButton(restored.panel) && !originalDownload(restored.panel),
    "restored answer was not distinguished from an original Document response");
  assert(!find(restored.panel.root, node => ["cnp-cite", "cnp-trust", "cnp-badge"].includes(node.className)),
    "history invented citation, verdict or intent metadata");
  await checkExactDownload(saveButton(restored.panel), "cosmaneura-saved-answer.md");
  assert(requests.every(request => request.url.endsWith("/welcome")), "hydration ran chat/model requests");
  assert(restored.panel.root.innerHTML.includes("a visit summary or an ethics consult") &&
    !restored.panel.root.innerHTML.includes("a draft from this record"), "Document helper misstates supported drafts");
  const format = restored.panel.root.querySelector(".cnp-fmt");
  format.value = "document";
  format._listeners.find(([event]) => event === "change")[1]();
  assert(!restored.panel.root.querySelector(".cnp-format-help").hidden &&
    restored.panel.root.querySelector(".cnp-in").placeholder === "Request a supported document draft…",
    "Document help or placeholder still imposes a chart-only request");
  restored.panel.destroy();

  for (const presentation of [
    { ...STANDARD, suppress: ["feature_actions"] },
    { mode: "emergency", suppress: [], human_contact: null },
    { mode: "sensitive_disclosure", suppress: SUPPRESSIONS, human_contact: {
      label: "Contact care team", action_id: "care-team-contact", fallback_text: "Contact your care team." } },
  ]) {
    const denied = await mount(history("completed", presentation));
    assert(!saveButton(denied.panel) && !originalDownload(denied.panel), "governed suppression exposed export");
    denied.panel.destroy();
  }
  for (const status of ["pending", "stale"]) {
    const incomplete = await mount(history(status));
    assert(!saveButton(incomplete.panel) && !originalDownload(incomplete.panel), "pending/stale history exposed export");
    incomplete.panel.destroy();
  }

  const switching = await mount();
  const oldSave = saveButton(switching.panel), beforeSwitch = blobs.length;
  switching.record = "record_fictional_other"; switching.envelope = null;
  const switched = switching.panel.setRecord(switching.record);
  await oldSave.click();
  await switched;
  await oldSave.click();
  assert(blobs.length === beforeSwitch && !saveButton(switching.panel), "retained prior-record action exported history");
  switching.panel.destroy();

  const revoked = await mount();
  const revokedSave = saveButton(revoked.panel), beforeRevoke = blobs.length;
  chatMode = "revoked";
  revoked.panel.root.querySelector(".cnp-in").value = "A fictional follow-up";
  await revoked.panel.root.querySelector(".cnp-send").click();
  await revokedSave.click();
  assert(blobs.length === beforeRevoke && !saveButton(revoked.panel) &&
    !revoked.panel.root.querySelector(".cnp-gate").hidden, "revocation left saved-answer export active");
  revoked.panel.destroy();

  const bootstrapDenied = await mount();
  const deniedSave = saveButton(bootstrapDenied.panel), beforeDenied = blobs.length;
  welcomeDenied = true;
  await bootstrapDenied.panel.setRecord(bootstrapDenied.record);
  await deniedSave.click();
  assert(blobs.length === beforeDenied && !saveButton(bootstrapDenied.panel), "failed authorization retained export");
  bootstrapDenied.panel.destroy(); welcomeDenied = false;

  const recovery = await mount(null);
  chatMode = "lost-final";
  recovery.panel.root.querySelector(".cnp-in").value = "A fictional document request";
  await recovery.panel.root.querySelector(".cnp-send").click();
  const chatCount = requests.filter(request => request.url.endsWith("/chat")).length;
  recovery.panel.root.querySelector(".cnp-in").value = "Preserve my newer draft";
  await find(recovery.panel.root, node => node.textContent === "Check response").click();
  assert(saveButton(recovery.panel) && !originalDownload(recovery.panel), "response recovery lost exact saved-answer export");
  await checkExactDownload(saveButton(recovery.panel), "cosmaneura-saved-answer.md");
  assert(requests.filter(request => request.url.endsWith("/chat")).length === chatCount &&
    recovery.panel.root.querySelector(".cnp-in").value === "Preserve my newer draft", "recovery replayed model or replaced draft");
  const destroyedSave = saveButton(recovery.panel), beforeDestroy = blobs.length;
  recovery.panel.destroy(); await destroyedSave.click();
  assert(blobs.length === beforeDestroy, "destroyed panel retained export action");

  // Original live Document behavior remains distinct; ordinary Chat is not a saved-answer export.
  chatMode = "success";
  for (const selected of ["chat", "document"]) {
    const live = await mount(null);
    live.panel.root.querySelector(".cnp-fmt").value = selected;
    live.panel.root.querySelector(".cnp-in").value = "A fictional supported draft";
    await live.panel.root.querySelector(".cnp-send").click();
    assert(!saveButton(live.panel) && Boolean(originalDownload(live.panel)) === (selected === "document"),
      "live Chat/Document output was mislabeled as restored history");
    if (selected === "document") await checkExactDownload(originalDownload(live.panel), "cosmaneura-ethics_consult.md");
    live.panel.destroy();
  }
  process.stdout.write("Saved-answer export: exact bytes, recovery, suppression and record/access guards passed.\n");
}
main().catch(error => { process.stderr.write(String(error.stack || error) + "\n"); process.exitCode = 1; });
