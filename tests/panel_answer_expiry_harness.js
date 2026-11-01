"use strict";

// Synthetic clocks and transport exercise the real public mount. No production
// timer, schema validator, export builder or lifecycle logic is reproduced here.
const fs = require("fs");
const vm = require("vm");
const { FakeElement, assert, find, response } = require("./panel_contract_harness");

const TURN_ID = "11111111-1111-4111-8111-111111111111";
const STANDARD = { mode: "standard", suppress: [], human_contact: null };
const SUPPRESSIONS = ["disclaimer", "intent_badge", "doctrinal_badge", "citations",
  "structured_cards", "feature_actions", "guided_paths"];
const ANSWER = "Selected reviewed-note excerpts for your question:\n\n" +
  "[1] 2026-10-08 — Medications\n\n" +
  "> Fictional-dose-marker: 25 mg daily replaces the earlier erroneous 50 mg instruction.\n" +
  "> Exact café 🩺 text & <draft>.\n\n" +
  "Source: 1 selected reviewed prior note through 2026-10-08.\n";
const RECORDED_AT = "2026-10-20T12:00:00.123456Z";
const SERVER_AT = Date.parse("2026-11-01T12:00:00Z");
const CITATION = { citation_ref: "conversation_final_phase_01abcdefghjkmnpqrstvwxyz12.1", ordinal: 1,
  source_at: "2026-10-08T14:33:21Z", reviewed_at: "2026-10-09T08:11:23.456Z", section: "Medications" };

function htmlText(value) {
  return value.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'").replace(/&amp;/g, "&");
}
class FixtureElement extends FakeElement {
  get firstChild() { return this.children[0] || null; }
  insertBefore(child, before) {
    child.parentNode = this;
    const index = this.children.indexOf(before);
    if (index < 0) this.children.push(child); else this.children.splice(index, 0, child);
    return child;
  }
  set innerHTML(value) {
    super.innerHTML = value;
    // The shared fake DOM intentionally does not parse HTML. Materialize only
    // real evidence elements needed to inspect citation actions from md().
    for (const match of String(value).matchAll(/<section class="cnp-evidence-card" data-citation-ordinal="(\d+)"[^>]*><h4 class="cnp-evidence-source">([\s\S]*?)<\/h4>/g)) {
      const card = new FixtureElement("section"); card.className = "cnp-evidence-card";
      card.setAttribute("data-citation-ordinal", match[1]);
      const heading = new FixtureElement("h4"); heading.className = "cnp-evidence-source";
      heading.textContent = htmlText(match[2]); card.appendChild(heading); this.appendChild(card);
    }
  }
  get innerHTML() { return super.innerHTML; }
}

function createEnvironment(panelPath) {
  let mono = 0, elapsed = 0, wall = Date.parse("2099-12-31T23:59:59Z"), nextTimer = 0, uuid = 1;
  const timers = new Map(), blobs = [], downloads = [];
  const flush = () => new Promise(resolve => setImmediate(resolve));
  global.setTimeout = (callback, delay) => {
    timers.set(++nextTimer, { callback, at: mono + Math.max(0, Number(delay) || 0) });
    return nextTimer;
  };
  global.clearTimeout = id => timers.delete(id);
  Date.now = () => wall;
  Object.defineProperty(global, "performance", { configurable: true, value: { now: () => mono } });
  global.document = Object.assign(new FixtureElement("document"), {
    body: new FixtureElement("body"), head: new FixtureElement("head"), hidden: false,
    createElement: tag => {
      const element = new FixtureElement(tag);
      if (tag === "a") element.onclick = () => downloads.push({ name: element.download, href: element.href });
      return element;
    },
    createElementNS: (_namespace, tag) => new FixtureElement(tag),
  });
  global.window = Object.assign(new FixtureElement("window"), { innerWidth: 1200, location: { origin: "https://host.example" },
    performance: global.performance,
    crypto: { randomUUID: () => `00000000-0000-4000-8000-${String(uuid++).padStart(12, "0")}` } });
  URL.createObjectURL = blob => { blobs.push(blob); return `blob:synthetic-timed-${blobs.length}`; };
  URL.revokeObjectURL = () => {};
  vm.runInThisContext(fs.readFileSync(panelPath, "utf8"));
  const env = { blobs, downloads, timers, flush, now: () => mono, elapsed: () => elapsed,
    // Mac sleep advances server/wall time while the monotonic clock and its
    // timers pause. No visibility event or production fence is simulated here.
    sleep: ms => { elapsed += ms; wall += ms; },
    setWall: value => { wall = value; },
    windowEvent: async event => {
      for (const [name, listener] of [...window._listeners])
        if (name === event) listener({ persisted: event === "pageshow" });
      await flush();
    },
    visibility: async hidden => {
      document.hidden = hidden;
      for (const [event, listener] of [...document._listeners])
        if (event === "visibilitychange") listener();
      await flush();
    },
    advance: async ms => {
      const target = mono + ms;
      let callbacks = 0;
      while (true) {
        const next = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
        if (!next || next[1].at > target) break;
        assert(++callbacks < 1000, "virtual timer did not settle");
        const step = next[1].at - mono;
        mono = next[1].at; elapsed += step; wall += step;
        timers.delete(next[0]); next[1].callback(); await flush();
      }
      const step = target - mono;
      mono = target; elapsed += step; wall += step; await flush();
    },
  };
  return env;
}

