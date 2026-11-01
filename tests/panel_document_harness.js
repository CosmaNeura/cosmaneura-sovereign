"use strict";

// Exercise the unchanged real panel with the response from the governed HTTP test.
const fs = require("fs");
const vm = require("vm");
const { FakeElement, assert, find, response, timedHistory } = require("./panel_contract_harness");

async function main() {
  const result = JSON.parse(fs.readFileSync(0, "utf8"));
  const format = process.argv[3];
  const body = new FakeElement("body");
  global.document = Object.assign(new FakeElement("document"), {
    body, head: new FakeElement("head"),
    createElement: tag => new FakeElement(tag),
    createElementNS: (_namespace, tag) => new FakeElement(tag),
  });
  global.window = Object.assign(new FakeElement("window"), {
    innerWidth: 1200, location: { origin: "https://host.example" },
    crypto: { randomUUID: () => result.client_turn_id },
  });
  let requests = 0, downloaded = null;
  URL.createObjectURL = blob => { downloaded = blob; return "blob:fictional-document"; };
  global.fetch = async (url, options) => {
    if (url.endsWith("/welcome")) return response(200, {
      persona: "provider", greeting: "Hello", paths: [], disclaimer: "Fictional disclaimer",
    });
    if (url.endsWith("/embed/history-timed")) {
      const common = { turn_ref: "turn_fictional_document", client_turn_id: result.client_turn_id,
        recorded_at: "2026-10-20T12:00:00Z" };
      return response(200, timedHistory({ schema_version: "1.0.0",
        history: { thread_ref: "thread_fictional_document", next_cursor: null,
          messages: JSON.parse(options.body).mode === "turn" ? [
            { ...common, role: "user", content: "A fictional document request.", presentation: null },
            { ...common, role: "assistant", content: result.answer, presentation: result.presentation },
          ] : [] },
        turns: JSON.parse(options.body).mode === "turn" ? [{ ...common, state: "completed", outcome: "completed" }] : [],
      }));
    }
    assert(url.endsWith("/chat"), "unexpected request");
    requests += 1;
    assert(JSON.parse(options.body).metadata.output_format === format, "selected format was lost");
    return response(200, result);
  };
  vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
  const panel = window.CosmaNeuraPanel.mount({
    sovereignUrl: "https://sovereign.example", embedVersion: "1.0",
    getAuthToken: async () => "opaque-fictional-token-123456789",
    getContext: async () => ({ clientId: "record_fictional" }),
    onHumanContact: async () => {},
  });
  await panel.setRecord("record_fictional");
  await panel.open();
  panel.root.querySelector(".cnp-fmt").value = format;
  panel.root.querySelector(".cnp-in").value = "Explain that more briefly.";
  await panel.root.querySelector(".cnp-send").click();
  assert(requests === 1, "rendering issued extra chat requests");
  const download = find(panel.root, node => node.textContent === "Download .md");
  assert(Boolean(download) === (format === "document"), "download did not match the final format");
  if (download) {
    await download.click();
    assert(downloaded && downloaded.type === "text/markdown", "download did not create markdown");
    const exported = await downloaded.text(), start = exported.indexOf(result.answer);
    assert(start >= 0 && exported.slice(start, start + result.answer.length) === result.answer,
      "download differs from the canonical final answer");
    assert(exported.includes("Answer recorded: 2026-10-20T12:00:00Z") &&
      exported.includes("Export verified: 2026-11-01T12:00:00Z") &&
      exported.includes("Available until: 2026-11-02T12:00:00Z"), "provider download lost authoritative export clocks");
    assert(requests === 1, "download issued another chat request");
  }
  panel.destroy();
}
main().catch(error => { process.stderr.write(String(error.stack || error) + "\n"); process.exitCode = 1; });
