import fs from "node:fs";
import {
  SessionManager,
  SettingsManager,
  DefaultResourceLoader,
  createAgentSession,
} from "@earendil-works/pi-coding-agent";
import { loadTools } from "./tools.mjs";

const IDLE_EVICT_MS = 30 * 60 * 1000;
const SWEEP_INTERVAL_MS = 5 * 60 * 1000;

// pi's own session id rule (session-manager.ts): first/last char alnum, body
// alnum/dot/underscore/dash. Not re-exported from the package's public entry
// point, so mirrored here rather than reaching into internal paths.
const VALID_SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]$|^[A-Za-z0-9]$/;
function assertValidSessionId(id) {
  if (!VALID_SESSION_ID.test(id)) {
    throw new Error(`invalid session id: ${JSON.stringify(id)}`);
  }
}

/**
 * Inline extension (a plain function, no file, no jiti compile) that injects the
 * per-turn context_block into the message sent to the provider WITHOUT persisting
 * it to the session file. This is pi's documented mechanism for exactly this case
 * (the `context` hook fires before every provider request with a rewritable deep
 * copy of the message list). Prepending to the existing final user message, rather
 * than inserting a new adjacent message, sidesteps any provider role-alternation
 * requirements entirely.
 */
function makeContextInjectionExtension(contextRef) {
  return function contextInjectionExtension(pi) {
    pi.on("context", ({ messages }) => {
      if (!contextRef.block) return {};
      const copy = messages.map((m) => ({ ...m }));
      const last = copy[copy.length - 1];
      if (!last || last.role !== "user") return {};
      const wrapped = `<context>\n${contextRef.block}\n</context>\n\n`;
      if (typeof last.content === "string") {
        copy[copy.length - 1] = { ...last, content: wrapped + last.content };
      } else if (Array.isArray(last.content)) {
        const content = last.content.slice();
        const firstText = content.findIndex((c) => c.type === "text");
        if (firstText >= 0) {
          content[firstText] = { ...content[firstText], text: wrapped + content[firstText].text };
        } else {
          content.unshift({ type: "text", text: wrapped.trimEnd() });
        }
        copy[copy.length - 1] = { ...last, content };
      }
      return { messages: copy };
    });
  };
}

/**
 * One-search-per-user-message invariant. The Python loop enforces this by
 * removing search_studies from the tool schema after first use and leaving it
 * removed for the rest of that stream_agent() call (helpers/agent.py:196-198)
 * — i.e. scoped to the whole user message, not to one LLM round. pi has no
 * mid-run schema mutation (setActiveTools takes effect "on the next agent
 * turn"), so the `tool_call` block hook is the only mechanism available. A
 * blocked call still gets a tool_execution_start/end pair (isError:true,
 * reason as the result text) so the model sees why and proceeds with what it
 * already has.
 *
 * Reset on `agent_start`, NOT `turn_start`: pi emits agent_start once per user
 * message but turn_start once per LLM round, so resetting on turn_start let
 * the model re-run search_studies on every subsequent tool round — observed
 * live doing exactly that (three search calls in one message) before this was
 * corrected.
 */
function makeSearchOncePerMessageExtension() {
  return function searchOncePerMessageExtension(pi) {
    let searchCalledThisMessage = false;
    pi.on("agent_start", () => {
      searchCalledThisMessage = false;
    });
    pi.on("tool_call", (event) => {
      if (event.toolName !== "search_studies") return {};
      if (searchCalledThisMessage) {
        return {
          block: true,
          reason: "search_studies already ran for this message — use the results you already have, or search_by_sample for a different angle.",
        };
      }
      searchCalledThisMessage = true;
      return {};
    });
  };
}

