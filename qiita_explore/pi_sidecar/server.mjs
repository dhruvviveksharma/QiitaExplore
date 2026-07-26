import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createModelRuntime, resolveModel } from "./model_runtime.mjs";
import { createSessionStore } from "./sessions.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const PORT = Number(process.env.PI_SIDECAR_PORT || 5100);
const HOST = process.env.PI_SIDECAR_HOST || "127.0.0.1";
const SECRET = process.env.PI_SIDECAR_SECRET;
const FLASK_URL = (process.env.FLASK_INTERNAL_URL || "http://127.0.0.1:5001").replace(/\/$/, "");
const STATE_DIR = process.env.PI_SIDECAR_STATE_DIR || path.join(__dirname, ".state");

if (!SECRET) {
  console.error("PI_SIDECAR_SECRET is required — refusing to start with an unauthenticated sidecar.");
  process.exit(1);
}

// Dedicated, always-empty cwd: no .pi/, no AGENTS.md, so pi's project-trust check
// short-circuits with no prompting (nothing here requires trust in the first
// place), and no repo files leak into what a coding-oriented tool could "read".
const CWD = path.join(STATE_DIR, "cwd");
const AGENT_DIR = path.join(STATE_DIR, "agent"); // isolated auth.json location; no models.json is read
const SESSION_DIR = path.join(STATE_DIR, "sessions");

const modelRuntime = await createModelRuntime({
  agentDir: AGENT_DIR,
  flaskUrl: FLASK_URL,
  piSecret: SECRET,
});
const sessionStore = createSessionStore({
  cwd: CWD,
  agentDir: AGENT_DIR,
  sessionDir: SESSION_DIR,
  modelRuntime,
  flaskUrl: FLASK_URL,
  piSecret: SECRET,
});

function sessionKeyFor(scope, chatId) {
  // pi session ids must match [A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]; chat_id is a
  // SQLite-generated id but we sanitize defensively rather than trust the caller.
  const safeChat = String(chatId).replace(/[^A-Za-z0-9._-]/g, "-");
  return `${scope}-${safeChat}`;
}

async function readJsonBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (chunks.length === 0) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function requireSecret(req, res) {
  if (req.headers["x-pi-secret"] !== SECRET) {
    res.writeHead(401, { "content-type": "application/json" }).end(JSON.stringify({ error: "unauthorized" }));
    return false;
  }
  return true;
}

async function handleHealthz(req, res) {
  res.writeHead(200, { "content-type": "application/json" }).end(
    JSON.stringify({ ok: true, cachedSessions: sessionStore.cacheSize() })
  );
}

async function handleChatStream(req, res) {
  let body;
  try {
    body = await readJsonBody(req);
  } catch {
    res.writeHead(400, { "content-type": "application/json" }).end(JSON.stringify({ error: "invalid JSON body" }));
    return;
  }

  const { scope, chat_id, user_id, session_file, model, system_prompt, context_block, message, tool_token } = body;
  if (!scope || !chat_id || !model || !message) {
    res.writeHead(400, { "content-type": "application/json" }).end(
      JSON.stringify({ error: "scope, chat_id, model, and message are required" })
    );
    return;
  }

  let resolvedModel;
  try {
    resolvedModel = resolveModel(modelRuntime, model);
  } catch (err) {
    res.writeHead(400, { "content-type": "application/json" }).end(JSON.stringify({ error: String(err.message) }));
    return;
  }

  const sessionKey = sessionKeyFor(scope, chat_id);

  let entry;
  try {
    entry = await sessionStore.getOrCreate(sessionKey, {
      model: resolvedModel, systemPrompt: system_prompt,
      // user_id scopes the session directory and the session cache. Flask is the
      // routing authority here — the same trust level it already has for
      // chat_id. session_file is the stored JSONL path, letting us skip the
      // directory scan; null for chats predating the pi_session_file column.
      userId: user_id, sessionFile: session_file,
    });
  } catch (err) {
    res.writeHead(500, { "content-type": "application/json" }).end(JSON.stringify({ error: String(err.message) }));
    return;
  }

  res.writeHead(200, {
    "content-type": "application/x-ndjson",
    "cache-control": "no-cache",
  });

  const writeLine = (obj) => {
    if (!res.writableEnded) res.write(JSON.stringify(obj) + "\n");
  };

  // Not a pi event: sidecar bookkeeping so Flask can persist the path of a
  // session we just created and stop scanning for it. Emitted before the turn so
  // it survives a turn that errors. pi_translate consumes it and does NOT
  // forward it to the browser.
  if (entry.createdSessionFile) {
    writeLine({ type: "sidecar_session", session_file: entry.createdSessionFile });
  }

  // Abort the in-flight turn if the client goes away before it settles — e.g.
  // Flask's generator was abandoned because the browser disconnected (a new
  // message sent before the previous one finished, or the tab closed).
  // Without this the turn keeps streaming into a dead socket — burning NRP
  // tokens — and holds the session non-idle (runTurn's serialization queue,
  // see sessions.mjs) until it finishes on its own, so the NEXT request for
  // this chat just waits behind the orphaned one instead of failing fast.
  let turnSettled = false;
  const onClientGone = () => {
    if (!turnSettled) sessionStore.abortSession(sessionKey, { userId: user_id }).catch(() => {});
  };
  res.on("close", onClientGone);

  try {
    await sessionStore.runTurn(entry, { message, contextBlock: context_block, toolToken: tool_token }, writeLine);
  } catch (err) {
    writeLine({ type: "sidecar_error", error: String(err && err.message ? err.message : err) });
  } finally {
    turnSettled = true;
    res.off("close", onClientGone);
    res.end();
  }
}