function legacyHistory(id = TURN_ID, status = "completed", presentation = STANDARD, answer = ANSWER) {
  const common = { turn_ref: "turn_synthetic_timed", client_turn_id: id, recorded_at: RECORDED_AT };
  const messages = [{ ...common, role: "user", content: "A fictional reviewed-note question.", presentation: null }];
  if (status === "completed") messages.push({ ...common, role: "assistant", content: answer, presentation });
  return { schema_version: "1.0.0", history: { thread_ref: "thread_synthetic_timed", messages, next_cursor: null },
    turns: [{ ...common, state: status, outcome: status === "completed" ? "completed" : null }] };
}

async function fixture(env, options = {}) {
  const f = { start: env.elapsed(), record: "record_synthetic_timed", digest: "digest_synthetic_timed",
    turnId: TURN_ID, answer: ANSWER, presentation: STANDARD, state: "completed", lifetime: 60000,
    displayMode: "success", chatMode: "success", chatIntent: "visit_prep", contextMode: "ready",
    authMode: "ready", welcomeDenied: false, requests: [], contextCalls: 0, authCalls: 0,
    hostHistoryLoads: 0, hasTurn: true, initial: legacyHistory(TURN_ID, "completed", STANDARD, "Legacy-unverified-answer-marker"),
    ...options };
  f.observed = () => new Date(SERVER_AT + env.elapsed() - f.start).toISOString();
  f.expires = () => new Date(SERVER_AT + f.lifetime).toISOString();
  f.envelope = () => {
    const value = legacyHistory(f.turnId, f.state, f.presentation, f.answer);
    value.schema_version = "1.3.0"; value.observed_at = f.observed();
    for (const message of value.history.messages) {
      message.reviewed_citations = message.role === "assistant" ? (f.citations ?? [CITATION]) : null;
      message.display_state = message.role === "assistant" ? (f.displayState || "historical") : null;
      message.expires_at = message.role === "assistant" ? f.expires() : null;
    }
    if (f.mutate) f.mutate(value);
    return value;
  };
  f.hold = kind => new Promise((resolve, reject) => { f[kind] = resolve; f[kind + "Reject"] = reject; });
  global.fetch = async (url, request) => {
    f.requests.push({ url, options: request });
    if (url.endsWith("/welcome")) return response(f.welcomeDenied ? 403 : 200, {
      persona: "provider", greeting: "Hello", paths: [], disclaimer: "Synthetic disclaimer",
    });
    if (url.endsWith("/embed/history-timed")) {
      const body = JSON.parse(request.body);
      assert(Object.keys(body).sort().join(",") === "client_turn_id,mode,schema_version" &&
        body.schema_version === "1.3.0" && ["recent", "turn"].includes(body.mode) &&
        (body.mode === "recent" ? body.client_turn_id === null : body.client_turn_id === f.turnId),
        "timed read was not closed1.3 recent/exact-turn");
      assert(request.method === "POST" && request.headers.authorization.startsWith("Bearer "),
        "timed read lacks fresh bearer transport");
      const value = body.mode === "recent" && !f.hasTurn ? {
        schema_version: "1.3.0", observed_at: f.observed(), turns: [],
        history: { thread_ref: "thread_synthetic_timed", messages: [], next_cursor: null },
      } : f.envelope();
      if (f.displayMode === "offline") throw new Error("Synthetic offline");
      if (f.displayMode === "denied") return response(403, {});
      if (f.displayMode === "unavailable") return response(503, {});
      if (f.displayMode === "missing") return response(404, {});
      if (f.displayMode === "hold-fetch") return f.hold("releaseFetch");
      const result = response(200, value);
      if (f.displayMode === "hold-body") result.json = async () => { await f.hold("releaseBody"); return value; };
      return result;
    }
    assert(url.endsWith("/chat"), "unexpected synthetic request " + url);
    if (f.chatMode === "denied") return response(403, {});
    const body = JSON.parse(request.body); f.turnId = body.client_turn_id; f.hasTurn = true;
    if (f.chatMode === "lost-final") return response(503, {
      type: "https://contracts.cosmaneura.com/problems/service_unavailable", title: "Service unavailable",
      status: 503, code: "SERVICE_UNAVAILABLE", detail: "Synthetic unverified response",
      trace_id: "trace_synthetic_timed", retryable: false,
    });
    return response(200, { answer: f.answer, intent: f.chatIntent, client_turn_id: f.turnId,
      presentation: f.presentation, citations: [], doctrinal_review: null,
      structured: { output_format: body.metadata.output_format }, safety_notice: null });
  };
  f.panel = window.CosmaNeuraPanel.mount({
    sovereignUrl: "https://sovereign.example", embedVersion: "1.0",
    getAuthToken: async () => {
      f.authCalls += 1;
      if (f.authMode === "hold") await f.hold("releaseAuth");
      return `opaque-synthetic-timed-${f.authCalls}-abcdefghijkl`;
    },
    getContext: async () => {
      f.contextCalls += 1;
      if (f.contextMode === "hold" || f.contextCalls === f.contextHoldAt) return f.hold("releaseContext");
      return { clientId: f.record, digest_ref: f.digest, thread_ref: "thread_synthetic_timed" };
    },
    loadHistory: async () => { f.hostHistoryLoads += 1; return f.initial; },
    onHumanContact: async () => {},
    citationHref: ref => "https://host.example/session-prep/citations/" + encodeURIComponent(ref),
  });
  await f.panel.setRecord(f.record); await f.panel.open(); await env.flush();
  f.input = f.panel.root.querySelector(".cnp-in");
  f.thread = f.panel.root.querySelector(".cnp-thread");
  f.button = text => find(f.thread, node => node.textContent === text);
  f.save = () => f.button("Save answer .md");
  f.download = () => f.button("Download .md");
  f.has = text => !!find(f.thread, node => node.textContent.includes(text) || node.innerHTML.includes(text));
  f.reads = () => f.requests.filter(request => request.url.endsWith("/embed/history-timed"));
  f.chats = () => f.requests.filter(request => request.url.endsWith("/chat"));
  f.ask = text => { f.input.value = text; return f.panel.root.querySelector(".cnp-send").click(); };
  f.end = async () => {
    f.panel.destroy(); await env.advance(0);
    assert(env.timers.size === 0, "answer/read timer leaked after destroy");
  };
  return f;
}

