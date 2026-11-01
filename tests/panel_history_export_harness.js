"use strict";

// The admitted original fixture is preserved in SOURCE9 local test evidence.
// These same provider case purposes now require a fresh closed1.3 display read
// and authoritative export clocks, rather than downloading cached answer bytes.
const { createEnvironment, fixture, verifiedExport, STANDARD, SUPPRESSIONS,
  CITATION, assert, find } = require("./panel_answer_expiry_harness");

const ANSWER = "# Fictional saved answer\r\n\r\nExact café 🩺 text & <draft>.\n" +
  "Sources: words in the answer are not saved citation metadata.\n";
const FACTUAL_ANSWER = "Selected reviewed-note excerpts for your question:\n\n" +
  "[1] 2026-10-08 — Medications\n\n" +
  "> Fictional reviewed dose: 25 mg daily supersedes the earlier erroneous 50 mg instruction.\n\n" +
  "Source: 1 selected reviewed prior note through 2026-10-08.\n";

async function main() {
  const env = createEnvironment(process.argv[2]);
  const mount = options => fixture(env, { answer: ANSWER, citations: [], ...options });
  const restored = await mount();
  assert(restored.save() && !restored.download(), "restored answer was not distinguished from an original Document response");
  assert(!find(restored.panel.root, node => ["cnp-cite", "cnp-trust", "cnp-badge"].includes(node.className)),
    "history invented citation, verdict or intent metadata");
  await verifiedExport(env, restored, restored.save());
  assert(restored.chats().length === 0 && restored.reads().length >= 2, "hydration/export ran chat/model requests or omitted fresh reads");
  assert(restored.panel.root.innerHTML.includes("a visit summary or an ethics consult") &&
    !restored.panel.root.innerHTML.includes("a draft from this record"), "Document helper misstates supported drafts");
  const format = restored.panel.root.querySelector(".cnp-fmt"); format.value = "document";
  format._listeners.find(([event]) => event === "change")[1]();
  assert(!restored.panel.root.querySelector(".cnp-format-help").hidden &&
    restored.input.placeholder === "Request a supported document draft…", "Document help or placeholder still imposes a chart-only request");
  await restored.end();

  for (const presentation of [
    { ...STANDARD, suppress: ["feature_actions"] },
    { mode: "emergency", suppress: [], human_contact: null },
    { mode: "sensitive_disclosure", suppress: SUPPRESSIONS, human_contact: {
      label: "Contact care team", action_id: "care-team-contact", fallback_text: "Contact your care team." } },
  ]) {
    const denied = await mount({ presentation });
    assert(!denied.save() && !denied.download(), "governed suppression exposed export"); await denied.end();
  }
  for (const state of ["pending", "stale"]) {
    const incomplete = await mount({ state });
    assert(!incomplete.save() && !incomplete.download(), "pending/stale history exposed export"); await incomplete.end();
  }

  const switching = await mount(), oldSave = switching.save(), beforeSwitch = env.blobs.length;
  switching.record = "record_synthetic_other"; switching.initial = null; switching.hasTurn = false;
  const switched = switching.panel.setRecord(switching.record);
  await oldSave.click(); await switched; await oldSave.click();
  assert(env.blobs.length === beforeSwitch && !switching.save(), "retained prior-record action exported history"); await switching.end();

  const revoked = await mount(), revokedSave = revoked.save(), beforeRevoke = env.blobs.length;
  revoked.chatMode = "denied"; await revoked.ask("A fictional follow-up"); await revokedSave.click();
  assert(env.blobs.length === beforeRevoke && !revoked.save() && !revoked.panel.root.querySelector(".cnp-gate").hidden,
    "revocation left saved-answer export active"); await revoked.end();

  const bootstrapDenied = await mount(), deniedSave = bootstrapDenied.save(), beforeDenied = env.blobs.length;
  bootstrapDenied.welcomeDenied = true; await bootstrapDenied.panel.setRecord(bootstrapDenied.record); await deniedSave.click();
  assert(env.blobs.length === beforeDenied && !bootstrapDenied.save(), "failed authorization retained export"); await bootstrapDenied.end();

  const recovery = await mount({ initial: null, hasTurn: false, chatMode: "lost-final" });
  await recovery.ask("A fictional document request");
  const chatCount = recovery.chats().length; recovery.input.value = "Preserve my newer draft";
  await recovery.button("Check response").click();
  assert(recovery.save() && !recovery.download(), "response recovery lost exact saved-answer export");
  await verifiedExport(env, recovery, recovery.save());
  assert(recovery.chats().length === chatCount && recovery.input.value === "Preserve my newer draft",
    "recovery replayed model or replaced draft");
  const destroyedSave = recovery.save(), beforeDestroy = env.blobs.length;
  await recovery.end(); await destroyedSave.click(); assert(env.blobs.length === beforeDestroy, "destroyed panel retained export action");

  // Fresh Chat and Document actions still save the canonical final response;
  // the timed server read must verify this exact client turn first.
  for (const selected of ["chat", "document"]) {
    const live = await mount({ initial: null, hasTurn: false,
      answer: selected === "chat" ? FACTUAL_ANSWER : ANSWER,
      citations: selected === "chat" ? [CITATION] : [],
      chatIntent: selected === "chat" ? "visit_prep" : "ethics_consult" });
    live.panel.root.querySelector(".cnp-fmt").value = selected;
    await live.ask(selected === "chat" ? "What did the fictional reviewed note document about the dose correction?" : "A fictional supported draft");
    assert(Boolean(live.save()) === (selected === "chat") && Boolean(live.download()) === (selected === "document"),
      "fresh Chat export or existing Document action is missing or duplicated");
    if (selected === "chat") {
      const freshSave = live.save(); await verifiedExport(env, live, freshSave, FACTUAL_ANSWER);
      const beforeFreshSwitch = env.blobs.length; live.record = "record_synthetic_other"; live.hasTurn = false;
      const changed = live.panel.setRecord(live.record); await freshSave.click(); await changed; await freshSave.click();
      assert(env.blobs.length === beforeFreshSwitch && !live.save(), "retained fresh-answer action exported after the record switched");
      await live.end(); await freshSave.click(); assert(env.blobs.length === beforeFreshSwitch, "destroyed fresh-answer panel retained export");
    } else {
      await verifiedExport(env, live, live.download(), ANSWER, "cosmaneura-ethics_consult-"); await live.end();
    }
  }
  for (const presentation of [
    { ...STANDARD, suppress: ["feature_actions"] },
    { mode: "emergency", suppress: [], human_contact: null },
    { mode: "sensitive_disclosure", suppress: SUPPRESSIONS, human_contact: {
      label: "Contact care team", action_id: "care-team-contact", fallback_text: "Contact your care team." } },
  ]) {
    const live = await mount({ initial: null, hasTurn: false, presentation, answer: FACTUAL_ANSWER });
    await live.ask("A fictional reviewed-note question");
    assert(!live.save() && !live.download(), "governed fresh Chat presentation exposed export"); await live.end();
  }
  process.stdout.write("Provider saved-answer export: fresh clocks, exact bytes, Document, recovery, suppression and record/access guards passed.\n");
}
main().catch(error => { process.stderr.write(String(error.stack || error) + "\n"); process.exitCode = 1; });
