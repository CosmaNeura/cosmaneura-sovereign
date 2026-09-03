"use strict";

const fs = require("fs");
const vm = require("vm");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

class FakeElement {
  constructor(tag, attributes = {}) {
    this.tagName = tag;
    this.attributes = { ...attributes };
    this.children = [];
    this.className = attributes.class || "";
    this.dataset = {};
    this.disabled = false;
    this.textContent = "";
    this._innerHTML = "";
    this.onclick = null;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  set innerHTML(value) {
    this._innerHTML = String(value);
    this.children = [];
  }

  get innerHTML() {
    return this._innerHTML;
  }

  click() {
    if (typeof this.onclick === "function") return this.onclick();
  }
}

function response(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((accept, decline) => {
    resolve = accept;
    reject = decline;
  });
  return { promise, resolve, reject };
}

async function until(predicate, message) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await new Promise(resolve => setImmediate(resolve));
  }
  throw new Error(message);
}

async function main() {
  const htmlPath = process.argv[2];
  assert(htmlPath, "demo HTML path is required");
  const html = fs.readFileSync(htmlPath, "utf8");
  const scripts = Array.from(html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi))
    .map(match => match[1])
    .filter(source => source.includes("CosmaNeuraPanel.mount"));
  assert(scripts.length === 1, "expected one inline Session Prep controller");

  const status = new FakeElement("div", { "aria-busy": "true" });
  status.textContent = "Opening the synthetic record…";
  const calls = new FakeElement("div", { role: "list" });
  const traceStatus = new FakeElement("span");
  traceStatus.textContent = "Waiting";
  const modes = ["ready", "refreshing", "pending", "unavailable"];
  const buttons = modes.map(mode => {
    const button = new FakeElement("button", {
      type: "button",
      "aria-pressed": "false",
    });
    button.dataset.mode = mode;
    return button;
  });
  const byId = {
    "demo-status": status,
    calls,
    "trace-status": traceStatus,
  };

  let mountOptions;
  let opened = 0;
  let destroyed = 0;
  let currentMode = "ready";
  let failNextState = false;
  let nextStateStatus = 200;
  let nextStatePayload = null;
  let nextTraceStatus = 200;
  let heldState = null;
  let actionBehavior = "success";
  let traceMutation = null;
  let actionCalls = 0;
  let latestTrace = null;
  const requests = [];
  const listeners = {};

  function traceFor(mode) {
    const states = {
      ready: ["pending", "refreshing", "ready"],
      refreshing: ["refreshing", "refreshing", "refreshing"],
      pending: ["pending", "pending", "pending"],
      unavailable: ["unavailable"],
    }[mode];
    return {
      mode,
      calls: states.map((state, index) => ({
        call: index + 1,
        core_state: state,
        method: "POST",
        path: "/service/v1/agent/tools/invoke",
        tool: "session-prep.context.read",
        version: "1.0.0",
        detail: "brief",
        exact_body: true,
        same_body: true,
        new_poll_key: true,
        authority_headers: true,
      })),
      canonical_writes: [{ state: mode, characters: 80 }],
    };
  }

  const prepAction = new FakeElement("button");
  prepAction.onclick = async () => {
    actionCalls += 1;
    if (actionBehavior === "error") throw new Error("synthetic action failure");
    if (actionBehavior === "revoke") {
      mountOptions.onAccessChange(false);
      return;
    }
    if (actionBehavior === "noop") return;
    latestTrace = traceFor(currentMode);
    if (traceMutation) {
      traceMutation(latestTrace);
      traceMutation = null;
    }
  };
  const panel = {
    root: { querySelector: selector => selector === ".cnp-prep-go" ? prepAction : null },
    setRecord: async recordId => {
      assert(recordId === "synthetic_session_prep_record", "unexpected synthetic record");
      mountOptions.onAccessChange(true);
      return 1;
    },
    open: () => { opened += 1; },
    destroy: () => { destroyed += 1; },
  };
  const document = {
    getElementById: id => byId[id],
    querySelectorAll: selector => selector === ".mode" ? buttons : [],
    createElement: tag => new FakeElement(tag),
  };
  const window = {
    location: { origin: "http://127.0.0.1:8765" },
    CosmaNeuraPanel: {
      mount: options => {
        mountOptions = options;
        return panel;
      },
    },
    addEventListener: (event, handler) => { listeners[event] = handler; },
  };
  const fetch = async (url, options = {}) => {
    requests.push({ url, options });
    if (url.startsWith("/demo/session-prep/state/")) {
      currentMode = url.split("/").pop();
      latestTrace = { mode: currentMode, calls: [], canonical_writes: [] };
      if (failNextState) {
        failNextState = false;
        throw new TypeError("synthetic connection failure");
      }
      if (heldState) return heldState.promise;
      const payload = nextStatePayload || { mode: currentMode };
      const statusCode = nextStateStatus;
      nextStatePayload = null;
      nextStateStatus = 200;
      return response(payload, statusCode);
    }
    assert(url === "/demo/session-prep/trace", "unexpected demo request: " + url);
    const statusCode = nextTraceStatus;
    nextTraceStatus = 200;
    return response(latestTrace, statusCode);
  };

  vm.runInNewContext(scripts[0], {
    document,
    window,
    fetch,
    console,
    Promise,
  }, { filename: htmlPath });

  function selectedMode() {
    const selected = buttons.filter(button =>
      button.getAttribute("aria-pressed") === "true");
    assert(selected.length <= 1, "multiple fixture modes were selected");
    return selected.length ? selected[0].dataset.mode : null;
  }

  function assertClosedFailure(previousMode, label) {
    assert(status.textContent === "The synthetic run failed closed. Check the local terminal.",
      `${label} did not expose the fail-closed status`);
    assert(status.getAttribute("aria-busy") === "false", `${label} remained busy`);
    assert(buttons.every(button => button.disabled === false),
      `${label} left controls disabled`);
    assert(selectedMode() === previousMode, `${label} changed the last confirmed mode`);
  }

  await until(
    () => status.textContent === "Rendered the ready state through the real vertical slice.",
    "initial ready run did not complete",
  );
  assert(opened === 1, "demo did not open the panel once");
  assert(status.getAttribute("aria-busy") === "false", "ready status remained busy");
  assert(buttons.every(button => button.disabled === false), "ready left controls disabled");
  assert(selectedMode() === "ready", "ready was not the sole selected mode");
  assert(calls.children.length === 3, "ready trace did not render three calls");
  assert(calls.children.every(row => row.getAttribute("role") === "listitem"),
    "trace rows were not semantic list items");
  assert(traceStatus.textContent === "3 verified Core calls", "ready trace count was unclear");

  for (const mode of ["refreshing", "unavailable"]) {
    await buttons[modes.indexOf(mode)].click();
    assert(status.textContent === `Rendered the ${mode} state through the real vertical slice.`,
      `${mode} did not expose its completion status`);
    assert(selectedMode() === mode, `${mode} was not the sole selected mode`);
  }
  assert(traceStatus.textContent === "1 verified Core call",
    "single-call trace count used incorrect grammar");

  heldState = deferred();
  const pendingRun = buttons[modes.indexOf("pending")].click();
  assert(status.getAttribute("aria-busy") === "true", "in-flight state was not marked busy");
  assert(buttons.every(button => button.disabled === true), "in-flight controls remained enabled");
  assert(selectedMode() === "unavailable",
    "in-flight request replaced the last confirmed mode");
  heldState.resolve(response({ mode: "pending" }));
  heldState = null;
  await pendingRun;
  assert(status.getAttribute("aria-busy") === "false", "completed state remained busy");
  assert(buttons.every(button => button.disabled === false), "completed state stayed disabled");
  assert(selectedMode() === "pending", "completed pending state was not selected");

  failNextState = true;
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "state connection failure");

  const actionsBeforeRejectedState = actionCalls;
  nextStateStatus = 503;
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "non-success state response");
  assert(actionCalls === actionsBeforeRejectedState,
    "non-success state response still invoked the panel action");

  nextStatePayload = { mode: "unavailable" };
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "mismatched state response");
  assert(actionCalls === actionsBeforeRejectedState,
    "mismatched state response still invoked the panel action");

  nextStatePayload = { mode: "ready", unexpected: true };
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "open state response");
  assert(actionCalls === actionsBeforeRejectedState,
    "open state response still invoked the panel action");

  actionBehavior = "noop";
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "no-op panel action");

  actionBehavior = "error";
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "rejected panel action");

  actionBehavior = "success";
  for (const invariant of ["exact_body", "authority_headers", "new_poll_key"]) {
    traceMutation = trace => { trace.calls[0][invariant] = false; };
    await buttons[modes.indexOf("ready")].click();
    assertClosedFailure("pending", `false ${invariant} trace invariant`);
  }

  traceMutation = trace => { trace.calls[2].core_state = "pending"; };
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "invalid trace sequence");

  traceMutation = trace => { trace.mode = "refreshing"; };
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "mismatched trace mode");

  traceMutation = trace => { trace.canonical_writes[0].state = "refreshing"; };
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "mismatched canonical write");

  nextTraceStatus = 503;
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "non-success trace response");

  actionBehavior = "revoke";
  await buttons[modes.indexOf("ready")].click();
  assert(status.textContent === "Synthetic authority was closed.",
    "mid-action authority loss was overwritten by a success or generic failure");
  assert(status.getAttribute("aria-busy") === "false", "authority loss remained busy");
  assert(buttons.every(button => button.disabled === true),
    "authority loss left fixture controls enabled");
  assert(selectedMode() === null, "authority loss retained a confirmed mode");

  assert(mountOptions.sovereignUrl === "http://127.0.0.1:8765" &&
    mountOptions.allowLoopbackHttp === true, "demo mount was not loopback-only");
  assert(await mountOptions.getAuthToken() === "synthetic-session-prep-mount-token",
    "demo did not use its synthetic mount token");
  const context = await mountOptions.getContext();
  assert(Object.keys(context).sort().join(",") === "care_model,clientId,thread_ref",
    "demo exposed unexpected browser context");
  assert(requests.every(request => request.url.startsWith("/demo/session-prep/")),
    "demo made a non-local request");
  assert(requests.filter(request => request.url.includes("/state/")).every(request =>
    request.options.method === "POST" && request.options.body === undefined),
  "state controls sent an unexpected request body");

  assert(typeof listeners.pagehide === "function", "pagehide teardown was not installed");
  listeners.pagehide();
  assert(destroyed === 1, "pagehide did not destroy the panel exactly once");
}

main().catch(error => {
  process.stderr.write(String(error.stack || error) + "\n");
  process.exitCode = 1;
});
