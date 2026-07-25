import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { jsonSchemaToTypeBox, loadTools } from "../tools.mjs";
import { startFlaskStub, SECRET } from "./helpers.mjs";

const SEARCH_STUDIES_SCHEMA = {
  type: "function",
  function: {
    name: "search_studies",
    description: "Search studies",
    parameters: {
      type: "object",
      properties: {
        keywords: { type: "array", items: { type: "string" }, description: "terms" },
        limit: { type: "integer", description: "cap" },
      },
      required: [],
    },
  },
};

const GET_STUDY_REPORT_SCHEMA = {
  type: "function",
  function: {
    name: "get_study_report",
    description: "Load a study",
    parameters: {
      type: "object",
      properties: { study_id: { type: "integer", description: "id" } },
      required: ["study_id"],
    },
  },
};

const SEARCH_BY_SAMPLE_SCHEMA = {
  type: "function",
  function: {
    name: "search_by_sample",
    description: "Search by sample",
    parameters: {
      type: "object",
      properties: {
        field_filters: {
          type: "array",
          items: {
            type: "object",
            properties: { field: { type: "string" }, value: { type: "string" } },
            required: ["field", "value"],
          },
        },
      },
      required: [],
    },
  },
};

describe("jsonSchemaToTypeBox", () => {
  test("maps object/integer and marks required properties", () => {
    const schema = jsonSchemaToTypeBox(GET_STUDY_REPORT_SCHEMA.function.parameters);
    assert.equal(schema.type, "object");
    assert.equal(schema.properties.study_id.type, "integer");
    // TypeBox 1.x computes `required` from which properties were NOT wrapped in
    // Type.Optional() — this is the real, load-bearing contract to verify, not
    // an internal marker on the property schema itself.
    assert.deepEqual(schema.required, ["study_id"]);
  });

  test("properties omitted from TOOL_SCHEMAS' required[] are optional (absent from computed required)", () => {
    const schema = jsonSchemaToTypeBox(SEARCH_STUDIES_SCHEMA.function.parameters);
    assert.equal(schema.properties.keywords.type, "array");
    assert.equal(schema.properties.keywords.items.type, "string");
    assert.ok(!schema.required || !schema.required.includes("keywords"));
    assert.ok(!schema.required || !schema.required.includes("limit"));
  });

  test("nested object-in-array (field_filters) round-trips its own required[]", () => {
    const schema = jsonSchemaToTypeBox(SEARCH_BY_SAMPLE_SCHEMA.function.parameters);
    const ff = schema.properties.field_filters;
    assert.equal(ff.type, "array");
    assert.equal(ff.items.type, "object");
    assert.deepEqual(ff.items.required, ["field", "value"]);
    assert.ok(!schema.required || !schema.required.includes("field_filters"));
  });
});

describe("loadTools — schema fetch and tool generation", () => {
  test("builds one tool per schema entry returned by Flask", async () => {
    const flask = await startFlaskStub({
      toolSchemas: [SEARCH_STUDIES_SCHEMA, GET_STUDY_REPORT_SCHEMA, SEARCH_BY_SAMPLE_SCHEMA],
    });
    try {
      const tools = await loadTools({ flaskUrl: flask.url, piSecret: SECRET, getToolToken: () => "tok" });
      assert.deepEqual(
        tools.map((t) => t.name).sort(),
        ["get_study_report", "search_by_sample", "search_studies"]
      );
    } finally {
      await flask.close();
    }
  });

  test("rejects with a clear error when Flask's schema endpoint is unreachable/unauthorized", async () => {
    const flask = await startFlaskStub({ toolSchemas: [] });
    try {
      await assert.rejects(
        () => loadTools({ flaskUrl: flask.url, piSecret: "wrong-secret", getToolToken: () => "tok" }),
        /failed to fetch tool schemas: 401/
      );
    } finally {
      await flask.close();
    }
  });
});

describe("tool wrapper execute()", () => {
  test("posts the raw args as JSON body with the current tool token", async () => {
    const flask = await startFlaskStub({
      toolSchemas: [GET_STUDY_REPORT_SCHEMA],
      toolHandlers: {
        get_study_report: async (args) => ({
          text: `report for ${args.study_id}`,
          label: "Loaded",
          detail: "ok",
          ui_payload: { kind: "samples_report", study_id: args.study_id },
        }),
      },
    });
    try {
      let currentToken = "token-v1";
      const tools = await loadTools({ flaskUrl: flask.url, piSecret: SECRET, getToolToken: () => currentToken });
      const tool = tools.find((t) => t.name === "get_study_report");

      const result = await tool.execute("call-1", { study_id: 11043 }, undefined, undefined);

      assert.equal(flask.calls.length, 1);
      assert.deepEqual(flask.calls[0].args, { study_id: 11043 });
      assert.equal(flask.calls[0].toolToken, "token-v1");
      // Both gates on every tool call — the scope token alone is not enough
      // for the real route (internal_tool_routes._guard).
      assert.equal(flask.calls[0].piSecret, SECRET);
      assert.equal(result.content[0].text, "report for 11043");
      assert.equal(result.details.ui_payload.study_id, 11043);

      // Token rotates per-turn via the getToolToken() closure, not baked in at tool build time.
      currentToken = "token-v2";
      await tool.execute("call-2", { study_id: 99 }, undefined, undefined);
      assert.equal(flask.calls[1].toolToken, "token-v2");
    } finally {
      await flask.close();
    }
  });

  test("maps {text,label,detail,ui_payload} into {content,details} exactly", async () => {
    const flask = await startFlaskStub({
      toolSchemas: [GET_STUDY_REPORT_SCHEMA],
      toolHandlers: {
        get_study_report: async () => ({ text: "T", label: "L", detail: "D", ui_payload: { a: 1 } }),
      },
    });
    try {
      const tools = await loadTools({ flaskUrl: flask.url, piSecret: SECRET, getToolToken: () => "tok" });
      const tool = tools.find((t) => t.name === "get_study_report");
      const result = await tool.execute("call-1", { study_id: 1 }, undefined, undefined);
      assert.deepEqual(result, {
        content: [{ type: "text", text: "T" }],
        details: { text: "T", label: "L", detail: "D", ui_payload: { a: 1 } },
      });
    } finally {
      await flask.close();
    }
  });

  test("throws (never returns error-as-content) on a non-2xx response", async () => {
    const flask = await startFlaskStub({
      toolSchemas: [GET_STUDY_REPORT_SCHEMA],
      toolHandlers: {
        get_study_report: async () => {
          throw Object.assign(new Error("boom"), { httpStatus: 500 });
        },
      },
    });
    try {
      const tools = await loadTools({ flaskUrl: flask.url, piSecret: SECRET, getToolToken: () => "tok" });
      const tool = tools.find((t) => t.name === "get_study_report");
      await assert.rejects(() => tool.execute("call-1", { study_id: 1 }, undefined, undefined), /failed: 500/);
    } finally {
      await flask.close();
    }
  });
});
