import { Type } from "typebox";
import { defineTool } from "@earendil-works/pi-coding-agent";

// pi's ToolDefinition.label is a static per-tool string, not per-call — pi's own
// tool_execution_start/end events carry only {toolCallId, toolName, args}, no label.
// The dynamic, per-call label the frontend shows ("Searching: mouse, wild…") is
// computed server-side by pi_translate.py, which imports
// helpers.tool_labels._tool_label(name, args) directly — no JS port, no drift risk.
function staticLabel(name) {
  return name
    .split("_")
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

/**
 * Recursively map a plain JSON-Schema object (as produced by
 * agent_tools.TOOL_SCHEMAS) to a TypeBox schema, carrying `description` and
 * `required` through. TOOL_SCHEMAS uses only object/array/string/integer today;
 * number/boolean are handled because they are one line each and are the obvious
 * next thing a tool author reaches for. Anything else degrades to Type.Unknown()
 * rather than throwing — extend the switch when a tool needs a richer shape.
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

// Schemas are fixed for the lifetime of the Flask process, and loadTools() runs
// on every session cache miss — i.e. once per chat. Memoised so that is one
// round trip for the sidecar's lifetime rather than one per conversation.
// Keyed by URL so a test harness pointing at its own stub is not served the
// real process's schemas.
const _schemaCache = new Map(); // flaskUrl -> Promise<schemas>

function fetchToolSchemas(flaskUrl, piSecret) {
  let pending = _schemaCache.get(flaskUrl);
  if (!pending) {
    pending = (async () => {
      const res = await fetch(`${flaskUrl}/api/internal/tools/schemas`, {
        headers: { "x-pi-secret": piSecret },
      });
      if (!res.ok) {
        throw new Error(`failed to fetch tool schemas: ${res.status} ${await res.text()}`);
      }
      return (await res.json()).tools;
    })().catch((err) => {
      // Never cache a failure — the next session should retry rather than
      // inherit a permanently broken tool list.
      _schemaCache.delete(flaskUrl);
      throw err;
    });
    _schemaCache.set(flaskUrl, pending);
  }
  return pending;
}

/**
 * Build one pi defineTool() per entry in Flask's TOOL_SCHEMAS (the single source
 * of truth). Each tool's execute() posts back to the same internal tool route the
 * schema came from — Flask enforces scope, dispatches to the existing
 * execute_tool(), and the tool set can never drift from qiita_explore's.
 */
export async function loadTools({ flaskUrl, piSecret, getToolToken, onToolResult }) {
  const schemas = await fetchToolSchemas(flaskUrl, piSecret);

  return schemas.map(({ function: fn }) =>
    defineTool({
      name: fn.name,
      label: staticLabel(fn.name),
      description: fn.description || "",
      parameters: jsonSchemaToTypeBox(fn.parameters || { type: "object", properties: {} }),
      async execute(_toolCallId, params, signal) {
        const res = await fetch(`${flaskUrl}/api/internal/tools/${fn.name}`, {
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
          // The Flask translator (pi_translate.py) maps this into a
          // segment_tool_result event with isError text.
          throw new Error(`${fn.name} failed: ${res.status} ${(await res.text()).slice(0, 200)}`);
        }
        const r = await res.json(); // {text, label, detail, ui_payload, executed}
        // Post-execution hook for callers tracking per-message tool budgets.
        // Deliberately generic — this file is generated wholesale from the
        // schemas Flask serves and must not know any tool by name; the
        // search_studies rule lives in sessions.mjs beside its block hook.
        onToolResult?.(fn.name, r);
        return {
          content: [{ type: "text", text: r.text ?? "" }],
          details: r,
        };
      },
    })
  );
}

export { jsonSchemaToTypeBox };
