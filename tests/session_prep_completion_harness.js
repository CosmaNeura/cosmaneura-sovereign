"use strict";

const fs = require("fs");
const vm = require("vm");
const { FakeElement, assert, find, response } = require("./panel_contract_harness");

// Drive real panel code with controlled HTTP/host callbacks and a virtual clock.
// No request, persisted fixture, or generated brief leaves this process.
let now = 0;
let nextTimer = 0;
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
    now = next[1].at;
    timers.delete(next[0]);
    next[1].callback();
    await flush();
  }
  now = target;
  await flush();
}

global.document = Object.assign(new FakeElement("document"), {
  hidden: false, body: new FakeElement("body"), head: new FakeElement("head"),
  createElement: tag => new FakeElement(tag),
});
let uuid = 1;
global.window = {
  innerWidth: 1200, location: { origin: "https://host.example" },
  crypto: { randomUUID: () => `00000000-0000-4000-8000-${String(uuid++).padStart(12, "0")}` },
};
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));

function visibility(hidden) {
  document.hidden = hidden;
  [...document._listeners].forEach(([event, handler]) => {
    if (event === "visibilitychange") handler();
  });
}

async function fixture() {
  const f = {
    mode: "pending", auth: "ready", context: "ready", record: "record_a",
    requests: [], authCalls: 0, contextCalls: 0, retryAfter: 5,
    release: null,
  };
  function hold() { return new Promise(resolve => { f.release = resolve; }); }
  global.fetch = async (url, options) => {
    if (url.endsWith("/chat")) return new Promise(resolve => {
      f.chatRelease = () => resolve(response(503, { code: "SERVICE_UNAVAILABLE", retryable: true }));
    });
    if (url.endsWith("/welcome")) return response(200, {
      partner_name: "Synthetic", persona: "provider", greeting: "Hello",
      paths: [{ label: "Open Session Prep", message: "Open Session Prep", intent: "session_prep" }],
      disclaimer: "Synthetic", emergency_note: "Call 911 in an emergency.",
      walkthrough_mode: "local_synthetic",
    });
    assert(url.endsWith("/session-prep"), "unexpected request");
    f.requests.push(options);
    if (f.latency) await new Promise(resolve => setTimeout(resolve, f.latency));
    const mode = f.mode;
    if (mode === "hang") await hold();
    if (mode === "network") throw new TypeError("synthetic disconnect");
    if (mode === "forbidden") return response(403, {});
    if (mode === "service") return response(503, { code: "SERVICE_UNAVAILABLE", retryable: true });
    if (mode === "malformed") return response(200, { state: "ready" });
    const content = ["ready", "refreshing", "hang", "body-hang"].includes(mode);
    const result = response(200, {
      client_turn_id: JSON.parse(options.body).client_turn_id,
      state: mode === "hang" || mode === "body-hang" ? "ready" : mode,
      freshness: content ? mode === "refreshing" ? "stale" : "current" : "none",
      current_through: content ? "2026-09-24T12:00:00Z" : null,
      source_count: content ? 1 : 0,
      prepared_brief: content ? {
        recap: "Synthetic completed brief", trajectory_and_material_changes: [],
        talking_points: ["Review interval changes"], open_threads: [], cautions: [],
        suggested_outline: ["Discuss prior plan"], patient_reported: null,
      } : null,
      retry_after_seconds: ["pending", "refreshing"].includes(mode) ? f.retryAfter : null,
      reason_code: mode === "pending" ? "generation_pending" :
        mode === "refreshing" ? "refresh_in_progress" :
        mode === "unavailable" ? "context_not_available" : null,
    });
    if (mode === "body-hang") {
      const read = result.json;
      result.json = async () => { await hold(); return read(); };
    }
    return result;
  };
  f.panel = window.CosmaNeuraPanel.mount({
    sovereignUrl: "https://sovereign.example", embedVersion: "1.0",
    getAuthToken: async () => {
      f.authCalls += 1;
      if (f.auth === "hang") await hold();
      if (f.auth === "denied") throw new Error("synthetic sign-out");
      return `opaque-token-synthetic-${f.authCalls}-abcdefghijkl`;
    },
    getContext: async () => {
      f.contextCalls += 1;
      if (f.context === "hang") await hold();
      return { clientId: f.record, thread_ref: "thread_synthetic" };
    },
    onHumanContact: async () => {}, loadHistory: async () => null,
  });
  await f.panel.setRecord(f.record);
  f.panel.open();
  f.view = f.panel.root.querySelector(".cnp-prep-view");
  f.go = f.panel.root.querySelector(".cnp-prep-go");
  f.progress = f.panel.root.querySelector(".cnp-prep-progress");
  f.has = text => f.progress.textContent.includes(text) ||
    !!find(f.view, node => node.textContent.includes(text));
  f.button = text => find(f.view, node => node.textContent === text);
  f.end = () => {
    f.panel.destroy();
    assert(timers.size === 0, "destroy leaked a timer");
    assert(document._listeners.length === 0, "destroy leaked document listeners");
  };
  return f;
}

