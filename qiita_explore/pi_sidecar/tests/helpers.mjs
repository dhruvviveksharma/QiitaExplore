// Shared test fixtures: a stub Flask standing in for /api/internal/tools/*, and a
// faux-provider-backed session store. Offline and deterministic — no network, no
// real LLM, matches the pattern validated in the Phase 1 smoke test.
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fauxProvider } from "@earendil-works/pi-ai";
import { createModelRuntime } from "../model_runtime.mjs";
import { createSessionStore } from "../sessions.mjs";

export const SECRET = "test-secret";

// Mirrors config.py MODEL_METADATA's nrp+supports_tools rows. Only used when a
// test exercises the real NRP provider registration; the faux provider used by
// the turn tests bypasses it.
const STUB_MODELS = [{ id: "qwen3", contextWindow: 1_010_000 }];

export async function startFlaskStub({ toolSchemas, toolHandlers = {}, models = STUB_MODELS }) {
  const calls = [];
  const server = http.createServer(async (req, res) => {
    if (req.method === "GET" && req.url === "/api/internal/tools/schemas") {
      if (req.headers["x-pi-secret"] !== SECRET) {
        res.writeHead(401).end();
        return;
      }
      res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ tools: toolSchemas }));
      return;
    }
    if (req.method === "GET" && req.url === "/api/internal/models") {
      if (req.headers["x-pi-secret"] !== SECRET) {
        res.writeHead(401).end();
        return;
      }
      res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ models }));
      return;
    }
    const match = req.url.match(/^\/api\/internal\/tools\/([^/]+)$/);
    if (req.method === "POST" && match) {
      const name = match[1];
      // Mirror internal_tool_routes._guard(): the real route requires the
      // shared secret on tool calls too, not just on schema reads. Enforcing it
      // here means a sidecar that stops sending it fails the suite instead of
      // only failing in deployment.
      if (req.headers["x-pi-secret"] !== SECRET) {
        res.writeHead(401).end();
        return;
      }
      const chunks = [];
      for await (const c of req) chunks.push(c);
      const args = chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {};
      calls.push({
        name, args,
        toolToken: req.headers["x-tool-token"],
        piSecret: req.headers["x-pi-secret"],
      });
      const handler = toolHandlers[name];
      try {
        const result = handler
          ? await handler(args)
          : { text: `stub result for ${name}`, label: name, detail: "", ui_payload: null };
        res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(result));
      } catch (err) {
        res.writeHead(err.httpStatus || 500, { "content-type": "application/json" }).end(
          JSON.stringify({ error: String(err.message || err) })
        );
      }
      return;
    }
    res.writeHead(404).end();
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  return { url, calls, close: () => new Promise((r) => server.close(r)) };
}

export async function createTestHarness({ toolSchemas, toolHandlers, fauxModels }) {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-sidecar-test-"));
  const flask = await startFlaskStub({ toolSchemas, toolHandlers });

  const modelRuntime = await createModelRuntime({
    agentDir: path.join(stateDir, "agent"),
    flaskUrl: flask.url,
    piSecret: SECRET,
  });
  const faux = fauxProvider({
    models: fauxModels || [{ id: "faux-1", contextWindow: 100000, maxTokens: 4096 }],
  });
  modelRuntime.registerNativeProvider(faux.provider);

  const sessionStore = createSessionStore({
    cwd: path.join(stateDir, "cwd"),
    agentDir: path.join(stateDir, "agent"),
    sessionDir: path.join(stateDir, "sessions"),
    modelRuntime,
    flaskUrl: flask.url,
    piSecret: SECRET,
  });

  const model = modelRuntime.getModel("faux", (fauxModels || [{ id: "faux-1" }])[0].id);

  async function cleanup() {
    await flask.close();
    fs.rmSync(stateDir, { recursive: true, force: true });
  }

  return { stateDir, flask, faux, model, sessionStore, cleanup };
}

export async function runTurnCollectingEvents(sessionStore, entry, turnInput) {
  const events = [];
  await sessionStore.runTurn(entry, turnInput, (e) => events.push(e));
  return events;
}
