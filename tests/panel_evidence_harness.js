"use strict";

// Render the actual response and its restored canonical history through the
// unchanged public panel mount API, including the real md()/esc() functions.
const fs = require("fs");
const vm = require("vm");
const { FakeElement, assert, response } = require("./panel_contract_harness");

async function main() {
  const input = JSON.parse(fs.readFileSync(0, "utf8"));
  const result = input.result;
  const body = new FakeElement("body");
  global.document = Object.assign(new FakeElement("document"), {
    body, head: new FakeElement("head"),
    createElement: tag => new FakeElement(tag),
    createElementNS: (_namespace, tag) => new FakeElement(tag),
  });
  global.window = {
    innerWidth: 1200, location: { origin: "https://host.example" },
    crypto: { randomUUID: () => result.client_turn_id },
  };
  let requests = 0;
  global.fetch = async (url, options) => {
    if (url.endsWith("/welcome")) return response(200, {
      persona: "provider", greeting: "Hello", disclaimer: "Fictional disclaimer",
      paths: input.preparation ? [{ intent: "session_prep", label: "Open Session Prep", message: "Open Session Prep" }] : [],
    });
    if (input.preparation && url.endsWith("/session-prep/structured-facts")) {
      requests += 1;
      return response(200, { ...input.preparation, client_turn_id: JSON.parse(options.body).client_turn_id });
    }
    assert(url.endsWith("/chat"), "unexpected request");
    requests += 1;
    assert(JSON.parse(options.body).metadata.output_format === "chat", "format changed");
    return response(200, result);
  };
  vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
  const common = { turn_ref: "turn_fictional_evidence", client_turn_id: result.client_turn_id,
                   recorded_at: "2026-10-20T12:00:00Z" };
  const history = {
    schema_version: "1.0.0",
    history: { thread_ref: "thread_fictional_evidence", next_cursor: null,
      messages: [{ ...common, role: "user", content: "A factual question.", presentation: null },
        { ...common, role: "assistant", content: result.answer, presentation: result.presentation }] },
    turns: [{ ...common, state: "completed", outcome: "completed" }],
  };
  const panel = window.CosmaNeuraPanel.mount({
    sovereignUrl: "https://sovereign.example", embedVersion: "1.0",
    getAuthToken: async () => "opaque-fictional-token-123456789",
    getContext: async () => ({ clientId: "record_fictional" }),
    loadHistory: async () => input.hydrated ? history : null,
    onHumanContact: async () => {},
  });
  await panel.setRecord("record_fictional");
  if (!input.hydrated) {
    if (input.preparation) await panel.openSessionPrep();
    else {
      panel.root.querySelector(".cnp-in").value = "A factual question.";
      await panel.root.querySelector(".cnp-send").click();
    }
  }
  assert(requests === (input.hydrated ? 0 : 1), "rendering issued extra chat requests");
  const bubbles = panel.root.querySelectorAll(".cnp-b");
  assert(bubbles.length > 0, "answer did not render");
  const html = bubbles[bubbles.length - 1].innerHTML;
  const literalTexts = panel.root.querySelector(".cnp-prep-view").querySelectorAll("*").map(node => node.textContent);
  process.stdout.write(JSON.stringify({ html, literalTexts }));
  panel.destroy();
}

main().catch(error => { process.stderr.write(String(error.stack || error) + "\n"); process.exitCode = 1; });
