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
      const names = ["cnp-fab", "cnp-rail", "cnp-thread", "cnp-chips", "cnp-in",
                     "cnp-send", "cnp-fmt", "cnp-sandbox", "cnp-sub", "cnp-x"];
      this._mountNodes = {};
      names.forEach(name => {
        const node = new FakeElement(
          name === "cnp-in" ? "textarea" : name === "cnp-fmt" ? "select" : "div"
        );
        node.className = name;
        node.value = name === "cnp-fmt" ? "chat" : "";
        this._mountNodes[name] = node;
        this.appendChild(node);
      });
    }
  }

  get innerHTML() { return this._innerHTML; }
  get scrollHeight() { return 100; }
  get offsetWidth() { return 400; }
  set scrollTop(_value) {}
  focus() {}
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
  global.document = {
    body,
    head: new FakeElement("head"),
    createElement: tag => new FakeElement(tag),
    createElementNS: (_namespace, tag) => new FakeElement(tag),
  };
  let uuidCounter = 1;
  global.window = {
    innerWidth: 1200,
    crypto: {
      randomUUID: () => `00000000-0000-4000-8000-${String(uuidCounter++).padStart(12, "0")}`,
    },
  };
  Object.defineProperty(global, "navigator", {
    value: { clipboard: { writeText: async () => {} } },
    configurable: true,
  });

  const tokenRequests = [];
  let mountToken = "opaque-token-session-a-abcdefghijkl";
  let rotateCalls = 0;
  let currentRecord = "record_a";
  let chatMode = "auth-retry";
  const chatRequests = [];
  let humanContacts = 0;
  let historyNotifications = 0;

  global.fetch = async (url, options) => {
    if (url.endsWith("/welcome")) {
      return response(200, {
        partner_name: "Test",
        persona: "provider",
        greeting: "Hello",
        paths: [],
        disclaimer: "Standard disclaimer",
        emergency_note: "Call 911 in an emergency.",
      });
    }
    assert(url.endsWith("/chat"), "unexpected URL");
    chatRequests.push({
      body: options.body,
      authorization: options.headers.authorization,
      turn: options.headers["x-cosmaneura-client-turn-id"],
    });
    if (chatMode === "auth-retry") {
      chatMode = "success";
      return response(401, {
        type: "https://contracts.cosmaneura.com/problems/token_invalid",
        title: "Token invalid",
        status: 401,
        code: "TOKEN_INVALID",
        detail: "The assistant session could not be verified.",
        trace_id: "trace_auth_retry_1234",
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
      structured: {},
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

  const controller = window.CosmaNeuraPanel.mount({
    sovereignUrl: "https://sovereign.example",
    embedVersion: "1.0",
    getAuthToken: async request => {
      tokenRequests.push(request);
      if (request && request.forceRefresh === true) {
        rotateCalls += 1;
        mountToken = "opaque-token-session-b-abcdefghijkl";
      }
      return mountToken;
    },
    getContext: async () => ({
      clientId: currentRecord,
      thread_ref: `thread_${currentRecord}`,
      context_snapshot_ref: `snapshot_${currentRecord}`,
    }),
    onHumanContact: async action => {
      assert(Object.keys(action).sort().join(",") === "action_id,fallback_text,label",
             "human callback received more than the action descriptor");
      humanContacts += 1;
    },
    loadHistory: async () => ({
      thread_ref: `thread_${currentRecord}`,
      messages: [],
      next_cursor: null,
    }),
    onHistoryUpdated: async () => { historyNotifications += 1; },
  });

  const send = controller.root.querySelector(".cnp-send");
  const input = controller.root.querySelector(".cnp-in");
  assert(send.disabled === true, "send enabled before record handshake");
  await controller.setRecord("record_a");
  await Promise.resolve();
  assert(send.disabled === false, "send stayed disabled after record handshake");
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
  assert(tokenRequests[tokenRequests.length - 1] &&
         Object.keys(tokenRequests[tokenRequests.length - 1]).join(",") === "forceRefresh" &&
         tokenRequests[tokenRequests.length - 1].forceRefresh === true,
         "401 retry did not request exactly one forced refresh");
  assert(tokenRequests.filter(request => request && request.forceRefresh === true).length === 1,
         "401 retry requested more than one forced refresh");
  assert(rotateCalls === 1, "401 retry did not rotate the existing mount session exactly once");
  const sent = JSON.parse(chatRequests[0].body);
  assert(Object.keys(sent).sort().join(",") === "client_turn_id,message,metadata",
         "first send included a forbidden top-level field");
  assert(!("history" in sent) && !("persona" in sent), "first send replayed raw browser state");
  assert(find(controller.root, node => node.innerHTML.includes("Governed answer")),
         "successful governed answer was not rendered");

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
}

main().catch(error => {
  process.stderr.write(String(error.stack || error) + "\n");
  process.exitCode = 1;
});