async function verifiedExport(env, f, button, expectedAnswer = f.answer, prefix = "cosmaneura-saved-answer-") {
  const beforeBlobs = env.blobs.length, beforeReads = f.reads().length, beforeChats = f.chats().length;
  await button.click();
  assert(env.blobs.length === beforeBlobs + 1, "verified export did not create exactly one Blob");
  assert(f.reads().length === beforeReads + 1 && f.chats().length === beforeChats,
    "export did not perform exactly one fresh display read or replayed inference");
  const read = f.reads().at(-1);
  assert(JSON.parse(read.options.body).mode === "turn", "export accepted a recent rather than exact-turn read");
  const exported = await env.blobs.at(-1).text();
  assert(env.blobs.at(-1).type === "text/markdown", "export media type changed");
  assert(exported.includes("Answer recorded: " + RECORDED_AT) &&
    exported.includes("Export verified: " + f.observed()) &&
    exported.includes("Available until: " + f.expires()), "export omitted authoritative answer/verification/expiry clocks");
  const canonicalOffset = exported.indexOf(expectedAnswer);
  assert(canonicalOffset >= 0 && Buffer.from(exported.slice(canonicalOffset, canonicalOffset + expectedAnswer.length), "utf8")
    .equals(Buffer.from(expectedAnswer, "utf8")), "export altered exact canonical UTF-8 answer bytes");
  assert(exported.indexOf("Export verified:") < canonicalOffset, "server export clocks were not separated from the canonical answer");
  if (f.citations === undefined || f.citations.length) assert(exported.includes(CITATION.source_at) &&
    exported.includes(CITATION.reviewed_at) && exported.includes(CITATION.citation_ref), "export lost exact reviewed-source clocks or citation link");
  const name = env.downloads.at(-1).name;
  assert(name.startsWith(prefix) && name.endsWith(".md") && /^[a-zA-Z0-9._-]+$/.test(name) &&
    name === prefix + f.observed().replace(/[-:.]/g, "") + ".md" && !name.includes(f.record) && !name.includes(f.digest),
    "export filename omitted its server stamp or included patient/record metadata");
  return exported;
}

