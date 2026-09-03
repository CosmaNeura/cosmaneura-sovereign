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
    if (this.disabled) return;
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

const CONTROLLER_START_MARKER = "/* SESSION_PREP_DEMO_CONTROLLER_START */";
const CONTROLLER_END_MARKER = "/* SESSION_PREP_DEMO_CONTROLLER_END */";

function uniqueMarkerIndex(source, marker, label) {
  const first = source.indexOf(marker);
  assert(first !== -1, `missing ${label} marker`);
  assert(source.indexOf(marker, first + marker.length) === -1,
    `duplicate ${label} marker`);
  return first;
}

function extractController(source) {
  const start = uniqueMarkerIndex(source, CONTROLLER_START_MARKER, "controller start");
  const end = uniqueMarkerIndex(source, CONTROLLER_END_MARKER, "controller end");
  assert(end > start, "controller markers are out of order");
  return source.slice(start + CONTROLLER_START_MARKER.length, end);
}

async function main() {
  const htmlPath = process.argv[2];
  assert(htmlPath, "demo HTML path is required");
  const html = fs.readFileSync(htmlPath, "utf8");
  const controller = extractController(html);
  assert(controller.includes("CosmaNeuraPanel.mount"),
    "expected the marked Session Prep controller");

  const status = new FakeElement("div", { "aria-busy": "true" });
  status.textContent = "Opening the synthetic record…";
  const calls = new FakeElement("div", { role: "list" });
  const traceStatus = new FakeElement("span");
  traceStatus.textContent = "Waiting";
  const hostStatus = new FakeElement("div", { "aria-busy": "true" });
  hostStatus.textContent = "Opening synthetic record A…";
  const switchButton = new FakeElement("button", { type: "button" });
  const revokeButton = new FakeElement("button", { type: "button" });
  const resetButton = new FakeElement("button", { type: "button", disabled: "" });
  resetButton.disabled = true;
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
    "host-status": hostStatus,
    "record-switch": switchButton,
    "authority-revoke": revokeButton,
    "authority-reset": resetButton,
  };

  let mountOptions;
  let opened = 0;
  let closed = 0;
  let destroyed = 0;
  let accessGranted = false;
  let activeRecord = null;
  let currentMode = "ready";
  let failNextState = false;
  let nextStateStatus = 200;
  let nextStatePayload = null;
  let nextTraceStatus = 200;
  let heldState = null;
  let heldAction = null;
  let actionBehavior = "success";
  let traceMutation = null;
  let actionCalls = 0;
  let latestTrace = null;
  let currentRunId = null;
  let runCounter = 0;
  const requests = [];
  const recordChanges = [];
  const listeners = {};

  function runId(number) {
    return "demo_run_" + number.toString(16).padStart(32, "0");
  }

  function nextRunId() {
    runCounter += 1;
    return runId(runCounter);
  }

  function traceFor(mode, runId) {
    const states = {
      ready: ["pending", "refreshing", "ready"],
      refreshing: ["refreshing", "refreshing", "refreshing"],
      pending: ["pending", "pending", "pending"],
      unavailable: ["unavailable"],
    }[mode];
    return {
      run_id: runId,
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
      canonical_writes: [{
        run_id: runId,
        state: mode === "unavailable" ? "unavailable" : "completed",
        characters: 80,
      }],
    };
  }

  const prepAction = new FakeElement("button");
  prepAction.onclick = async () => {
    actionCalls += 1;
    if (actionBehavior === "error") throw new Error("synthetic action failure");
    if (actionBehavior === "revoke") {
      accessGranted = false;
      mountOptions.onAccessChange(false);
      return;
    }
    if (actionBehavior === "noop") return;
    await window.fetch(window.location.origin + "/session-prep", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{}",
    });
    if (traceMutation) {
      traceMutation(latestTrace);
      traceMutation = null;
    }
  };
  const panel = {
    root: { querySelector: selector => selector === ".cnp-prep-go" ? prepAction : null },
    setRecord: async recordId => {
      assert(["synthetic_session_prep_record_a", "synthetic_session_prep_record_b"]
        .includes(recordId), "unexpected synthetic record");
      recordChanges.push(recordId);
      if (accessGranted) {
        accessGranted = false;
        mountOptions.onAccessChange(false);
      }
      const token = await mountOptions.getAuthToken();
      if (token !== "synthetic-session-prep-mount-token") return null;
      const context = await mountOptions.getContext();
      assert(context.clientId === recordId, "host context did not follow the record");
      assert(context.thread_ref === `thread_synthetic_session_prep_${recordId.endsWith("_a") ? "a" : "b"}`,
        "host thread context crossed synthetic records");
      activeRecord = recordId;
      accessGranted = true;
      mountOptions.onAccessChange(true);
      return recordChanges.length;
    },
    open: () => { opened += 1; },
    close: () => { closed += 1; },
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
  async function fetch(url, options = {}) {
    requests.push({ url, options });
    if (url.startsWith("/demo/session-prep/state/")) {
      currentMode = url.split("/").pop();
      currentRunId = nextRunId();
      latestTrace = {
        run_id: currentRunId, mode: currentMode, calls: [], canonical_writes: [],
      };
      if (failNextState) {
        failNextState = false;
        throw new TypeError("synthetic connection failure");
      }
      if (heldState) return heldState.promise;
      const payload = nextStatePayload || { mode: currentMode, run_id: currentRunId };
      const statusCode = nextStateStatus;
      nextStatePayload = null;
      nextStateStatus = 200;
      return response(payload, statusCode);
    }
    if (url === window.location.origin + "/session-prep" || url === "/session-prep") {
      assert(options.headers["X-Cosmaneura-Demo-Run-ID"] === currentRunId,
        "panel action did not carry the current server-issued run id");
      latestTrace = traceFor(currentMode, currentRunId);
      if (heldAction) {
        const pendingAction = heldAction;
        heldAction = null;
        await pendingAction.promise;
      }
      return response({}, 200);
    }
    assert(url === "/demo/session-prep/trace", "unexpected demo request: " + url);
    assert(options.headers["X-Cosmaneura-Demo-Run-ID"] === currentRunId,
      "trace request did not carry the current server-issued run id");
    const statusCode = nextTraceStatus;
    nextTraceStatus = 200;
    return response(latestTrace, statusCode);
  }
  window.fetch = fetch;

  vm.runInNewContext(controller, {
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
  const initialRunId = currentRunId;

  const heldRecordAAction = deferred();
  heldAction = heldRecordAAction;
  const actionsBeforeStaleRun = actionCalls;
  const traceRequestsBeforeStaleRun = requests.filter(request =>
    request.url.endsWith("/trace")).length;
  const staleRun = buttons[modes.indexOf("refreshing")].click();
  await until(() => actionCalls === actionsBeforeStaleRun + 1,
    "held record A action did not start");
  assert(status.getAttribute("aria-busy") === "true",
    "held record A action was not busy");
  assert(switchButton.disabled === false && revokeButton.disabled === false,
    "host boundary controls could not interrupt a held action");

  const switchPromise = switchButton.click();
  assert(closed === 1, "record switch did not close the panel immediately");
  assert(selectedMode() === null, "record switch retained prior mode evidence");
  assert(calls.children.length === 0 && calls.innerHTML.includes("current synthetic record"),
    "record switch did not clear prior trace evidence immediately");
  assert(traceStatus.textContent === "Waiting for a current synthetic run",
    "record switch retained the prior trace status");
  assert(status.textContent === "Switching synthetic records…",
    "record switch did not expose a live transition status");
  await switchPromise;
  assert(activeRecord === "synthetic_session_prep_record_b",
    "record switch did not activate synthetic record B");
  assert(status.textContent === "Synthetic record B is open. Choose a typed state.",
    "record switch did not expose its bounded completion status");
  assert(hostStatus.textContent === "Synthetic record B active; prior displayed trace cleared.",
    "record switch did not confirm that its displayed trace was cleared");
  assert(switchButton.disabled === false && revokeButton.disabled === false &&
    resetButton.disabled === true, "record switch left incorrect host controls enabled");

  await buttons[modes.indexOf("ready")].click();
  assert(status.textContent === "Rendered the ready state through the real vertical slice.",
    "ready did not work on record B while record A remained held");
  assert(selectedMode() === "ready", "ready was not selected on record B");
  assert(calls.children.length === 3, "record B ready trace did not render");
  const traceRequestsAfterRecordB = requests.filter(request =>
    request.url.endsWith("/trace")).length;
  assert(traceRequestsAfterRecordB === traceRequestsBeforeStaleRun + 1,
    "record B did not produce exactly one current trace request");

  heldRecordAAction.resolve();
  await staleRun;
  assert(status.textContent === "Rendered the ready state through the real vertical slice.",
    "late record A action repainted record B status");
  assert(selectedMode() === "ready", "late record A action changed record B state");
  assert(calls.children.length === 3,
    "late record A action repainted record B trace evidence");
  assert(requests.filter(request => request.url.endsWith("/trace")).length ===
    traceRequestsAfterRecordB, "late record A action requested stale trace evidence");

  for (const mode of ["refreshing", "unavailable"]) {
    await buttons[modes.indexOf(mode)].click();
    assert(status.textContent === `Rendered the ${mode} state through the real vertical slice.`,
      `${mode} did not expose its completion status`);
    assert(selectedMode() === mode, `${mode} was not the sole selected mode`);
  }
  assert(traceStatus.textContent === "1 verified Core call",
    "single-call trace count used incorrect grammar");

  traceMutation = trace => { trace.canonical_writes[0].state = "completed"; };
  await buttons[modes.indexOf("unavailable")].click();
  assertClosedFailure("unavailable", "wrong unavailable canonical outcome");

  heldState = deferred();
  const pendingRun = buttons[modes.indexOf("pending")].click();
  assert(status.getAttribute("aria-busy") === "true", "in-flight state was not marked busy");
  assert(buttons.every(button => button.disabled === true), "in-flight controls remained enabled");
  assert(selectedMode() === "unavailable",
    "in-flight request replaced the last confirmed mode");
  heldState.resolve(response({ mode: "pending", run_id: currentRunId }));
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

  nextStatePayload = { mode: "unavailable", run_id: runId(runCounter + 1) };
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "mismatched state response");
  assert(actionCalls === actionsBeforeRejectedState,
    "mismatched state response still invoked the panel action");

  nextStatePayload = {
    mode: "ready", run_id: runId(runCounter + 1), unexpected: true,
  };
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "open state response");
  assert(actionCalls === actionsBeforeRejectedState,
    "open state response still invoked the panel action");

  nextStatePayload = { mode: "ready", run_id: "demo_run_not_a_nonce" };
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "malformed server run id");
  assert(actionCalls === actionsBeforeRejectedState,
    "malformed server run id still invoked the panel action");

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

  traceMutation = trace => { trace.canonical_writes = []; };
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "missing current canonical write");

  traceMutation = trace => {
    trace.canonical_writes.push({ ...trace.canonical_writes[0] });
  };
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "duplicate current canonical write");

  traceMutation = trace => { trace.calls[2].core_state = "pending"; };
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "invalid trace sequence");

  traceMutation = trace => { trace.mode = "refreshing"; };
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "mismatched trace mode");

  traceMutation = trace => { trace.canonical_writes[0].state = "ready"; };
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "mode-shaped canonical write");

  traceMutation = trace => {
    trace.run_id = initialRunId;
    trace.canonical_writes[0].run_id = initialRunId;
  };
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "cross-run trace evidence");

  traceMutation = trace => { trace.canonical_writes[0].run_id = initialRunId; };
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "cross-run canonical write evidence");

  nextTraceStatus = 503;
  await buttons[modes.indexOf("ready")].click();
  assertClosedFailure("pending", "non-success trace response");

  actionBehavior = "revoke";
  await buttons[modes.indexOf("ready")].click();
  assert(status.textContent === "Synthetic authority was closed.",
    "mid-action authority loss was overwritten by a success or generic failure");
  assert(hostStatus.textContent ===
    "Local synthetic access closed; displayed content and trace cleared.",
  "mid-action authority loss left a stale host boundary status");
  assert(status.getAttribute("aria-busy") === "false", "authority loss remained busy");
  assert(buttons.every(button => button.disabled === true),
    "authority loss left fixture controls enabled");
  assert(selectedMode() === null, "authority loss retained a confirmed mode");
  assert(switchButton.disabled === true && revokeButton.disabled === true &&
    resetButton.disabled === false, "authority loss exposed unsafe host controls");

  actionBehavior = "success";
  await resetButton.click();
  assert(activeRecord === "synthetic_session_prep_record_b",
    "synthetic reset changed the active record");
  assert(status.textContent === "Synthetic access reset. Choose a typed state.",
    "synthetic reset did not expose its completion status");
  assert(hostStatus.textContent === "Local synthetic access reset for record B.",
    "synthetic reset did not remain explicitly local");
  assert(buttons.every(button => button.disabled === false),
    "synthetic reset did not restore typed-state controls");
  assert(resetButton.disabled === true,
    "synthetic reset remained available while authority was open");

  await buttons[modes.indexOf("pending")].click();
  assert(selectedMode() === "pending" && calls.children.length === 3,
    "typed states did not work after synthetic reset");

  const closesBeforeExplicitRevocation = closed;
  const revokePromise = revokeButton.click();
  assert(closed === closesBeforeExplicitRevocation + 1,
    "explicit revocation did not close the panel immediately");
  assert(selectedMode() === null && calls.children.length === 0,
    "explicit revocation did not clear rendered evidence immediately");
  assert(hostStatus.textContent === "Revoking local synthetic access…",
    "explicit revocation did not expose a live transition status");
  await revokePromise;
  assert(status.textContent === "Synthetic authority was closed.",
    "explicit revocation did not leave the panel closed");
  assert(hostStatus.textContent ===
    "Local synthetic access revoked; displayed content and trace cleared.",
  "explicit revocation did not confirm its local, cleared boundary");
  assert(buttons.every(button => button.disabled === true) &&
    switchButton.disabled === true && revokeButton.disabled === true &&
    resetButton.disabled === false, "explicit revocation left unsafe controls enabled");
  assert(await mountOptions.getAuthToken() === null,
    "explicit revocation retained the synthetic token");

  await resetButton.click();
  await buttons[modes.indexOf("ready")].click();
  assert(selectedMode() === "ready" && calls.children.length === 3,
    "four-state exercise did not recover after explicit reset");

  assert(mountOptions.sovereignUrl === "http://127.0.0.1:8765" &&
    mountOptions.allowLoopbackHttp === true, "demo mount was not loopback-only");
  assert(await mountOptions.getAuthToken() === "synthetic-session-prep-mount-token",
    "demo did not use its synthetic mount token");
  const context = await mountOptions.getContext();
  assert(Object.keys(context).sort().join(",") === "care_model,clientId,thread_ref",
    "demo exposed unexpected browser context");
  assert(requests.every(request => request.url.startsWith("/demo/session-prep/") ||
    request.url === "http://127.0.0.1:8765/session-prep"),
    "demo made a non-local request");
  assert(requests.filter(request => request.url.includes("/state/")).every(request =>
    request.options.method === "POST" && request.options.body === undefined),
  "state controls sent an unexpected request body");
  assert(requests.filter(request => request.url.endsWith("/session-prep") &&
    !request.url.includes("/demo/")).every(request =>
      /^demo_run_[0-9a-f]{32}$/.test(
        request.options.headers["X-Cosmaneura-Demo-Run-ID"] || "")),
  "panel actions omitted the opaque server-issued run id");
  assert(requests.filter(request => request.url.endsWith("/trace")).every(request =>
    /^demo_run_[0-9a-f]{32}$/.test(
      request.options.headers["X-Cosmaneura-Demo-Run-ID"] || "")),
  "trace requests omitted the opaque server-issued run id");
  const renderedHostText = [status.textContent, hostStatus.textContent,
    traceStatus.textContent, calls.innerHTML]
    .concat(calls.children.map(row => row.textContent)).join("\n");
  for (const hidden of [
    "synthetic-session-prep-mount-token",
    "synthetic_session_prep_record_a",
    "synthetic_session_prep_record_b",
    "thread_synthetic_session_prep_a",
    "thread_synthetic_session_prep_b",
  ]) {
    assert(!renderedHostText.includes(hidden), `host UI exposed ${hidden}`);
    assert(!requests.some(request => String(request.url).includes(hidden) ||
      String(request.options.body || "").includes(hidden)),
    `request URL or body exposed ${hidden}`);
  }

  assert(typeof listeners.pagehide === "function", "pagehide teardown was not installed");
  listeners.pagehide();
  assert(destroyed === 1, "pagehide did not destroy the panel exactly once");
}

main().catch(error => {
  process.stderr.write(String(error.stack || error) + "\n");
  process.exitCode = 1;
});
