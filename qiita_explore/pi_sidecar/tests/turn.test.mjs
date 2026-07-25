import { test, describe } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { fauxAssistantMessage, fauxToolCall } from "@earendil-works/pi-ai";
import { createTestHarness, runTurnCollectingEvents } from "./helpers.mjs";

const SEARCH_STUDIES_SCHEMA = {
  type: "function",
  function: {
    name: "search_studies",
    description: "Search",
    parameters: { type: "object", properties: { keywords: { type: "array", items: { type: "string" } } }, required: [] },
  },
};

describe("full turn via faux provider (offline, deterministic)", () => {
  test("tool call executes, result reaches the model, turn settles", async () => {
    const h = await createTestHarness({
      toolSchemas: [SEARCH_STUDIES_SCHEMA],
      toolHandlers: {
        search_studies: async () => ({
          text: "Found study 11043",
          label: "Search complete",
          detail: "1 study",
          ui_payload: { kind: "tool_call", tool: "search_studies", result_studies: [{ study_id: 11043 }] },
        }),
      },
    });
    try {
      h.faux.setResponses([
        fauxAssistantMessage([fauxToolCall("search_studies", { keywords: ["mice"] })], { stopReason: "toolUse" }),
        fauxAssistantMessage("Study 11043 matches."),
      ]);
      const entry = await h.sessionStore.getOrCreate("global-t1", { model: h.model, systemPrompt: "test" });
      const events = await runTurnCollectingEvents(h.sessionStore, entry, { message: "find mice studies" });

      const toolEnd = events.find((e) => e.type === "tool_execution_end");
      assert.equal(toolEnd.isError, false);
      assert.equal(toolEnd.result.details.ui_payload.result_studies[0].study_id, 11043);
      assert.ok(events.some((e) => e.type === "agent_settled"));
      assert.equal(h.flask.calls.length, 1);
    } finally {
      await h.cleanup();
    }
  });

  test("context_block reaches the LLM request but is not persisted to the session file", async () => {
    const h = await createTestHarness({ toolSchemas: [], toolHandlers: {} });
    try {
      let seenContext = null;
      h.faux.setResponses([
        (context) => {
          seenContext = context;
          return fauxAssistantMessage("ack");
        },
      ]);
      const entry = await h.sessionStore.getOrCreate("global-t2", { model: h.model, systemPrompt: "test" });
      await runTurnCollectingEvents(h.sessionStore, entry, {
        message: "hello",
        contextBlock: "PINNED STUDY REPORTS:\nstudy 99999 canary-xyz",
      });

      assert.ok(JSON.stringify(seenContext).includes("canary-xyz"), "context reached the provider request");

      const sessionFiles = fs.readdirSync(`${h.stateDir}/sessions`).filter((f) => f.endsWith(".jsonl"));
      assert.equal(sessionFiles.length, 1);
      const persisted = fs.readFileSync(`${h.stateDir}/sessions/${sessionFiles[0]}`, "utf8");
      assert.ok(!persisted.includes("canary-xyz"), "context block must not leak into the persisted transcript");
      assert.ok(persisted.includes("hello"), "the real user message is still persisted verbatim");
    } finally {
      await h.cleanup();
    }
  });

  test("tool failure (non-2xx from Flask) produces isError:true, turn still settles", async () => {
    const h = await createTestHarness({
      toolSchemas: [SEARCH_STUDIES_SCHEMA],
      toolHandlers: {
        search_studies: async () => {
          throw Object.assign(new Error("db unreachable"), { httpStatus: 500 });
        },
      },
    });
    try {
      h.faux.setResponses([
        fauxAssistantMessage([fauxToolCall("search_studies", { keywords: ["mice"] })], { stopReason: "toolUse" }),
        fauxAssistantMessage("Sorry, search failed."),
      ]);
      const entry = await h.sessionStore.getOrCreate("global-t3", { model: h.model, systemPrompt: "test" });
      const events = await runTurnCollectingEvents(h.sessionStore, entry, { message: "find mice studies" });

      const toolEnd = events.find((e) => e.type === "tool_execution_end");
      assert.equal(toolEnd.isError, true);
      assert.ok(events.some((e) => e.type === "agent_settled"), "a tool failure must not hang the turn");
    } finally {
      await h.cleanup();
    }
  });

  test("one-search-per-turn: a second search_studies call in the same turn is blocked, not executed", async () => {
    const h = await createTestHarness({
      toolSchemas: [SEARCH_STUDIES_SCHEMA],
      toolHandlers: {
        search_studies: async (args) => ({
          text: `results for ${JSON.stringify(args.keywords)}`,
          label: "Search complete",
          detail: "",
          ui_payload: null,
        }),
      },
    });
    try {
      // A model that (incorrectly) tries to call search_studies twice in one turn.
      h.faux.setResponses([
        fauxAssistantMessage(
          [fauxToolCall("search_studies", { keywords: ["mice"] }), fauxToolCall("search_studies", { keywords: ["rats"] })],
          { stopReason: "toolUse" }
        ),
        fauxAssistantMessage("done"),
      ]);
      const entry = await h.sessionStore.getOrCreate("global-t4", { model: h.model, systemPrompt: "test" });
      const events = await runTurnCollectingEvents(h.sessionStore, entry, { message: "find mice and rat studies" });

      const toolEnds = events.filter((e) => e.type === "tool_execution_end");
      assert.equal(toolEnds.length, 2, "both calls still produce a start/end pair");
      assert.equal(toolEnds.filter((e) => e.isError === false).length, 1, "exactly one call actually executed");
      assert.equal(toolEnds.filter((e) => e.isError === true).length, 1, "the second call is blocked as an error");
      assert.equal(h.flask.calls.length, 1, "Flask only ever saw the first call");
    } finally {
      await h.cleanup();
    }
  });

  test("one-search-per-message: a second search_studies on a LATER tool round is blocked", async () => {
    // Regression: pi emits turn_start once per LLM round, not per user
    // message, so a gate that reset on turn_start let the model re-run
    // search_studies on every subsequent round. Observed live making three
    // search calls for one message. The blocking test above never caught it
    // because it put both calls in a single assistant message (one round);
    // this puts them in SEPARATE rounds, which is the real-world shape.
    const h = await createTestHarness({
      toolSchemas: [SEARCH_STUDIES_SCHEMA],
      toolHandlers: {
        search_studies: async () => ({ text: "ok", label: "Search complete", detail: "", ui_payload: null }),
      },
    });
    try {
      h.faux.setResponses([
        fauxAssistantMessage([fauxToolCall("search_studies", { keywords: ["mice"] })], { stopReason: "toolUse" }),
        // second LLM round of the SAME user message — tries to search again
        fauxAssistantMessage([fauxToolCall("search_studies", { keywords: ["rats"] })], { stopReason: "toolUse" }),
        fauxAssistantMessage("done"),
      ]);
      const entry = await h.sessionStore.getOrCreate("global-t6", { model: h.model, systemPrompt: "test" });
      const events = await runTurnCollectingEvents(h.sessionStore, entry, { message: "find mice and rat studies" });

      const toolEnds = events.filter((e) => e.type === "tool_execution_end");
      assert.equal(toolEnds.filter((e) => e.isError === false).length, 1, "only the first search executes");
      assert.equal(toolEnds.filter((e) => e.isError === true).length, 1, "the later-round search is blocked");
      assert.equal(h.flask.calls.length, 1, "Flask only ever saw one search_studies call for this message");
      assert.ok(
        events.filter((e) => e.type === "turn_start").length > 1,
        "sanity: this really was a multi-round turn, which is what makes the test meaningful"
      );
    } finally {
      await h.cleanup();
    }
  });

  test("one-search-per-message: the gate resets on the next user message", async () => {
    const h = await createTestHarness({
      toolSchemas: [SEARCH_STUDIES_SCHEMA],
      toolHandlers: {
        search_studies: async () => ({ text: "ok", label: "Search complete", detail: "", ui_payload: null }),
      },
    });
    try {
      h.faux.setResponses([
        fauxAssistantMessage([fauxToolCall("search_studies", { keywords: ["mice"] })], { stopReason: "toolUse" }),
        fauxAssistantMessage("turn 1 done"),
        fauxAssistantMessage([fauxToolCall("search_studies", { keywords: ["rats"] })], { stopReason: "toolUse" }),
        fauxAssistantMessage("turn 2 done"),
      ]);
      const entry = await h.sessionStore.getOrCreate("global-t5", { model: h.model, systemPrompt: "test" });
      await runTurnCollectingEvents(h.sessionStore, entry, { message: "turn one" });
      await runTurnCollectingEvents(h.sessionStore, entry, { message: "turn two" });

      assert.equal(h.flask.calls.length, 2, "search_studies is allowed again in a fresh turn");
    } finally {
      await h.cleanup();
    }
  });
});