async function main() {
  const env = createEnvironment(process.argv[2]);
  {
    const f = await fixture(env, { lifetime: 1000 });
    assert(f.reads().some(read => JSON.parse(read.options.body).mode === "recent") && f.save(),
      "provider initial history did not use a fresh1.3 recent display read");
    assert(!f.has("Legacy-unverified-answer-marker"), "host1.0 history displayed without authoritative expiry");
    f.input.value = "Preserve this newer fictional draft";
    await env.advance(999);
    assert(f.has("Fictional-dose-marker") && f.save(), "stable unrelated wall-clock offset shortened authoritative remaining lifetime");
    const oldSave = f.save(), before = env.blobs.length;
    await env.advance(1);
    assert(!f.has("Fictional-dose-marker") && !f.save() && f.input.value === "Preserve this newer fictional draft",
      "answer/export remained at its expiry boundary or draft was changed");
    await oldSave.click(); assert(env.blobs.length === before, "retained expired action exported cached bytes");
    await f.end();
  }
  {
    const f = await fixture(env), before = env.blobs.length, reads = f.reads().length;
    const oldSave = f.save(); f.input.value = "Draft survives Mac sleep";
    env.sleep(120000); // No mono/timer progress and no visibility event.
    await env.advance(1000); // First foreground local heartbeat after wake.
    assert(!f.has("Fictional-dose-marker") && !f.save() && f.reads().length === reads + 1,
      "paused Mac clock left an expired visible answer or omitted its fresh revalidation");
    await oldSave.click(); await env.advance(5000);
    assert(env.blobs.length === before && f.reads().length === reads + 1 &&
      f.input.value === "Draft survives Mac sleep", "wake exported stale bytes, changed draft or polled the server");
    await f.end();
  }
  {
    const f = await fixture(env), before = env.blobs.length, reads = f.reads().length;
    const oldSave = f.save(); f.input.value = "Draft survives interrupted wake check";
    f.displayMode = "hold-fetch"; env.sleep(2000); await env.advance(1000);
    assert(!f.has("Fictional-dose-marker") && !f.save() && f.reads().length === reads + 1,
      "wake did not hide cached bytes before awaiting its fresh read");
    await oldSave.click(); const late = f.envelope(), release = f.releaseFetch;
    f.panel.close(); release(response(200, late)); await env.flush(); await env.flush();
    assert(!f.has("Fictional-dose-marker") && env.blobs.length === before &&
      f.input.value === "Draft survives interrupted wake check", "late wake read resurrected closed text/export or erased draft");
    await f.end();
  }
  for (const phase of ["hold-fetch", "hold-body"]) {
    const f = await fixture(env), before = env.blobs.length;
    f.displayMode = phase; const captured = f.envelope();
    f.input.value = "Draft survives sleeping export";
    const saving = f.save().click(); await env.flush(); env.sleep(120000);
    if (phase === "hold-fetch") f.releaseFetch(response(200, captured)); else f.releaseBody();
    await saving;
    assert(env.blobs.length === before && !f.has("Fictional-dose-marker") &&
      f.input.value === "Draft survives sleeping export", "paused clock let pre-sleep response/body export cached bytes");
    await f.end();
  }
  {
    const f = await fixture(env);
    f.panel.close(); f.displayMode = "hold-body"; f.panel.open(); await env.flush();
    env.sleep(120000); f.releaseBody(); await env.flush(); await env.flush();
    assert(!f.has("Fictional-dose-marker") && !f.save(),
      "sleep during hidden fresh history read displayed pre-sleep metadata without a heartbeat");
    await f.end();
  }
  for (const event of ["focus", "pageshow"]) {
    const f = await fixture(env), before = env.blobs.length, reads = f.reads().length;
    const oldSave = f.save(); f.input.value = "Draft survives " + event;
    f.displayMode = "hold-fetch"; env.sleep(1000); await env.windowEvent(event);
    assert(!f.has("Fictional-dose-marker") && !f.save() && f.reads().length === reads + 1,
      event + " reused cached bytes before its fresh read");
    await oldSave.click(); assert(env.blobs.length === before, event + " retained cached export action");
    f.releaseFetch(response(200, f.envelope())); await env.flush(); await env.flush();
    assert(f.has("Fictional-dose-marker") && f.save() && f.input.value === "Draft survives " + event,
      event + " failed to restore newly verified content or changed draft");
    await f.end();
  }
  for (const event of ["focus", "pageshow", "visibility"]) {
    const f = await fixture(env); f.displayMode = "hold-fetch";
    const updating = f.panel.setRecord(f.record); await env.flush();
    const release = f.releaseFetch, late = f.envelope();
    f.input.value = "Draft survives interrupted same-record bootstrap";
    if (event === "visibility") await env.visibility(true); else await env.windowEvent(event);
    await updating;
    assert(!f.has("Fictional-dose-marker") && !f.has("Legacy-unverified-answer-marker") &&
      f.panel.root.querySelector(".cnp-gate").hidden &&
      f.input.value === "Draft survives interrupted same-record bootstrap",
      event + " stranded current-record bootstrap or showed unverified history");
    release(response(200, late)); await env.flush();
    assert(!f.has("Fictional-dose-marker"), "late interrupted bootstrap rendered old answer");
    f.displayMode = "success";
    if (event === "visibility") await env.visibility(false);
    else { f.panel.close(); await f.panel.open(); }
    await env.flush();
    assert(f.has("Fictional-dose-marker") && f.save() &&
      !f.panel.root.querySelector(".cnp-send").disabled &&
      f.input.value === "Draft survives interrupted same-record bootstrap",
      event + " did not restore an operable verified record on resume");
    await f.end();
  }
  {
    const f = await fixture(env), before = env.blobs.length, reads = f.reads().length;
    f.displayMode = "hold-fetch"; env.setWall(Date.parse("1900-01-01T00:00:00Z"));
    await f.save().click(); await env.flush();
    assert(!f.has("Fictional-dose-marker") && !f.save() && env.blobs.length === before &&
      f.reads().length === reads + 1, "backwards wall-clock discontinuity granted cached display/export time");
    f.releaseFetch(response(200, f.envelope())); await env.flush(); await env.flush();
    assert(f.has("Fictional-dose-marker") && f.save(),
      "stable wall offset blocked freshly authorized server time after discontinuity");
    await f.end();
  }
  for (const mutate of [
    value => { delete value.observed_at; },
    value => { value.observed_at = "not-a-clock"; },
    value => { value.observed_at = "2026-11-01T12:00:00-05:00"; },
    value => { delete value.history.messages[1].expires_at; },
    value => { value.history.messages[1].expires_at = null; },
    value => { value.history.messages[1].expires_at = "2026-11-02"; },
    value => { value.history.messages[1].expires_at = value.observed_at; },
    value => { value.history.messages[1].expires_at = "2026-10-31T12:00:00Z"; },
    value => { value.history.messages[0].expires_at = value.history.messages[1].expires_at; },
    value => { value.schema_version = "1.2.0"; },
    value => { value.unapproved_clock = value.observed_at; },
  ]) {
    const f = await fixture(env, { mutate });
    assert(!f.has("Fictional-dose-marker") && !f.has("Legacy-unverified-answer-marker") && !f.save(),
      "missing/invalid closed1.3 timing metadata displayed or exported an answer");
    await f.end();
  }
  for (const mode of ["offline", "denied", "unavailable", "missing", "hold-fetch", "hold-body"]) {
    const f = await fixture(env), before = env.blobs.length, button = f.save();
    f.input.value = "Newer draft survives unavailable export"; f.displayMode = mode;
    const saving = button.click(); await env.flush();
    if (mode.startsWith("hold-")) { await env.advance(30000); await saving; }
    else await saving;
    assert(env.blobs.length === before && f.input.value === "Newer draft survives unavailable export",
      "unverified export changed bytes/draft in " + mode + ": blobs=" + (env.blobs.length - before) +
      "; draft=" + JSON.stringify(f.input.value));
    if (mode === "denied") assert(!f.has("Fictional-dose-marker") && !f.save() &&
      !f.panel.root.querySelector(".cnp-gate").hidden, "denied fresh export retained answer/actions or provider access");
    if (mode === "hold-fetch") f.releaseFetch(response(200, f.envelope()));
    if (mode === "hold-body") f.releaseBody();
    await env.flush(); assert(env.blobs.length === before, "late timed-out read exported an answer");
    await f.end();
  }
  // Observed server lifetime is reduced by the entire local request/body wait.
  // It is deliberately unrelated to this answer's much older recorded_at.
  for (const phase of ["hold-fetch", "hold-body"]) {
    const f = await fixture(env, { lifetime: 1000 }), before = env.blobs.length;
    f.displayMode = phase; const saving = f.save().click(); await env.flush();
    const captured = f.envelope(); await env.advance(1000);
    if (phase === "hold-fetch") f.releaseFetch(response(200, captured)); else f.releaseBody();
    await saving;
    assert(env.blobs.length === before && !f.has("Fictional-dose-marker"),
      "expiry during read renewed the display clock or exported elapsed bytes");
    await f.end();
  }
  {
    const f = await fixture(env, { lifetime: 1000 });
    f.panel.close(); f.displayMode = "hold-body"; f.panel.open(); await env.flush();
    await env.advance(600); f.releaseBody(); await env.flush(); await env.flush();
    assert(f.has("Fictional-dose-marker"), "non-expired delayed fresh display was discarded");
    await env.advance(399); assert(f.has("Fictional-dose-marker"), "delayed display expired before its server boundary");
    await env.advance(1); assert(!f.has("Fictional-dose-marker") && !f.save(),
      "delayed display renewed its lifetime instead of subtracting body-read latency");
    await f.end();
  }
  {
    const f = await fixture(env, { lifetime: 1000 }), before = env.blobs.length;
    f.contextHoldAt = f.contextCalls + 2;
    const saving = f.save().click(); await env.flush();
    await env.advance(1000);
    f.releaseContext({ clientId: f.record, digest_ref: f.digest, thread_ref: "thread_synthetic_timed" });
    await saving;
    assert(env.blobs.length === before && !f.has("Fictional-dose-marker"), "expiry during host-context verification exported bytes");
    await f.end();
  }
  for (const lifecycle of ["record", "access", "destroy", "context"]) {
    const f = await fixture(env), before = env.blobs.length, oldSave = f.save();
    f.displayMode = "hold-fetch"; const saving = oldSave.click(); await env.flush();
    const release = f.releaseFetch, late = f.envelope(); f.displayMode = "success";
    if (lifecycle === "record") { f.record = "record_synthetic_other"; await f.panel.setRecord(f.record); }
    if (lifecycle === "access") { f.welcomeDenied = true; await f.panel.setRecord(f.record); }
    if (lifecycle === "destroy") f.panel.destroy();
    if (lifecycle === "context") f.digest = "digest_synthetic_changed";
    release(response(200, late)); await saving; await env.flush();
    assert(env.blobs.length === before, "late read exported after record/access/destroy/context changed");
    await oldSave.click(); assert(env.blobs.length === before, "retained lifecycle-invalid export action stayed active");
    await f.end();
  }
  // A valid timed DTO for another turn or changed saved material cannot grant
  // export authority to the retained action from the displayed answer.
  for (const mutate of [
    value => {
      const id = "99999999-9999-4999-8999-999999999999";
      value.turns[0].client_turn_id = id;
      value.history.messages.forEach(message => { message.client_turn_id = id; });
    },
    value => { value.history.messages[1].content += "Changed canonical answer."; },
    value => { value.history.messages[1].expires_at = "2026-11-03T12:00:00Z"; },
    value => { value.history.messages[1].reviewed_citations[0].reviewed_at = "2026-10-10T08:11:23.456Z"; },
  ]) {
    const f = await fixture(env), before = env.blobs.length;
    f.mutate = value => {
      // Clone nested fixture metadata so one rejected response cannot alter
      // the authoritative material for a later independent test case.
      value.history.messages[1].reviewed_citations = value.history.messages[1].reviewed_citations.map(citation => ({ ...citation }));
      mutate(value);
    };
    f.input.value = "Newer draft survives unmatched export";
    await f.save().click();
    assert(env.blobs.length === before && f.input.value === "Newer draft survives unmatched export",
      "unmatched turn/content/expiry/source clock exported retained bytes or changed draft");
    await f.end();
  }
  for (const hiddenBy of ["close", "visibility"]) {
    const f = await fixture(env), oldSave = f.save(), before = env.blobs.length, reads = f.reads().length;
    f.input.value = "Preserve the draft while hidden";
    if (hiddenBy === "close") f.panel.close(); else await env.visibility(true);
    assert(!f.has("Fictional-dose-marker") && !f.save(), "hidden panel retained cached accepted answer bytes/actions");
    await oldSave.click(); assert(env.blobs.length === before, "hidden retained action exported");
    f.displayMode = "hold-fetch";
    if (hiddenBy === "close") f.panel.open(); else await env.visibility(false);
    await env.flush();
    assert(f.reads().length === reads + 1 && !f.has("Fictional-dose-marker"), "reopen reused cache before its fresh read");
    f.releaseFetch(response(200, f.envelope())); await env.flush(); await env.flush();
    assert(f.has("Fictional-dose-marker") && f.save() && f.input.value === "Preserve the draft while hidden",
      "fresh reopen did not restore verified content or changed draft");
    await f.end();
  }
  {
    const f = await fixture(env), before = env.blobs.length;
    f.panel.close(); await env.advance(60000); f.displayMode = "offline"; f.panel.open(); await env.flush();
    assert(!f.has("Fictional-dose-marker") && !f.save() && env.blobs.length === before,
      "delayed offline reopen displayed expired cached answer");
    await f.end();
  }
  {
    const f = await fixture(env);
    await env.advance(1234); await verifiedExport(env, f, f.save());
    await f.end();
  }
  process.stdout.write("Provider answer expiry: server clocks, Mac sleep/wake discontinuities, boundary/latency, fresh export, hide/reopen and late-scope guards passed.\n");
}

module.exports = { FixtureElement, createEnvironment, fixture, legacyHistory, verifiedExport,
  ANSWER, STANDARD, SUPPRESSIONS, TURN_ID, RECORDED_AT, CITATION, assert, find, response };
if (require.main === module) main().catch(error => {
  process.stderr.write(String(error.stack || error) + "\n"); process.exitCode = 1;
});
