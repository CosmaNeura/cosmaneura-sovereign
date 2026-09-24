"use strict";

const fs = require("fs");
const vm = require("vm");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

class FakeElement {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.parentNode = null;
    this.style = {};
    this.className = "";
    this.textContent = "";
    this.value = tag === "select" ? "chat" : "";
    this.hidden = false;
    this.disabled = false;
    this._innerHTML = "";
    this._listeners = [];
    this.classList = {
      add: name => {
        if (!this.className.split(/\s+/).includes(name))
          this.className = (this.className + " " + name).trim();
      },
      toggle: (name, enabled) => {
        const values = this.className.split(/\s+/).filter(Boolean);
        const present = values.includes(name);
        if (enabled && !present) values.push(name);
        if (!enabled && present) values.splice(values.indexOf(name), 1);
        this.className = values.join(" ");
      },
    };
  }

  set innerHTML(value) {
    this._innerHTML = String(value);
    this.children = [];
    if (this.className === "cnp-root" && value.includes("cnp-rail")) {
      const names = ["cnp-gate", "cnp-fab", "cnp-rail", "cnp-thread", "cnp-chips", "cnp-in",
                     "cnp-send", "cnp-fmt", "cnp-sandbox", "cnp-sub", "cnp-x",
                     "cnp-prep", "cnp-prep-go", "cnp-prep-view", "cnp-prep-progress"];
      this._mountNodes = {};
      names.forEach(name => {
        const node = new FakeElement(
          name === "cnp-in" ? "textarea" : name === "cnp-fmt" ? "select" : "div"
        );
        node.className = name;
        node.value = name === "cnp-fmt" ? "chat" : "";
        if (name === "cnp-gate")
          node.textContent = "Sovereign named beta · unavailable";
        this._mountNodes[name] = node;
        this.appendChild(node);
      });
    }
  }

  get innerHTML() { return this._innerHTML; }
  get scrollHeight() { return 100; }
  get offsetWidth() { return 400; }
  set scrollTop(_value) {}
  focus() { if (!this.disabled) document.activeElement = this; }
  get disabled() { return this._disabled; }
  set disabled(value) {
    this._disabled = value;
    if (value && typeof document !== "undefined" && document.activeElement === this)
      document.activeElement = null;
  }
  click() { if (typeof this.onclick === "function") return this.onclick(); }
  setAttribute() {}
  remove() {
    if (this.parentNode)
      this.parentNode.children = this.parentNode.children.filter(child => child !== this);
  }
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  addEventListener(event, handler) { this._listeners.push([event, handler]); }
  removeEventListener(event, handler) {
    this._listeners = this._listeners.filter(item => item[0] !== event || item[1] !== handler);
  }
  querySelector(selector) {
    const name = selector.startsWith(".") ? selector.slice(1) : null;
    if (name && this._mountNodes && this._mountNodes[name]) return this._mountNodes[name];
    return find(this, node => name && node.className.split(/\s+/).includes(name));
  }
  querySelectorAll(selector) {
    const values = [];
    const name = selector.startsWith(".") ? selector.slice(1) : null;
    walk(this, node => {
      if (selector === "*" || (name && node.className.split(/\s+/).includes(name)))
        values.push(node);
    });
    return values;
  }
}

function walk(root, callback) {
  root.children.forEach(child => {
    callback(child);
    walk(child, callback);
  });
}

function find(root, predicate) {
  let result = null;
  walk(root, node => { if (!result && predicate(node)) result = node; });
  return result;
}

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: name => name.toLowerCase() === "x-cosmaneura-embed-version" ? "1.0" : null },
    json: async () => body,
  };
}