export function createSessionStore({ cwd, agentDir, sessionDir, modelRuntime, flaskUrl, piSecret }) {
  const cache = new Map(); // sessionKey -> { session, contextRef, lastUsed }

  fs.mkdirSync(sessionDir, { recursive: true });
  fs.mkdirSync(cwd, { recursive: true });

  async function findExistingPath(sessionKey) {
    const infos = await SessionManager.list(cwd, sessionDir);
    const hit = infos.find((i) => i.id === sessionKey);
    return hit ? hit.path : null;
  }

  async function getOrCreate(sessionKey, { model, systemPrompt }) {
    assertValidSessionId(sessionKey);
    const cached = cache.get(sessionKey);
    if (cached) {
      cached.lastUsed = Date.now();
      return cached;
    }

    const existingPath = await findExistingPath(sessionKey);
    const sessionManager = existingPath
      ? SessionManager.open(existingPath, sessionDir, cwd)
      : SessionManager.create(cwd, sessionDir, { id: sessionKey });

    const contextRef = { block: null };
    // Read fresh (not captured) at call time inside tools.mjs's execute(), so a
    // per-turn token rotation takes effect without rebuilding the tool list.
    const toolTokenRef = { token: null };

    const resourceLoader = new DefaultResourceLoader({
      cwd,
      agentDir,
      noContextFiles: true,
      noSkills: true,
      noPromptTemplates: true,
      noThemes: true,
      systemPrompt,
      extensionFactories: [makeContextInjectionExtension(contextRef), makeSearchOncePerMessageExtension()],
    });
    await resourceLoader.reload();

    // Isolated to agentDir (never touches the host's ~/.pi/agent). projectTrusted:
    // true means no trust prompting is even possible — belt-and-suspenders on top
    // of cwd already being a dedicated empty dir with nothing that would require it.
    const settingsManager = SettingsManager.create(cwd, agentDir, { projectTrusted: true });

    const tools = await loadTools({
      flaskUrl,
      piSecret,
      getToolToken: () => toolTokenRef.token,
    });

    const { session } = await createAgentSession({
      cwd,
      model,
      modelRuntime,
      resourceLoader,
      sessionManager,
      settingsManager,
      customTools: tools,
      // Security-critical: without this the agent has bash/read/edit/write as the
      // Flask user. pi ships no permission sandbox (README.md: "Pi does not include
      // a built-in permission system for restricting filesystem, process, network,
      // or credential access").
      noTools: "builtin",
    });

    const entry = { session, contextRef, toolTokenRef, lastUsed: Date.now() };
    cache.set(sessionKey, entry);
    return entry;
  }

  /**
   * Run one turn to completion. Subscribes before prompting (no race), streams
   * every event to onEvent, and resolves once the session reports agent_settled —
   * NOT on session.prompt()'s own resolution, which only signals preflight
   * acceptance (queued/dispatched), not turn completion.
   */
  async function runTurn(entry, { message, contextBlock, toolToken }, onEvent) {
    entry.lastUsed = Date.now();
    entry.contextRef.block = contextBlock || null;
    entry.toolTokenRef.token = toolToken || null;

    await new Promise((resolve, reject) => {
      let settled = false;
      const unsubscribe = entry.session.subscribe((event) => {
        onEvent(event);
        if (event.type === "agent_settled") {
          settled = true;
          unsubscribe();
          resolve();
        }
      });
      entry.session.prompt(message).catch((err) => {
        if (!settled) {
          unsubscribe();
          reject(err);
        }
      });
    });
  }

  function evictIdle() {
    const now = Date.now();
    for (const [key, entry] of cache) {
      if (now - entry.lastUsed > IDLE_EVICT_MS && !entry.session.isStreaming) {
        entry.session.dispose();
        cache.delete(key);
      }
    }
  }
  const sweepTimer = setInterval(evictIdle, SWEEP_INTERVAL_MS);
  sweepTimer.unref();

  async function deleteSession(sessionKey) {
    const cached = cache.get(sessionKey);
    if (cached) {
      cached.session.dispose();
      cache.delete(sessionKey);
    }
    const existingPath = await findExistingPath(sessionKey);
    if (existingPath && fs.existsSync(existingPath)) {
      fs.unlinkSync(existingPath);
    }
  }

  async function abortSession(sessionKey) {
    const cached = cache.get(sessionKey);
    if (cached) await cached.session.abort();
  }

  return { getOrCreate, runTurn, deleteSession, abortSession, evictIdle, cacheSize: () => cache.size };
}
