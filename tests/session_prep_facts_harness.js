"use strict";
const fs = require("fs"), vm = require("vm"), path = require("path");
const { FakeElement, assert, find, response } = require("./panel_contract_harness");
const facts = JSON.parse(fs.readFileSync(path.join(__dirname, "../contracts/fixtures/provider-session-prep-facts-v1.1/facts.json")));
let timer = 0;
const timers = new Set();
global.setTimeout = () => { timers.add(++timer); return timer; };
global.clearTimeout = id => timers.delete(id);
global.document = Object.assign(new FakeElement("document"), {
  hidden: false, body: new FakeElement("body"), head: new FakeElement("head"),
  createElement: tag => new FakeElement(tag),
});
let uuid = 1;
global.window = {
  innerWidth: 380, location: { origin: "https://host.example" },
  crypto: { randomUUID: () => `00000000-0000-4000-8000-${String(uuid++).padStart(12, "0")}` },
};
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
function projection(turn, mode) {
  const corrections = new Map();
  facts.facts.forEach(fact => fact.supersedes.forEach(ref => corrections.set(ref, fact.correction_evidence)));
  const content = mode === "ready" || mode === "legacy";
  const body = {
    client_turn_id: turn, state: content ? "ready" : mode === "unavailable" ? "unavailable" : "pending",
    freshness: content ? "current" : "none", current_through: content ? "2026-09-02T12:00:00Z" : null,
    source_count: content ? 3 : 0, prepared_brief: content ? {
      recap: "Synthetic brief", trajectory_and_material_changes: [], talking_points: ["Review interval changes"],
      open_threads: [], cautions: [], suggested_outline: ["Review prior plans"], patient_reported: null,
    } : null,
    retry_after_seconds: content || mode === "unavailable" ? null : 5,
    reason_code: content ? null : mode === "unavailable" ? "context_not_available" : "generation_pending",
  };
  if (mode === "legacy") return body;
  body.schema_version = "1.1.0"; body.facts_state = "ready";
  body.structured_facts = {
    coverage: facts.coverage, source_dates: facts.sources.map(source => source.source_at),
    selection_limited: false, facts_limited: false,
    facts: facts.facts.map(fact => {
      const correction = corrections.get(fact.fact_ref);
      return {
        kind: fact.kind, subject: fact.subject, value: fact.value, state: fact.state, setting: fact.setting,
        note_date: fact.evidence.source_at, section: fact.evidence.section, quote: fact.evidence.quote,
        correction: correction ? { note_date: correction.source_at, section: correction.section, quote: correction.quote } : null,
      };
    }),
  };
  if (mode === "zero") body.structured_facts.facts = [];
  if (mode === "malformed") body.structured_facts.facts[0].state = "confirmed_taking";
  if (mode === "literal") body.structured_facts.facts[0].quote += " **Keep this literal** [external](https://example.invalid)";
  return body;
}
async function fixture(mode) {
  const f = { mode, record: "record_a", requests: [] };
  global.fetch = async (url, options) => {
    if (url.endsWith("/welcome")) return response(200, {
      partner_name: "Synthetic", persona: "provider", greeting: "Hello",
      paths: [{ label: "Open Session Prep", message: "Open Session Prep", intent: "session_prep" }],
      disclaimer: "Synthetic", emergency_note: "Call 911 in an emergency.", walkthrough_mode: "local_synthetic",
    });
    assert(url.endsWith("/session-prep/structured-facts"), "panel did not opt into additive facts route");
    assert(Object.keys(JSON.parse(options.body)).join() === "client_turn_id", "browser selected private authority");
    f.requests.push(options);
    return response(200, projection(JSON.parse(options.body).client_turn_id, f.mode));
  };
  f.panel = window.CosmaNeuraPanel.mount({ sovereignUrl: "https://sovereign.example", embedVersion: "1.0",
    getAuthToken: async () => "opaque-token-synthetic-abcdefghijklmnop",
    getContext: async () => ({ clientId: f.record, thread_ref: "thread_synthetic" }),
    onHumanContact: async () => {}, loadHistory: async () => null,
  });
  await f.panel.setRecord(f.record); f.panel.open();
  await f.panel.openSessionPrep();
  f.view = f.panel.root.querySelector(".cnp-prep-view");
  f.has = text => !!find(f.view, node => node.textContent.includes(text));
  f.end = () => f.panel.destroy();
  return f;
}
async function main() {
  for (const mode of ["ready", "pending", "unavailable"]) {
    const f = await fixture(mode);
    assert(f.has("Medication observations") && f.has("Order observations") && f.has("Measurements"), "dated tables missing: " + mode);
    assert(f.has("Superseded by correction dated 2026-08-19") && f.has("2026-08-12"), "correction dates overwritten");
    assert(f.has("148/92 mmHg") && f.has("128–136/78–84 mmHg"), "office/home values changed");
    assert(f.has("Source evidence") && f.has("supersedes the previous 50 mg plan"), "exact correction evidence missing");
    assert(!f.has("No clinical content is available yet"), "source-ready state hidden by pending brief");
    assert(!find(f.view, node => node.tagName === "a"), "invented source link");
    f.panel.invalidateSessionPrep(f.record);
    assert(!f.has("148/92 mmHg"), "source change retained stale fact rows");
    await f.panel.openSessionPrep();
    f.record = "record_b"; await f.panel.setRecord(f.record);
    assert(!f.has("148/92 mmHg"), "record switch retained another patient's facts"); f.end();
  }
  { const f = await fixture("zero"); assert(f.has("does not establish absence from the chart"), "empty facts infer absence"); f.end(); }
  { const f = await fixture("malformed"); assert(!f.has("148/92 mmHg") && !f.has("Medication observations"), "malformed facts rendered"); f.end(); }
  { const f = await fixture("legacy"); assert(f.has("Synthetic brief") && !f.has("Medication observations"), "v1 brief compatibility lost"); f.end(); }
  { const f = await fixture("literal"); assert(f.has("**Keep this literal** [external](https://example.invalid)"), "quotation became markdown"); f.end(); }
  assert(timers.size === 0, "destroy leaked polling timers");
  console.log("Dated facts ready/pending/unavailable, corrections, literal evidence and context freshness passed.");
}
main().catch(error => { console.error(error); process.exit(1); });