async function main() {
  // Async completion uses new logical turns, fresh authority, and server pacing.
  for (const initial of ["pending", "refreshing"]) {
    const f = await fixture();
    f.mode = initial;
    await f.go.onclick();
    const staleButton = f.button(initial === "pending" ? "Check again" : "Refresh Session Prep");
    const auth = f.authCalls, context = f.contextCalls;
    assert(f.has("Checking automatically"), "waiting feedback absent");
    await advance(4999);
    assert(f.requests.length === 1, "ignored server retry interval");
    f.mode = "ready";
    await advance(1);
    assert(f.has("Session Prep ready") && f.has("Synthetic completed brief"), "did not complete automatically");
    assert(f.authCalls === auth + 1 && f.contextCalls === context + 1, "poll reused authority");
    assert(f.requests[0].body !== f.requests[1].body, "logical poll replayed pending turn");
    for (const request of f.requests) {
      assert(Object.keys(JSON.parse(request.body)).join() === "client_turn_id", "poll leaked context");
      assert(JSON.parse(request.body).client_turn_id === request.headers["x-cosmaneura-client-turn-id"], "turn mismatch");
    }
    assert(timers.size === 0, "ready state continued polling");
    // A retained prior cycle button must not replace a more recent result.
    await f.go.onclick();
    const count = f.requests.length;
    await staleButton.onclick();
    assert(f.requests.length === count, "old cycle button launched a request");
    f.end();
  }

  // The server owns pacing; the client cannot loop indefinitely.
  for (const retryAfter of [1, 20, 300]) {
    const f = await fixture();
    f.retryAfter = retryAfter;
    await f.go.onclick();
    await advance(240000);
    assert(f.requests.length <= 13, "poll count exceeded budget");
    if (retryAfter === 1) assert(f.requests.length === 13, "count ceiling not exercised");
    if (retryAfter === 20) assert(f.requests.length === 8, "time ceiling not exercised");
    if (retryAfter === 300) assert(f.requests.length === 1, "long retry interval was shortened");
    assert(timers.size === 0 && f.has("Automatic checking has paused"), "exhaustion left a spinner or timer");
    f.mode = "ready";
    await f.button("Check again").onclick();
    assert(f.has("Session Prep ready"), "manual check failed after budget exhaustion");
    f.end();
  }

  // Laptop suspension must not fire a queued check after its deadline.
  {
    const f = await fixture();
    await f.go.onclick();
    now += 240000;
    const queued = [...timers.values()][0]; timers.clear();
    queued.callback(); await flush();
    assert(f.requests.length === 1 && timers.size === 0 && f.has("Automatic checking has paused"), "resumed overdue poll");
    f.end();
  }

  // Background checks cannot overlap a provider's foreground chat turn.
  {
    const f = await fixture();
    await f.go.onclick();
    f.panel.root.querySelector(".cnp-in").value = "Synthetic provider question";
    const chat = f.panel.root.querySelector(".cnp-send").onclick();
    await flush();
    assert(f.chatRelease, "chat did not start");
    f.mode = "ready";
    await advance(5000);
    assert(f.requests.length === 1, "poll overlapped foreground chat");
    f.chatRelease(); await chat;
    // The synthetic chat failure restores its draft; dismiss it before resuming.
    f.panel.root.querySelector(".cnp-in").value = "";
    await advance(5000);
    assert(f.requests.length === 2 && f.has("Session Prep ready"), "poll did not resume after chat");
    f.end();
  }

  // A manual check replaces the old schedule even if its callback is queued.
  {
    const f = await fixture();
    await f.go.onclick();
    const queued = [...timers.values()][0];
    f.mode = "ready";
    await f.button("Check again").onclick();
    queued.callback(); await flush();
    assert(f.requests.length === 2 && timers.size === 0, "superseded timer launched work");
    f.end();
  }

  // Drafts/focus delay polling so a check cannot disable a field mid-sentence.
  for (const writing of ["draft", "focus"]) {
    const f = await fixture();
    await f.go.onclick();
    const input = f.panel.root.querySelector(".cnp-in");
    if (writing === "draft") input.value = "An unfinished question";
    else document.activeElement = input;
    await advance(5000);
    assert(f.requests.length === 1 && !input.disabled && f.has("while you’re writing"), "poll interrupted typing");
    input.value = ""; document.activeElement = null;
    f.mode = "ready";
    await advance(5000);
    assert(f.requests.length === 2 && f.has("Session Prep ready"), "poll failed to resume after typing");
    f.end();
  }

  // An unchanged response keeps the actual content nodes and scroll position.
  for (const initial of ["pending", "refreshing"]) {
    const f = await fixture(); f.mode = initial;
    await f.go.onclick();
    const children = [...f.view.children];
    f.latency = 2400;
    await advance(5000);
    assert(f.has("Checking for Session Prep updates"), "background progress absent");
    assert(children.every((node, i) => f.view.children[i] === node), "background check blanked content");
    // Changes in transport identity or pacing do not replace identical content.
    f.retryAfter = 10;
    await advance(2400);
    assert(children.every((node, i) => f.view.children[i] === node), "unchanged brief was rebuilt");
    f.mode = "ready";
    await advance(12400);
    assert(f.has("Session Prep ready"), "preserved content blocked completion");
    f.end();
  }

  // Realistic response latency must end the watch neutrally, not manufacture
  // a short final timeout at the edge of its waiting budget.
  {
    const f = await fixture(); f.retryAfter = 20; f.latency = 2400;
    const first = f.go.onclick();
    await flush(); await advance(2400); await first;
    await advance(240000);
    assert(f.requests.length === 7 && timers.size === 0, "latency drift exceeded waiting budget");
    assert(f.has("Automatic checking has paused") && !f.has("timed out"), "deadline produced a false timeout");
    assert(f.requests.every(request => !request.signal.aborted), "deadline aborted an avoidable final request");
    f.end();
  }

  // Interrupted deliveries stop until explicit retry, preserving exact identity.
  for (const mode of ["network", "service"]) {
    const f = await fixture();
    f.mode = mode;
    await f.go.onclick();
    assert(timers.size === 0, "delivery failure retried automatically");
    f.mode = "pending";
    await f.button("Retry this exact Session Prep request").onclick();
    assert(f.requests[0].body === f.requests[1].body, "delivery retry changed identity");
    assert(f.requests[0].headers.authorization !== f.requests[1].headers.authorization, "retry reused bearer");
    f.mode = "ready";
    await advance(5000);
    assert(f.requests[1].body !== f.requests[2].body && f.has("Session Prep ready"), "retry did not resume completion");
    f.end();
  }

  // Timeout covers callbacks, response headers, and body. Late completion cannot
  // send with an expired token or replace a later result from the same record.
  for (const phase of ["auth", "context", "hang", "body-hang"]) {
    const f = await fixture();
    if (phase === "auth" || phase === "context") f[phase] = "hang";
    else f.mode = phase;
    const original = f.go.onclick(); await flush();
    assert(f.release, "fixture did not hang at " + phase);
    const release = f.release;
    await advance(30000); await original;
    assert(!f.go.disabled && f.has("timed out"), "timeout stranded controls at " + phase);
    const first = f.requests[0];
    if (first) assert(first.signal.aborted, "timeout did not abort transport");
    f.auth = f.context = "ready"; f.mode = "ready";
    await f.button("Retry this exact Session Prep request").onclick();
    if (first) assert(first.body === f.requests[1].body, "timeout retry changed turn");
    const count = f.requests.length;
    release(); await flush();
    assert(f.requests.length === count && f.has("Session Prep ready"), "late timed-out operation escaped");
    f.end();
  }

  // Cancellation clears timers and late responses, including same-record resets.
  for (const action of ["close", "hidden", "record", "encounter", "destroy"]) {
    for (const hanging of [false, true]) {
      const f = await fixture();
      await f.go.onclick();
      let pending;
      if (hanging) {
        f.mode = "hang";
        pending = f.go.onclick(); await flush();
        assert(f.release, "late response fixture missing");
      }
      const requestCount = f.requests.length;
      if (action === "close") f.panel.close();
      if (action === "hidden") visibility(true);
      if (action === "record" || action === "encounter") {
        if (action === "record") f.record = "record_b";
        await f.panel.setRecord(f.record);
      }
      if (action === "destroy") f.panel.destroy();
      await flush();
      assert(f.view.hidden && !f.view.children.length && timers.size === 0, "cancellation retained content/work: " + action);
      if (hanging) { f.release(); await pending; await flush(); }
      await advance(300000);
      assert(f.requests.length === requestCount && !f.has("Synthetic completed brief"), "canceled work resumed: " + action);
      visibility(false);
      f.end();
    }
  }

  for (const failure of ["forbidden", "malformed", "unavailable", "sign-out", "changed-record"]) {
    const f = await fixture();
    await f.go.onclick();
    f.mode = failure === "sign-out" || failure === "changed-record" ? "ready" : failure;
    if (failure === "sign-out") f.auth = "denied";
    if (failure === "changed-record") f.record = "record_b";
    await advance(5000);
    assert(timers.size === 0 && !f.has("Synthetic completed brief"), "terminal state retained work/content: " + failure);
    if (["forbidden", "sign-out", "changed-record"].includes(failure))
      assert(f.view.hidden, "authority loss did not clear panel");
    if (["sign-out", "changed-record"].includes(failure))
      assert(f.requests.length === 1, "poll sent after host authority changed");
    f.end();
  }
  process.stdout.write("Session Prep completion, recovery, limits, and cancellation passed.\n");
}

main().catch(error => {
  process.stderr.write(String(error.stack || error) + "\n");
  process.exitCode = 1;
});
