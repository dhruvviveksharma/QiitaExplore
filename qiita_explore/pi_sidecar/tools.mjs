import { Type } from "typebox";
import { defineTool } from "@earendil-works/pi-coding-agent";

// pi's ToolDefinition.label is a static per-tool string, not per-call — pi's own
// tool_execution_start/end events carry only {toolCallId, toolName, args}, no label.
// The dynamic, per-call label the frontend shows ("Searching: mouse, wild…") is
// computed server-side by pi_translate.py, which imports the REAL
// helpers.agent._tool_label(name, args) directly — no JS port, no drift risk.
function staticLabel(name) {
  return name
    .split("_")
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

/**
 * Recursively map a plain JSON-Schema object (as produced by
 * agent_tools.TOOL_SCHEMAS) to a TypeBox schema. Covers exactly what
 * TOOL_SCHEMAS actually uses today: object/string/integer/array, description,
 * required. Extend if a new tool needs a shape this doesn't cover yet.
 */
function jsonSchemaToTypeBox(schema) {
  if (!schema || typeof schema !== "object") return Type.Unknown();
  const opts = schema.description ? { description: schema.description } : {};

  switch (schema.type) {
    case "object": {
      const required = new Set(schema.required || []);
      const props = {};
      for (const [key, propSchema] of Object.entries(schema.properties || {})) {
        const mapped = jsonSchemaToTypeBox(propSchema);
        props[key] = required.has(key) ? mapped : Type.Optional(mapped);
      }
      return Type.Object(props, opts);
    }
    case "array":
      return Type.Array(jsonSchemaToTypeBox(schema.items || {}), opts);
    case "string":
      return Type.String(opts);
    case "integer":
      return Type.Integer(opts);
    case "number":
      return Type.Number(opts);
    case "boolean":
      return Type.Boolean(opts);
    default:
      return Type.Unknown(opts);
  }
}

/**
 * Fetch the tool schemas from Flask (single source of truth: agent_tools.TOOL_SCHEMAS)
 * and build one pi defineTool() per entry. Each tool's execute() posts back to the
 * same internal tool route the schema came from — Flask enforces scope, dispatches to
 * the existing execute_tool(), and the tool set can never drift from qiita_explore's.
 */
export async function loadTools({ flaskUrl, piSecret, getToolToken, fetchImpl = fetch }) {
  const res = await fetchImpl(`${flaskUrl}/api/internal/tools/schemas`, {
    headers: { "x-pi-secret": piSecret },
  });
  if (!res.ok) {
    throw new Error(`failed to fetch tool schemas: ${res.status} ${await res.text()}`);
  }
  const { tools: schemas } = await res.json();

  return schemas.map(({ function: fn }) =>
    defineTool({
      name: fn.name,
      label: staticLabel(fn.name),
      description: fn.description || "",
      parameters: jsonSchemaToTypeBox(fn.parameters || { type: "object", properties: {} }),
      async execute(_toolCallId, params, signal) {
        const res = await fetchImpl(`${flaskUrl}/api/internal/tools/${fn.name}`, {
          method: "POST",
          signal,
          headers: {
            "content-type": "application/json",
            // Both gates on every call: the shared secret proves this process
            // is the sidecar, the scope token proves which user/chat/workspace
            // the call belongs to. Flask requires both (internal_tool_routes._guard).
            "x-pi-secret": piSecret,
            "x-tool-token": getToolToken(),
          },
          body: JSON.stringify(params),
        });
        if (!res.ok) {
          // pi's contract: throw on failure, never encode errors as content.
          // The Flask translator maps this to the same failure segment_tool_result
          // shape helpers/agent.py:34-40 emits today.
          throw new Error(`${fn.name} failed: ${res.status} ${(await res.text()).slice(0, 200)}`);
        }
        const r = await res.json(); // {text, label, detail, ui_payload}
        return {
          content: [{ type: "text", text: r.text ?? "" }],
          details: r,
        };
      },
    })
  );
}

export { jsonSchemaToTypeBox };