async function main() {
  const body = new FakeElement("body");
  body.style = {};
  global.document = Object.assign(new FakeElement("document"), {
    body,
    head: new FakeElement("head"),
    createElement: tag => new FakeElement(tag),
    createElementNS: (_namespace, tag) => new FakeElement(tag),
  });
  let uuidCounter = 1;
  global.window = {
    innerWidth: 1200,
    location: { origin: "https://host.example" },
    crypto: {
      randomUUID: () => `00000000-0000-4000-8000-${String(uuidCounter++).padStart(12, "0")}`,
    },
  };
  let browserPersistenceTouches = 0;
  ["localStorage", "sessionStorage", "indexedDB", "caches"].forEach(api => {
    const descriptor = {
      configurable: true,
      get: () => {
        browserPersistenceTouches += 1;
        throw new Error("browser persistence access: " + api);
      },
    };
    Object.defineProperty(global.window, api, descriptor);
    Object.defineProperty(global, api, descriptor);
  });
  Object.defineProperty(global, "navigator", {
    value: { clipboard: { writeText: async () => {} } },
    configurable: true,
  });

  const tokenRequests = [];
  let tokenSequence = 0;
  let currentRecord = "record_a";
  let welcomeMode = "provider";
  let contextLoads = 0;
  let historyLoads = 0;
  let chatMode = "service-retry";
  const chatRequests = [];
  let sessionPrepAdvertised = false;
  let sessionPrepMode = "ready";
  let releaseSessionPrep = null;
  const sessionPrepRequests = [];
  let humanContacts = 0;
  let historyNotifications = 0;

  global.fetch = async (url, options) => {
    if (url.endsWith("/welcome")) {
      if (welcomeMode === "denied") {
        return response(403, {
          code: "CAPABILITY_DENIED",
          retryable: false,
        });
      }
      return response(200, {
        partner_name: "Test",
        persona: welcomeMode,
        greeting: "Hello",
        paths: sessionPrepAdvertised ? [{
          label: "Open Session Prep", message: "Open Session Prep", intent: "session_prep",
        }] : [],
        disclaimer: "Standard disclaimer",
        emergency_note: "Call 911 in an emergency.",
        walkthrough_mode: "local_synthetic",
      });
    }
    if (url.endsWith("/session-prep")) {
      sessionPrepRequests.push({
        url,
        body: options.body,
        authorization: options.headers.authorization,
        turn: options.headers["x-cosmaneura-client-turn-id"],
      });
      const mode = sessionPrepMode;
      if (mode === "late-ready")
        await new Promise(resolve => { releaseSessionPrep = resolve; });
      if (mode === "service-retry") {
        sessionPrepMode = "ready";
        return response(503, {
          code: "SERVICE_UNAVAILABLE", retryable: true,
        });
      }
      if (mode === "forbidden") {
        return response(403, { code: "CAPABILITY_DENIED", retryable: false });
      }
      const requestBody = JSON.parse(options.body);
      const resultMode = mode === "late-ready" ? "ready" : mode;
      const content = resultMode === "ready" || resultMode === "refreshing";
      return response(200, {
        client_turn_id: requestBody.client_turn_id,
        state: resultMode,
        freshness: resultMode === "ready" ? "current" :
          resultMode === "refreshing" ? "stale" : "none",
        current_through: content ? "2026-09-02T12:00:00Z" : null,
        source_count: content ? 1 : 0,
        prepared_brief: content ? {
          recap: resultMode === "refreshing"
            ? "Only the returned stale brief is visible."
            : "Direct prepared-brief content.",
          trajectory_and_material_changes: [],
          talking_points: ["Confirm interval changes."],
          open_threads: [], cautions: [],
          suggested_outline: ["Review the prior plan."],
          patient_reported: null,
        } : null,
        retry_after_seconds: resultMode === "refreshing" || resultMode === "pending"
          ? 5 : null,
        reason_code: resultMode === "refreshing" ? "refresh_in_progress" :
          resultMode === "pending" ? "generation_pending" :
          resultMode === "unavailable" ? "context_not_available" : null,
      });
    }
    assert(url.endsWith("/chat"), "unexpected URL");
    chatRequests.push({
      url,
      body: options.body,
      authorization: options.headers.authorization,
      turn: options.headers["x-cosmaneura-client-turn-id"],
    });
    if (chatMode === "service-retry") {
      chatMode = "success";
      return response(503, {
        type: "https://contracts.cosmaneura.com/problems/service_unavailable",
        title: "Service unavailable",
        status: 503,
        code: "SERVICE_UNAVAILABLE",
        detail: "The assistant is temporarily unavailable.",
        trace_id: "trace_service_retry_1234",
        retryable: true,
      });
    }
    if (chatMode === "forbidden") {
      return response(403, {
        type: "https://contracts.cosmaneura.com/problems/capability_denied",
        title: "Capability denied",
        status: 403,
        code: "CAPABILITY_DENIED",
        detail: "Named-beta provider access is required.",
        trace_id: "trace_role_revoked_1234",
        retryable: false,
      });
    }
    if (chatMode === "mismatch") {
      return response(409, {
        type: "https://contracts.cosmaneura.com/problems/record_context_mismatch",
        title: "Record context mismatch",
        status: 409,
        code: "RECORD_CONTEXT_MISMATCH",
        detail: "The current record context cannot safely support this action.",
        trace_id: "trace_mismatch_1234",
        retryable: false,
      });
    }
    const requestBody = JSON.parse(options.body);
    return response(200, {
      answer: "Governed answer",
      intent: "small_talk",
      client_turn_id: requestBody.client_turn_id,
      citations: [],
      doctrinal_review: null,
      structured: {
        matches: [{
          name: "Fixture Provider", specialty: "Family Medicine",
          telehealth: true, booking_url: "https://directory.example/fixture",
        }],
      },
      safety_notice: null,
      presentation: {
        mode: "standard",
        suppress: [],
        human_contact: {
          label: "Contact support",
          action_id: "contact-support",
          fallback_text: "Please contact your support person.",
        },
      },
    });
  };

  const source = fs.readFileSync(process.argv[2], "utf8");
  vm.runInThisContext(source, { filename: process.argv[2] });

  let invalidTokenCalls = 0;
  try {
    window.CosmaNeuraPanel.mount({
      sovereignUrl: "https://sovereign.example/path",
      embedVersion: "1.0",
      getAuthToken: async () => { invalidTokenCalls += 1; return "opaque-token-invalid-1234"; },
      getContext: async () => ({ clientId: "record_a" }),
      onHumanContact: async () => {},
    });
    throw new Error("invalid origin was accepted");
  } catch (error) {
    assert(error.message.includes("HTTPS origin"), "invalid origin failed for wrong reason");
  }
  assert(invalidTokenCalls === 0, "origin validation acquired a token");
  try {
    window.CosmaNeuraPanel.mount({
      sovereignUrl: "http://remote.example",
      embedVersion: "1.0",
      allowLoopbackHttp: true,
      getAuthToken: async () => "opaque-token-invalid-1234",
      getContext: async () => ({ clientId: "record_a" }),
      onHumanContact: async () => {},
    });
    throw new Error("remote HTTP origin was accepted");
  } catch (error) {
    assert(error.message.includes("loopback HTTP"), "remote HTTP failed for wrong reason");
  }

  async function assertNamedBetaLocked(mode) {
    welcomeMode = mode;
    const locked = window.CosmaNeuraPanel.mount({
      sovereignUrl: "https://sovereign.example",
      embedVersion: "1.0",
      getAuthToken: async () => "opaque-token-unlisted-abcdefghijkl",
      getContext: async () => {
        contextLoads += 1;
        return { clientId: "record_locked" };
      },
      onHumanContact: async () => {},
      loadHistory: async () => { historyLoads += 1; return null; },
    });
    const gate = locked.root.querySelector(".cnp-gate");
    const fab = locked.root.querySelector(".cnp-fab");
    const rail = locked.root.querySelector(".cnp-rail");
    const lockedSend = locked.root.querySelector(".cnp-send");
    assert(gate.hidden === false && gate.textContent.includes("named beta"),
           "locked mount did not show named-beta copy");
    assert(fab.hidden === true && rail.hidden === true && lockedSend.disabled === true,
           "locked mount exposed the orchestrator before preflight");
    await locked.setRecord("record_locked");
    locked.open();
    assert(gate.hidden === false && fab.hidden === true && rail.hidden === true,
           "unlisted mount entered the orchestrator");
    assert(contextLoads === 0, "unlisted mount requested record context");
    assert(historyLoads === 0, "unlisted mount requested governed history");
    assert(chatRequests.length === 0, "unlisted mount sent a chat request");
    locked.destroy();
  }

  await assertNamedBetaLocked("denied");
  await assertNamedBetaLocked("patient");
  welcomeMode = "provider";
  window.location.origin = "http://127.0.0.1:8000";

  const controller = window.CosmaNeuraPanel.mount({
    sovereignUrl: "http://127.0.0.1:8000",
    embedVersion: "1.0",
    allowLoopbackHttp: true,
    getAuthToken: async request => {
      tokenRequests.push(request);
      tokenSequence += 1;
      return `opaque-token-session-${String(tokenSequence).padStart(2, "0")}-abcdefghijkl`;
    },
    getContext: async () => {
      contextLoads += 1;
      return {
        clientId: currentRecord,
        thread_ref: `thread_${currentRecord}`,
      };
    },
    onHumanContact: async action => {
      assert(Object.keys(action).sort().join(",") === "action_id,fallback_text,label",
             "human callback received more than the action descriptor");
      humanContacts += 1;
    },
    loadHistory: async () => {
      historyLoads += 1;
      return {
        thread_ref: `thread_${currentRecord}`,
        messages: [],
        next_cursor: null,
      };
    },
    onHistoryUpdated: async () => { historyNotifications += 1; },
  });

  const send = controller.root.querySelector(".cnp-send");
  const input = controller.root.querySelector(".cnp-in");
  assert(send.disabled === true, "send enabled before record handshake");
  await controller.setRecord("record_a");
  await Promise.resolve();
  assert(controller.root.querySelector(".cnp-gate").hidden === true,
         "provider preflight did not clear named-beta gate");
  assert(controller.root.querySelector(".cnp-fab").hidden === false,
         "provider preflight did not reveal panel launcher");
  assert(send.disabled === false, "send stayed disabled after record handshake");
  assert(input.readOnly === true, "local walkthrough accepted free-form input");
  assert(controller.root.querySelector(".cnp-fmt").disabled === true,
         "local walkthrough allowed output-format changes");
  assert(historyNotifications === 1, "governed history callback was not notified");

  input.value = "first governed turn";
  await send.onclick();
  const retry = find(controller.root, node => node.textContent === "Retry this exact request");
  assert(retry, "retryable response did not offer explicit retry");
  await retry.onclick();
  assert(chatRequests.length === 2, "explicit retry did not produce exactly one new fetch");
  assert(chatRequests[0].body === chatRequests[1].body, "explicit retry changed body/UUID");
  assert(chatRequests[0].turn === chatRequests[1].turn, "explicit retry changed turn header");
  assert(chatRequests[0].authorization !== chatRequests[1].authorization,
         "explicit retry did not acquire a fresh bearer");
  assert(tokenRequests.every(request => request === undefined),
         "bearer callback received an undocumented refresh directive");
  const sent = JSON.parse(chatRequests[0].body);
  assert(Object.keys(sent).sort().join(",") === "client_turn_id,message,metadata",
         "first send included a forbidden top-level field");
  assert(sent.metadata.output_format === "chat",
         "local walkthrough did not send its locked chat format");
  assert(!("context_snapshot_ref" in sent.metadata),
         "generic chat metadata received the Session Prep handle");
  assert(!chatRequests[0].body.includes("snapshot_record_a"),
         "generic chat body serialized the Session Prep handle");
  assert(chatRequests[0].url === "http://127.0.0.1:8000/chat" &&
         !chatRequests[0].url.includes("snapshot_record_a"),
         "generic chat URL exposed the Session Prep handle");
  let renderedChatSurface = "";
  walk(controller.root, node => {
    renderedChatSurface += " " + node.textContent + " " + node.innerHTML;
  });
  assert(!renderedChatSurface.includes("snapshot_record_a"),
         "generic chat rendered the Session Prep handle into the DOM");
  assert(!("history" in sent) && !("persona" in sent), "first send replayed raw browser state");
  assert(find(controller.root, node => node.innerHTML.includes("Governed answer")),
         "successful governed answer was not rendered");
  assert(find(controller.root, node => node.innerHTML.includes("Sovereign route")),
         "local walkthrough did not expose the routed orchestrator path");
  assert(find(controller.root, node => node.textContent === "Fixture Provider"),
         "structured directory option was not rendered");

  const oldContact = find(controller.root, node => node.textContent === "Contact support");
  assert(oldContact, "governed contact action was not rendered");
  currentRecord = "record_b";
  await controller.setRecord("record_b");
  await oldContact.onclick();
  assert(humanContacts === 0, "stale record action crossed a record generation");

  chatMode = "mismatch";
  input.value = "mismatched record turn";
  await send.onclick();
  assert(send.disabled === true, "409 record mismatch did not disable send");
  assert(controller.root.querySelector(".cnp-thread").children.length === 0,
         "409 record mismatch left the prior thread painted");

  controller.destroy();

  currentRecord = "record_revoked";
  chatMode = "success";
  const revoked = window.CosmaNeuraPanel.mount({
    sovereignUrl: "https://sovereign.example",
    embedVersion: "1.0",
    getAuthToken: async () => "opaque-token-revoked-abcdefghijkl",
    getContext: async () => ({ clientId: currentRecord }),
    onHumanContact: async () => {},
    loadHistory: async () => null,
  });
  await revoked.setRecord(currentRecord);
  const revokedGate = revoked.root.querySelector(".cnp-gate");
  const revokedFab = revoked.root.querySelector(".cnp-fab");
  const revokedRail = revoked.root.querySelector(".cnp-rail");
  const revokedSend = revoked.root.querySelector(".cnp-send");
  const revokedInput = revoked.root.querySelector(".cnp-in");
  assert(revokedGate.hidden === true && revokedFab.hidden === false,
         "provider fixture did not enter the orchestrator before revocation");
  chatMode = "forbidden";
  revokedInput.value = "turn after role revocation";
  await revokedSend.onclick();
  revoked.open();
  assert(revokedGate.hidden === false && revokedGate.textContent.includes("named beta"),
         "403 did not restore named-beta unavailable copy");
  assert(revokedFab.hidden === true && revokedRail.hidden === true && revokedSend.disabled === true,
         "403 left the orchestrator available");
  assert(revoked.root.querySelector(".cnp-thread").children.length === 0,
         "403 left governed history visible");
  revoked.destroy();

  // Session Prep is advertised only by a server-filtered governed path. Its
  // browser request contains no record, patient, note, source, or artifact data.
  sessionPrepAdvertised = true;
  sessionPrepMode = "ready";
  chatMode = "success";
  currentRecord = "record_prep_a";
  const serverOnlyContextRef = "context_synthetic_001";
  const noteDetailContext = {
    clientId: currentRecord,
    thread_ref: `thread_${currentRecord}`,
    digest_ref: "digest_must_not_cross_prep",
    terminology: "client",
    care_model: "must not cross",
  };
  assert(!("context_snapshot_ref" in noteDetailContext) &&
         !JSON.stringify(noteDetailContext).includes(serverOnlyContextRef),
         "generic NoteDetail context exposed the server-owned Session Prep handle");
  const prepController = window.CosmaNeuraPanel.mount({
    sovereignUrl: "https://sovereign.example",
    embedVersion: "1.0",
    getAuthToken: async () => {
      tokenSequence += 1;
      return `opaque-token-prep-${String(tokenSequence).padStart(2, "0")}-abcdefghijkl`;
    },
    getContext: async () => ({ ...noteDetailContext, clientId: currentRecord,
                               thread_ref: `thread_${currentRecord}` }),
    onHumanContact: async () => {},
    loadHistory: async () => null,
  });
  await prepController.setRecord(currentRecord);
  prepController.open();
  const prepGo = prepController.root.querySelector(".cnp-prep-go");
  const prepView = prepController.root.querySelector(".cnp-prep-view");
  assert(prepController.root.querySelector(".cnp-prep").hidden === false,
         "governed Session Prep path did not reveal the action");
  const prepChatStart = chatRequests.length;
  prepController.root.querySelector(".cnp-in").value = "generic chat before Session Prep";
  await prepController.root.querySelector(".cnp-send").onclick();
  const prepHostChat = chatRequests[prepChatStart];
  assert(prepHostChat && prepHostChat.url === "https://sovereign.example/chat",
         "prep-capable host did not use the exact generic chat URL");
  assert(!("context_snapshot_ref" in JSON.parse(prepHostChat.body).metadata) &&
         !prepHostChat.body.includes(serverOnlyContextRef) &&
         !prepHostChat.url.includes(serverOnlyContextRef),
         "prep-capable host exposed its Session Prep handle through generic chat");
  await prepGo.onclick();
  const firstPrep = JSON.parse(sessionPrepRequests[0].body);
  assert(Object.keys(firstPrep).join(",") === "client_turn_id",
         "Session Prep request carried extra browser data");
  assert(!sessionPrepRequests[0].body.includes(serverOnlyContextRef),
         "Session Prep browser body exposed the server-owned context handle");
  assert(sessionPrepRequests[0].url === "https://sovereign.example/session-prep" &&
         !sessionPrepRequests[0].url.includes(serverOnlyContextRef),
         "Session Prep URL exposed the server-owned context handle");
  const firstPrepWire = sessionPrepRequests[0].body;
  [serverOnlyContextRef, "record_prep_a", "digest_must_not_cross_prep", "care model",
   "patient", "note", "source_ref", "brief_ref"].forEach(value => {
    assert(!firstPrepWire.includes(value), "Session Prep leaked browser context: " + value);
  });
  assert(find(prepView, node => node.textContent === "Session Prep ready"),
         "ready state was not rendered");
  assert(find(prepView, node => node.textContent === "Direct prepared-brief content."),
         "prepared brief was not projected directly");
  assert(find(prepView, node => node.textContent.includes("1 source")),
         "human-readable source summary was not rendered");
  let renderedPrepSurface = "";
  walk(prepController.root, node => {
    renderedPrepSurface += " " + node.textContent + " " + node.innerHTML;
  });
  [serverOnlyContextRef, "record_prep_a", "digest_must_not_cross_prep",
   "opaque-token-prep", "source_ref", "brief_ref"].forEach(value => {
    assert(!renderedPrepSurface.includes(value), "Session Prep leaked into the DOM: " + value);
  });
  ["localStorage", "sessionStorage", "indexedDB", "caches.open"].forEach(api => {
    assert(!source.includes(api), "Session Prep uses browser persistence: " + api);
  });
  assert(browserPersistenceTouches === 0,
         "panel touched a browser-persistence API while a Session Prep handle was installed");

  // A pending result replaces the old content rather than retaining it.
  sessionPrepMode = "pending";
  await prepGo.onclick();
  assert(find(prepView, node => node.textContent === "Session Prep pending"),
         "pending state was not rendered");
  assert(!find(prepView, node => node.textContent === "Direct prepared-brief content."),
         "pending state retained prior clinical content");

  // A refreshing result may render only the stale brief returned by that call.
  sessionPrepMode = "refreshing";
  await prepGo.onclick();
  assert(find(prepView, node => node.textContent === "Session Prep refreshing"),
         "refreshing state was not rendered");
  assert(find(prepView, node => node.textContent === "Only the returned stale brief is visible."),
         "refreshing did not render its returned stale brief");
  assert(!find(prepView, node => node.textContent === "Direct prepared-brief content."),
         "refreshing retained the preceding brief");

  sessionPrepMode = "unavailable";
  await prepGo.onclick();
  assert(find(prepView, node => node.textContent === "Session Prep unavailable"),
         "unavailable state was not rendered");
  assert(!find(prepView, node => node.textContent === "Only the returned stale brief is visible."),
         "unavailable retained stale clinical content");

  // Browser transport retry preserves the exact logical action and UUID while
  // acquiring a fresh bearer and rechecking the same encounter handle.
  sessionPrepMode = "service-retry";
  const retryStart = sessionPrepRequests.length;
  await prepGo.onclick();
  const prepRetry = find(prepView, node =>
    node.textContent === "Retry this exact Session Prep request");
  assert(prepRetry, "retryable Session Prep error did not offer exact retry");
  await prepRetry.onclick();
  const retryRequests = sessionPrepRequests.slice(retryStart);
  assert(retryRequests.length === 2, "Session Prep retry made an unexpected request count");
  assert(retryRequests[0].body === retryRequests[1].body &&
         retryRequests[0].turn === retryRequests[1].turn,
         "Session Prep retry changed its body or client turn");
  assert(retryRequests[0].authorization !== retryRequests[1].authorization,
         "Session Prep retry did not acquire a fresh bearer");

  // Even a noncooperative late response from record A cannot render after the
  // synchronous A-to-B clear and generation change.
  sessionPrepMode = "late-ready";
  const lateRecordResponse = prepGo.onclick();
  for (let i = 0; i < 5 && !releaseSessionPrep; i += 1) await Promise.resolve();
  assert(releaseSessionPrep, "late record response was not held");
  currentRecord = "record_prep_b";
  const recordSwitch = prepController.setRecord(currentRecord);
  assert(prepView.hidden === true && prepView.children.length === 0,
         "record switch did not synchronously clear Session Prep");
  releaseSessionPrep(); releaseSessionPrep = null;
  await lateRecordResponse;
  assert(!find(prepView, node => node.textContent === "Direct prepared-brief content."),
         "late prior-record response repopulated Session Prep");
  await recordSwitch;
  prepController.open();
  sessionPrepMode = "ready";
  await prepGo.onclick();
  assert(find(prepView, node => node.textContent === "Direct prepared-brief content."),
         "new record did not render its prepared brief");
  sessionPrepMode = "late-ready";
  const lateEncounterResponse = prepGo.onclick();
  for (let i = 0; i < 5 && !releaseSessionPrep; i += 1) await Promise.resolve();
  // The host explicitly advances the same-record generation; no snapshot
  // handle is exposed to or compared by the browser.
  const encounterSwitch = prepController.setRecord(currentRecord);
  assert(prepView.hidden === true && prepView.children.length === 0,
         "encounter switch did not synchronously clear Session Prep");
  releaseSessionPrep(); releaseSessionPrep = null;
  await lateEncounterResponse;
  assert(!find(prepView, node => node.textContent === "Direct prepared-brief content."),
         "late prior-encounter response repopulated Session Prep");
  await encounterSwitch;
  prepController.open();

  // Revocation is terminal and clears every rendered byte rather than showing
  // the previous authorized brief as fallback.
  sessionPrepMode = "forbidden";
  await prepGo.onclick();
  assert(prepView.hidden === true && prepView.children.length === 0,
         "revocation retained Session Prep content");
  assert(prepController.root.querySelector(".cnp-gate").hidden === false,
         "revocation did not restore the closed named-beta gate");
  prepController.destroy();
}

module.exports = { FakeElement, assert, find, response };
if (require.main === module) {
  main().catch(error => {
    process.stderr.write(String(error.stack || error) + "\n");
    process.exitCode = 1;
  });
}
