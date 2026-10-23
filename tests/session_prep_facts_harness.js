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
async function fixture(mode, observations) {
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
    f.projection = projection(JSON.parse(options.body).client_turn_id, f.mode);
    if (observations) {
      f.projection.structured_facts.facts = JSON.parse(JSON.stringify(observations));
      f.projection.structured_facts.source_dates = [...new Set(observations.flatMap(fact =>
        [fact.note_date, ...(fact.correction ? [fact.correction.note_date] : [])]))];
    }
    f.originalProjection = JSON.stringify(f.projection);
    return response(200, f.projection);
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
function factTable(f, caption) {
  const table = find(f.view, node => node.tagName === "table" &&
    node.children.some(child => child.tagName === "caption" && child.textContent === caption));
  assert(table, "expected observation table missing: " + caption);
  const body = table.children.find(child => child.tagName === "tbody");
  return { table, rows: body.children.filter(row => row.children.length === 3) };
}
function evidence(f, table, observations, correctionLabels = {}) {
  const actual = table.querySelectorAll("*").filter(node => node.tagName === "details")
    .map(node => node.children.map(child => child.textContent));
  const expected = observations.map(fact => ["Source evidence · " + fact.section, fact.quote,
    ...(fact.correction ? ["Correction · " + (correctionLabels[fact.correction.note_date] || fact.correction.note_date.slice(0, 10)) + " · " + fact.correction.section,
      fact.correction.quote] : [])]);
  assert(JSON.stringify(actual.map(JSON.stringify).sort()) === JSON.stringify(expected.map(JSON.stringify).sort()),
    "a source section, literal quote or correction disclosure was lost or rewritten");
  assert(!find(table, node => ["a", "img", "script"].includes(node.tagName)), "source evidence became active markup");
  assert(JSON.stringify(f.projection) === f.originalProjection, "display grouping changed the source payload");
}
const order = { kind: "order", subject: "creatinine", value: null, state: "result_not_documented", setting: null,
  note_date: "2026-08-12T12:00:00Z", section: "Labs", quote: "No creatinine results are documented.", correction: null };
const measurement = { kind: "vital", subject: "blood pressure", value: "128-136/78-84", state: "measured", setting: "home",
  note_date: order.note_date, section: "Objective", quote: "Home blood pressure 128-136/78-84.", correction: null };
const correction = { note_date: "2026-08-19T12:00:00Z", section: "Correction", quote: "Prior observation was corrected." };
async function distinct(first, change, caption, message, correctionLabels) {
  const observations = [first, { ...first, ...change, section: "Other source", quote: "Exact synthetic second quotation." }];
  const f = await fixture("ready", observations);
  const { table, rows } = factTable(f, caption);
  assert(rows.length === 2, message);
  evidence(f, table, observations, correctionLabels); f.end();
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
  {
    const dates = ["2026-08-12T12:00:00Z", "2026-08-12T12:00:00.125Z",
      "2026-08-12T12:00:00.250+02:00", "2026-08-12T12:00:00+00:00"];
    const labels = ["2026-08-12 12:00:00 UTC", "2026-08-12 12:00:00.125 UTC",
      "2026-08-12 12:00:00.250 +02:00", "2026-08-12 12:00:00 +00:00"];
    const observations = dates.map(note_date => ({ ...order, note_date }));
    const f = await fixture("ready", observations), { table, rows } = factTable(f, "Order observations");
    assert(JSON.stringify(rows.map(row => row.children[2].textContent)) === JSON.stringify(labels),
      "distinct supplied note timestamps have indistinguishable visible row labels");
    const notice = find(f.view, node => node.className === "cnp-facts-notice");
    assert(notice.textContent.includes("Note dates: " + labels.join(", ") + "."), "note-date notice discarded supplied times or offsets");
    evidence(f, table, observations); f.end();
  }
  {
    const dates = ["2026-08-19T12:00:00Z", "2026-08-19T12:00:00.125Z", "2026-08-19T12:00:00+02:00"];
    const labels = ["2026-08-19 12:00:00 UTC", "2026-08-19 12:00:00.125 UTC", "2026-08-19 12:00:00 +02:00"];
    const observations = dates.map(note_date => ({ ...order, correction: { ...correction, note_date } }));
    const f = await fixture("ready", observations), { table, rows } = factTable(f, "Order observations");
    assert(rows.every(row => row.children[2].textContent === "2026-08-12"), "noncolliding observation dates became verbose");
    assert(JSON.stringify(rows.map(row => row.children[1].children[0].textContent)) ===
      JSON.stringify(labels.map(label => "Superseded by correction dated " + label)), "correction badges lost time/offset distinctions");
    const notice = find(f.view, node => node.className === "cnp-facts-notice");
    assert(notice.textContent.includes("Note dates: 2026-08-12, " + labels.join(", ") + "."), "correction dates missing from notice");
    evidence(f, table, observations, Object.fromEntries(dates.map((date, index) => [date, labels[index]]))); f.end();
  }
  {
    const corrected = { ...order, correction: { ...correction, note_date: "2026-08-12T12:01:00Z" } };
    const f = await fixture("ready", [corrected]), { table, rows } = factTable(f, "Order observations");
    assert(rows[0].children[2].textContent === "2026-08-12 12:00:00 UTC", "same-day note/correction collision did not expand the note date");
    assert(rows[0].children[1].children[0].textContent === "Superseded by correction dated 2026-08-12 12:01:00 UTC",
      "same-day correction badge stayed ambiguous");
    evidence(f, table, [corrected], { "2026-08-12T12:01:00Z": "2026-08-12 12:01:00 UTC" }); f.end();
  }
  {
    const observations = [order, { ...order, section: "Other source", quote: "Exact duplicate-timestamp proof." }];
    const f = await fixture("ready", observations), { table, rows } = factTable(f, "Order observations");
    assert(rows.length === 1 && rows[0].children[2].textContent === "2026-08-12", "one distinct timestamp did not retain the concise date");
    assert(find(f.view, node => node.className === "cnp-facts-notice").textContent.endsWith("Note dates: 2026-08-12."),
      "ordinary note-date notice changed");
    evidence(f, table, observations); f.end();
  }
  {
    const second = { ...order, section: "Objective", quote: "Creatinine results are not documented. **Literal** <img src=x>" };
    const potassium = { ...order, subject: "potassium", quote: "No potassium results are documented." };
    const observations = [order, potassium, second];
    const f = await fixture("ready", observations), { table, rows } = factTable(f, "Order observations");
    assert(rows.length === 2 && rows[0].children[0].textContent === "creatinine" &&
      rows[1].children[0].textContent === "potassium", "duplicate orders remain or first-observation order changed");
    assert(table.children.find(child => child.tagName === "tbody").children[1].children[0].children.length === 2,
      "joined order did not retain both independently expandable source proofs");
    evidence(f, table, observations); f.end();
  }
  {
    const observations = [measurement, { ...measurement, section: "Subjective", quote: "Exact second home measurement quotation." }];
    const f = await fixture("pending", observations), { table, rows } = factTable(f, "Measurements");
    assert(rows.length === 1, "identical measurements did not join while the brief was pending");
    evidence(f, table, observations);
    f.panel.invalidateSessionPrep(f.record);
    assert(!f.has(measurement.value), "source advance retained joined measurements"); f.end();
  }
  for (const state of ["cancelled", "not_ordered", "order_not_documented", "ordered", "resulted"])
    await distinct(order, { state }, "Order observations", "distinct order status or negation was joined: " + state);
  await distinct(order, { note_date: "2026-08-12T12:01:00Z" }, "Order observations", "same-day distinct note timestamps were joined");
  await distinct(order, { correction }, "Order observations", "corrected and uncorrected observations were joined");
  for (const change of [{ note_date: "2026-08-19T12:01:00Z" }, { section: "Another correction section" },
    { quote: "Different exact correction quotation." }]) {
    const labels = change.note_date ? { "2026-08-19T12:00:00Z": "2026-08-19 12:00:00 UTC",
      "2026-08-19T12:01:00Z": "2026-08-19 12:01:00 UTC" } : {};
    await distinct({ ...order, correction }, { correction: { ...correction, ...change } }, "Order observations",
      "distinct correction identity was joined", labels);
  }
  {
    const observations = [{ ...order, correction }, { ...order, correction: { ...correction },
      section: "Objective", quote: "Exact second corrected source quotation." }];
    const f = await fixture("ready", observations), { table, rows } = factTable(f, "Order observations");
    assert(rows.length === 1 && rows[0].children[1].children[0].textContent.includes("2026-08-19"),
      "exact correction identity did not join or visible correction date was lost");
    evidence(f, table, observations); f.end();
  }
  for (const setting of ["office", "unspecified"])
    await distinct(measurement, { setting }, "Measurements", "measurement setting distinction was joined");
  for (const value of ["148/92", "128 and 136/78 to 84"])
    await distinct(measurement, { value }, "Measurements", "different measurement value or wording was normalized");
  await distinct(order, { subject: "Creatinine" }, "Order observations", "source observation spelling was normalized");
  await distinct({ ...order, subject: "a b", value: "c" }, { subject: "a", value: "b c" },
    "Order observations", "equal display strings conflated distinct subject/value identities");
  {
    const medication = { ...order, kind: "medication", subject: "losartan", value: "25 mg", state: "planned" };
    await distinct(medication, {}, "Medication observations", "medication rows were grouped");
  }
  assert(timers.size === 0, "destroy leaked polling timers");
  console.log("Dated facts/grouping and visible exact same-day note/correction times, fractions/offsets, concise normal dates, literal proofs, immutable payload and freshness passed.");
}
main().catch(error => { console.error(error); process.exit(1); });
