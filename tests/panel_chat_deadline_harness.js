"use strict";

const fs = require("fs");
const vm = require("vm");
const { FakeElement, assert, find, response } = require("./panel_contract_harness");

// Virtual time and deliberately non-cooperative host/transport promises prove
// that deadlines release the real panel, with no network or clinical data.
let now = 0, nextTimer = 0, uuid = 1;
const timers = new Map();
global.setTimeout = (callback, delay) => {
  timers.set(++nextTimer, { callback, at: now + delay });
  return nextTimer;
};
global.clearTimeout = id => timers.delete(id);
Date.now = () => now;
const flush = () => new Promise(resolve => setImmediate(resolve));
async function advance(ms) {
  const target = now + ms;
  while (true) {
    const next = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
    if (!next || next[1].at > target) break;
    now = next[1].at; timers.delete(next[0]); next[1].callback(); await flush();
  }
  now = target; await flush();
}
global.document = Object.assign(new FakeElement("document"), {
  body: new FakeElement("body"), head: new FakeElement("head"),
  createElement: tag => new FakeElement(tag),
});
global.window = { innerWidth: 1200, location: { origin: "https://host.example" },
  crypto: { randomUUID: () => `00000000-0000-4000-8000-${String(uuid++).padStart(12, "0")}` } };
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));

const QUESTION = "What fictional follow-up date was recorded?";
const ANSWER = "Synthetic recorded response.";
const PRESENTATION = { mode: "standard", suppress: [], human_contact: null };
function completed(id) {
  return { answer: ANSWER, client_turn_id: id, intent: "engagement", presentation: PRESENTATION,
    citations: [], doctrinal_review: null, structured: {}, safety_notice: null };
}
function recorded(id) {
  const common = { turn_ref: "turn_synthetic_deadline", client_turn_id: id,
    recorded_at: "2026-10-30T12:00:00Z" };
  return { schema_version: "1.0.0",
    history: { thread_ref: "thread_synthetic_deadline", next_cursor: null, messages: [
      { ...common, role: "user", content: QUESTION, presentation: null },
      { ...common, role: "assistant", content: ANSWER, presentation: PRESENTATION },
    ] }, turns: [{ ...common, state: "completed", outcome: "completed" }] };
}
async function fixture() {
  const f = { record: "record_synthetic_a", mode: "success", auth: "ready", context: "ready",
    requests: [], authCalls: 0, contextCalls: 0 };
  function hold(kind) { return new Promise((resolve, reject) => { f[kind] = resolve; f[kind + "Reject"] = reject; }); }
  global.fetch = async (url, options) => {
    if (url.endsWith("/welcome")) return response(200, {
      partner_name: "Synthetic", persona: "provider", greeting: "Hello", paths: [],
      disclaimer: "Synthetic", walkthrough_mode: "local_synthetic",
    });
    f.requests.push({ url, options });
    if (url.endsWith("/embed/history")) return response(200, recorded(JSON.parse(options.body).client_turn_id));
    assert(url.endsWith("/chat"), "unexpected request");
    const id = JSON.parse(options.body).client_turn_id;
    if (f.mode === "fetch-hang") return hold("releaseFetch");
    const result = response(200, completed(id));
    if (f.mode === "body-hang") result.json = async () => { await hold("releaseBody"); return completed(id); };
    return result;
  };
  f.panel = window.CosmaNeuraPanel.mount({
    sovereignUrl: "https://sovereign.example", embedVersion: "1.0",
    getAuthToken: async () => {
      f.authCalls += 1;
      if (f.auth === "hang") await hold("releaseAuth");
      return `opaque-synthetic-deadline-${f.authCalls}-abcdefghijkl`;
    },
    getContext: async () => {
      f.contextCalls += 1;
      if (f.context === "hang") return hold("releaseContext");
      return { clientId: f.record };
    },
    loadHistory: async () => null, onHumanContact: async () => {},
  });
  await f.panel.setRecord(f.record); f.panel.open(); await flush();
  f.input = f.panel.root.querySelector(".cnp-in");
  f.send = f.panel.root.querySelector(".cnp-send");
  f.thread = f.panel.root.querySelector(".cnp-thread");
  f.ask = () => { f.input.value = QUESTION; return f.send.click(); };
  f.has = text => !!find(f.thread, node => node.textContent.includes(text) || node.innerHTML.includes(text));
  f.button = text => find(f.thread, node => node.textContent === text);
  f.chats = () => f.requests.filter(request => request.url.endsWith("/chat"));
  f.end = async () => {
    f.panel.destroy(); await flush();
    assert(timers.size === 0, "chat deadline/progress timer leaked");
  };
  return f;
}