async function handleSessionDelete(req, res) {
  let body;
  try {
    body = await readJsonBody(req);
  } catch {
    res.writeHead(400, { "content-type": "application/json" }).end(JSON.stringify({ error: "invalid JSON body" }));
    return;
  }
  const { scope, chat_id, user_id, session_file } = body;
  if (!scope || !chat_id) {
    res.writeHead(400, { "content-type": "application/json" }).end(JSON.stringify({ error: "scope and chat_id are required" }));
    return;
  }
  await sessionStore.deleteSession(sessionKeyFor(scope, chat_id), { userId: user_id, sessionFile: session_file });
  res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ ok: true }));
}

async function handleAbort(req, res) {
  let body;
  try {
    body = await readJsonBody(req);
  } catch {
    res.writeHead(400, { "content-type": "application/json" }).end(JSON.stringify({ error: "invalid JSON body" }));
    return;
  }
  const { scope, chat_id, user_id } = body;
  if (!scope || !chat_id) {
    res.writeHead(400, { "content-type": "application/json" }).end(JSON.stringify({ error: "scope and chat_id are required" }));
    return;
  }
  await sessionStore.abortSession(sessionKeyFor(scope, chat_id), { userId: user_id });
  res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ ok: true }));
}

const ROUTES = {
  "GET /healthz": handleHealthz,
  "POST /chat/stream": handleChatStream,
  "POST /session/delete": handleSessionDelete,
  "POST /abort": handleAbort,
};

const server = http.createServer(async (req, res) => {
  if (!requireSecret(req, res)) return;
  const { pathname } = new URL(req.url, `http://${req.headers.host}`);
  const handler = ROUTES[`${req.method} ${pathname}`];
  if (!handler) {
    res.writeHead(404, { "content-type": "application/json" }).end(JSON.stringify({ error: "not found" }));
    return;
  }
  try {
    await handler(req, res);
  } catch (err) {
    console.error("unhandled error in", req.method, pathname, err);
    if (!res.headersSent) {
      res.writeHead(500, { "content-type": "application/json" }).end(JSON.stringify({ error: "internal error" }));
    } else if (!res.writableEnded) {
      res.end();
    }
  }
});

server.listen(PORT, HOST, () => {
  console.log(`pi sidecar listening on http://${HOST}:${PORT} (flask=${FLASK_URL}, state=${STATE_DIR})`);
});

for (const sig of ["SIGTERM", "SIGINT"]) {
  process.on(sig, () => {
    // server.close() alone only stops accepting NEW connections — it waits
    // indefinitely for existing ones to finish, and a live NDJSON chat stream
    // stays open for the whole turn. Without closeAllConnections() (Node
    // >=18.2; this sidecar already requires >=22), Ctrl-C on
    // start_barnacle.sh during an active turn hangs the shutdown until that
    // turn happens to finish on its own, instead of terminating promptly.
    server.close(() => process.exit(0));
    server.closeAllConnections();
  });
}