async function main() {
  // Initialization timeouts preserve the draft and never manufacture a turn.
  for (const phase of ["auth", "context"]) {
    const f = await fixture(); f[phase] = "hang";
    const sending = f.ask(); await flush();
    assert(f.send.disabled, "initialization was not pending");
    await advance(30000); await sending;
    assert(!f.send.disabled && f.input.value === QUESTION && f.has("no question was sent"), "setup deadline did not recover draft/controls");
    assert(f.chats().length === 0, "setup deadline submitted a question");
    if (phase === "auth") f.releaseAuth(); else f.releaseContext({ clientId: "record_other" });
    await flush();
    assert(f.chats().length === 0 && !f.send.disabled && f.input.value === QUESTION, "late setup callback mutated the recovered encounter");
    await f.end();
  }

  // Rejected callbacks after cleanup remain observed by the settled race and
  // cannot trigger unhandled rejections, account locking or late failure UI.
  for (const phase of ["auth", "context", "fetch-hang", "body-hang"]) {
    const f = await fixture();
    if (phase === "auth" || phase === "context") f[phase] = "hang"; else f.mode = phase;
    const sending = f.ask(); await flush();
    await advance(phase === "auth" || phase === "context" ? 30000 : 190000); await sending;
    const rejection = { auth: "releaseAuthReject", context: "releaseContextReject",
      "fetch-hang": "releaseFetchReject", "body-hang": "releaseBodyReject" }[phase];
    f[rejection](new Error("Synthetic late rejection")); await flush(); await flush();
    assert(!f.send.disabled && f.input.value === QUESTION && !f.panel.root.querySelector(".cnp-fab").hidden,
      "late rejection changed recovered access or draft");
    await f.end();
  }

  // Both fetch and body-read stalls remain pending through the actual server
  // budget, then recover. Explicit retry retains body/UUID and refreshes auth.
  for (const phase of ["fetch-hang", "body-hang"]) {
    const f = await fixture(); f.mode = phase;
    const sending = f.ask(); await flush();
    assert(f.chats().length === 1 && f.send.disabled, "question did not start exactly once");
    const original = f.chats()[0];
    await advance(30000);
    assert(f.has("still processing") && f.send.disabled, "long wait lacks bounded progress");
    await advance(150000);
    assert(f.send.disabled && !f.button("Retry this exact request"), "browser abandoned the server's 180-second receipt budget");
    await advance(10000); await sending;
    assert(!f.send.disabled && f.input.value === QUESTION && f.has("Check for a saved answer"), "chat deadline left controls/draft stranded");
    assert(f.chats().length === 1 && f.button("Check response"), "deadline automatically duplicated the turn or lost response recovery");
    if (phase === "fetch-hang") f.releaseFetch(response(200, completed(JSON.parse(original.options.body).client_turn_id)));
    else f.releaseBody();
    await flush();
    assert(!f.has(ANSWER), "late response replaced the timeout checkpoint");
    f.mode = "success";
    await f.button("Retry this exact request").click();
    const retried = f.chats()[1];
    assert(f.chats().length === 2 && retried.options.body === original.options.body &&
      retried.options.headers["x-cosmaneura-client-turn-id"] === original.options.headers["x-cosmaneura-client-turn-id"], "explicit retry changed the logical turn");
    assert(retried.options.headers.authorization !== original.options.headers.authorization, "retry reused bearer authority");
    assert(f.has(ANSWER) && !f.send.disabled, "exact retry did not recover");
    await f.end();
  }

  // History check recovers the same turn without replaying inference or
  // replacing a newer draft, including exact retry-identity preservation.
  {
    const f = await fixture(); f.mode = "fetch-hang";
    const sending = f.ask(); await flush(); await advance(190000); await sending;
    f.input.value = "A newer synthetic draft";
    await f.button("Check response").click();
    assert(f.chats().length === 1 && f.has(ANSWER) && f.input.value === "A newer synthetic draft", "history check replayed chat or replaced draft");
    await f.end();
  }

  // A retry callback that settles after its deadline cannot submit a turn or
  // invalidate the present record based on a late mismatched host context.
  {
    const f = await fixture(); f.mode = "fetch-hang";
    const sending = f.ask(); await flush(); await advance(190000); await sending;
    f.context = "hang";
    const retry = f.button("Retry this exact request").click(); await flush();
    await advance(190000); await retry;
    f.releaseContext({ clientId: "record_other" }); await flush();
    assert(f.chats().length === 1 && !f.send.disabled && f.input.value === QUESTION, "late retry context submitted or invalidated a turn");
    await f.end();
  }

  // Switching/destroying cancels the UI even if transport ignores abort. Old
  // timeouts/responses cannot release a newer request or paint another chart.
  {
    const f = await fixture(); f.mode = "fetch-hang";
    const first = f.ask(); await flush(); const releaseFirst = f.releaseFetch;
    f.record = "record_synthetic_b"; await f.panel.setRecord(f.record); await first;
    assert(!f.has(QUESTION), "record switch retained the old question");
    const second = f.ask(); await flush();
    releaseFirst(response(200, completed(JSON.parse(f.chats()[0].options.body).client_turn_id))); await flush();
    assert(f.send.disabled && !f.has(ANSWER), "old completion unlocked or rendered in a newer request");
    await advance(190000); await second;
    assert(!f.send.disabled && f.chats().length === 2, "new request deadline did not recover independently");
    await f.end();
  }
  {
    const f = await fixture(); f.mode = "body-hang";
    const sending = f.ask(); await flush();
    await f.end(); await sending;
    f.releaseBody(); await flush();
    assert(!f.has(ANSWER), "destroyed panel rendered a late body");
  }
}
main().catch(error => { process.stderr.write(String(error.stack || error) + "\n"); process.exitCode = 1; });
