import fs from "node:fs";
import path from "node:path";
import {
  SessionManager,
  SettingsManager,
  DefaultResourceLoader,
  createAgentSession,
} from "@earendil-works/pi-coding-agent";
import { loadTools } from "./tools.mjs";

const IDLE_EVICT_MS = 30 * 60 * 1000;
const SWEEP_INTERVAL_MS = 5 * 60 * 1000;
// Each cache entry holds a live AgentSession — its full message history, tool
// list, and extension closures. Without a cap, the ONLY eviction is the idle
// sweep at IDLE_EVICT_MS, so a burst of activity (a few hundred chats in a
// half hour) keeps every one of those sessions resident in memory the whole
// time, on a box that also runs Flask and Postgres. This bounds resident
// session count independent of how recently anything went idle. Read once
// here as the default and threaded through createSessionStore as a parameter
// (see below), rather than being a plain module-level constant, so tests can
// override it per-harness without needing to set the env var before this
// module is ever imported.
const DEFAULT_MAX_CACHED_SESSIONS = Number(process.env.PI_MAX_CACHED_SESSIONS || 200);

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
 *
 * The budget is charged OPTIMISTICALLY here and refunded by the result (see
 * makeSearchBudgetRefunder). It has to be charged in this hook, synchronously:
 * a single assistant message can carry two parallel search_studies calls, and
 * both of their `tool_call` hooks run before either result exists — so a gate
 * that waited for a result would let both through.
 *
 * The refund exists because this hook cannot know whether the call will do any
 * work. A `search_studies {}` reaches Flask, comes back as _empty_input_result
 * ("No keywords provided — cannot search"), and used to permanently burn the
 * budget — leaving the model blocked on its own retry with "search_studies
 * already ran for this message" as the only thing it could see. Observed live:
 * that is what turned a recoverable mistake into a wrong answer.
 */
function makeSearchOncePerMessageExtension(searchStateRef) {
  return function searchOncePerMessageExtension(pi) {
    pi.on("agent_start", () => {
      searchStateRef.spent = false;
    });
    pi.on("tool_call", (event) => {
      if (event.toolName !== "search_studies") return {};
      if (searchStateRef.spent) {
        return {
          block: true,
          reason: "search_studies already ran for this message — use the results you already have, or search_by_sample for a different angle.",
        };
      }
      searchStateRef.spent = true;
      return {};
    });
  };
}

/**
 * Refunds the one-search budget when the call turned out to do no work.
 * `executed` is set by Flask (agent_tools.ToolResult) and is false only when a
 * tool rejected its input outright. Lives here beside the gate rather than in
 * tools.mjs, which is generated wholesale from served schemas and must not
 * know any tool by name.
 */
function makeSearchBudgetRefunder(searchStateRef) {
  return function refundIfNothingRan(toolName, result) {
    if (toolName === "search_studies" && result?.executed === false) {
      searchStateRef.spent = false;
    }
  };
}

/**
 * Directory component for one user's sessions.
 *
 * Sanitised the same defensive way server.mjs's sessionKeyFor treats chat_id.
 * Note this MUST be a separate path segment rather than part of the session key:
 * VALID_SESSION_ID forbids "/", which is what stops a crafted id escaping the
 * session directory, so a key like "990001/global-abc" would simply be rejected.
 *
 * Empty/missing user_id lands in "_shared" rather than throwing. That is the
 * pre-per-user layout's behaviour for anything that reaches the sidecar without
 * one, and failing a turn over a directory name would be worse than filing it
 * somewhere predictable.
 */
export function userDirFor(userId) {
  const safe = String(userId ?? "").replace(/[^A-Za-z0-9._-]/g, "-").replace(/^\.+/, "");
  return safe || "_shared";
}

export function createSessionStore({
  cwd, agentDir, sessionDir, modelRuntime, flaskUrl, piSecret,
  maxCachedSessions = DEFAULT_MAX_CACHED_SESSIONS,
}) {
  // Keyed `${userDir}/${sessionKey}`, not sessionKey alone: two users whose
  // chats shared an id would otherwise share a cached AgentSession. chat_ids are
  // SQLite-generated so that is not a practical collision today, but the cache
  // must not be the only thing standing between two users' transcripts. Each
  // entry also records ownerDir, and a hit whose owner differs throws — the key
  // alone would make a mismatch a silent cache miss that quietly builds a second
  // session, where the explicit check surfaces it.
  const cache = new Map(); // cacheKey -> { session, contextRef, ownerDir, lastUsed, turnQueue }
  // sessionKey -> in-progress Promise<entry>. Closes a race in getOrCreate: two
  // requests for the same chat arriving close together (a double-send, a
  // retry, or two of gunicorn's threads) both miss `cache` before either
  // finishes — building a session takes several awaited steps
  // (resourceLoader.reload, loadTools, createAgentSession) — and without this,
  // both would construct a separate AgentSession over the SAME JSONL file:
  // two writers on one append-only transcript, plus the loser leaked (never
  // disposed, its file handle held until process exit — idle eviction only
  // ever sees the winner that made it into `cache`).
  const inflight = new Map();

  fs.mkdirSync(sessionDir, { recursive: true });
  fs.mkdirSync(cwd, { recursive: true });

  // sessionDir is now a BASE; each user gets a subdirectory under it. Ownership
  // becomes a filesystem property rather than resting solely on Flask's
  // get_global_chat(user_id, chat_id) check, and the readdir fallback below
  // scans only one user's chats instead of every chat ever created.
  function dirForUser(userDir) {
    const dir = path.join(sessionDir, userDir);
    fs.mkdirSync(dir, { recursive: true });
    return dir;
  }

  // SessionManager.create() names files "<timestamp>_<id>.jsonl", so the id is
  // recoverable from the filename alone. Matching on that instead of
  // SessionManager.list() matters because list() opens and parses every session
  // in the directory to report its metadata — an O(all chats ever created) scan
  // on each cache miss, just to resolve one path. Timestamps sort lexically, so
  // the last match is the newest if a key somehow has more than one file.
  //
  // fs.promises.readdir, not the sync form: this still lists the whole
  // sessionDir (still O(all chats ever created) in directory-entry count, not
  // file-parse cost), but doing it synchronously blocks Node's single-threaded
  // event loop for every OTHER concurrent chat's request handling while it
  // runs — on a directory that only grows, since nothing here prunes old
  // session files. The async form yields the same result without stalling
  // in-flight streams on other sessions while it does.
  // FALLBACK ONLY, for chats whose pi_session_file column is still NULL. Flask
  // now stores the path it gets back from a new session and sends it on later
  // turns, so the common case never reaches this scan at all.
  async function findExistingPath(sessionKey, userDir) {
    const dir = dirForUser(userDir);
    const suffix = `_${sessionKey}.jsonl`;
    let newest = null;
    for (const name of await fs.promises.readdir(dir)) {
      if (name.endsWith(suffix) && (newest === null || name > newest)) newest = name;
    }
    return newest ? path.join(dir, newest) : null;
  }

  async function buildSession(sessionKey, { model, systemPrompt, userDir, sessionFile }) {
    const dir = dirForUser(userDir);
    // Prefer the path Flask stored; fall back to scanning this user's directory.
    // Only trust a stored path that still exists — a hand-moved or reaped file
    // would otherwise make SessionManager.open throw on every turn forever,
    // where the scan (or a fresh session) recovers.
    let existingPath = sessionFile && fs.existsSync(sessionFile) ? sessionFile : null;
    if (!existingPath) existingPath = await findExistingPath(sessionKey, userDir);

    const sessionManager = existingPath
      ? SessionManager.open(existingPath, dir, cwd)
      : SessionManager.create(cwd, dir, { id: sessionKey });

    const contextRef = { block: null };
    // Read fresh (not captured) at call time inside tools.mjs's execute(), so a
    // per-turn token rotation takes effect without rebuilding the tool list.
    const toolTokenRef = { token: null };
    // Shared between the block hook (reads) and the tool wrapper (writes), the
    // same way contextRef and toolTokenRef span extension and tool code.
    const searchStateRef = { spent: false };

    const resourceLoader = new DefaultResourceLoader({
      cwd,
      agentDir,
      noContextFiles: true,
      noSkills: true,
      noPromptTemplates: true,
      noThemes: true,
      systemPrompt,
      extensionFactories: [
        makeContextInjectionExtension(contextRef),
        makeSearchOncePerMessageExtension(searchStateRef),
      ],
    });
    await resourceLoader.reload();

    // In memory, not a file under agentDir. These values are identical for every
    // user and every chat, so there is no reason for them to live on disk where
    // something could edit them out from under a running process — and it leaves
    // agentDir holding only auth.json. projectTrusted: true means no trust
    // prompting is even possible, belt-and-suspenders on top of cwd already being
    // a dedicated empty directory with nothing in it that would require trust.
    const settingsManager = SettingsManager.inMemory({}, { projectTrusted: true });

    const tools = await loadTools({
      flaskUrl,
      piSecret,
      getToolToken: () => toolTokenRef.token,
      onToolResult: makeSearchBudgetRefunder(searchStateRef),
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

    return {
      session, contextRef, toolTokenRef,
      ownerDir: userDir,
      // Reported back to Flask so it can store it and skip the scan next time.
      // Only surfaced when we CREATED the session — reopening an existing one
      // means Flask either already knows the path or is about to learn it from
      // the fallback scan, which is the NULL-column case.
      createdSessionFile: existingPath ? null : (sessionManager.getSessionFile?.() ?? null),
      lastUsed: Date.now(), turnQueue: null,
    };
  }

  function getOrCreate(sessionKey, { model, systemPrompt, userId, sessionFile }) {
    assertValidSessionId(sessionKey);
    const userDir = userDirFor(userId);
    const cacheKey = `${userDir}/${sessionKey}`;

    const cached = cache.get(cacheKey);
    if (cached) {
      // Cannot happen while the key includes userDir, which is exactly why it is
      // worth asserting: if the key scheme is ever loosened, or Flask sends a
      // user_id that does not match the chat it is asking about, this throws
      // instead of silently handing one user another's live AgentSession.
      if (cached.ownerDir !== userDir) {
        throw new Error(`session ${sessionKey} is owned by another user`);
      }
      cached.lastUsed = Date.now();
      return Promise.resolve(cached);
    }

    const existingBuild = inflight.get(cacheKey);
    if (existingBuild) return existingBuild;

    // inflight.set happens synchronously, before buildSession's first await —
    // any concurrent getOrCreate for this key, no matter which await point it
    // arrives at, converges on this SAME promise instead of starting a second
    // build.
    const buildPromise = buildSession(sessionKey, { model, systemPrompt, userDir, sessionFile })
      .then((entry) => {
        cache.set(cacheKey, entry);
        evictLruIfOverCap();
        return entry;
      })
      .finally(() => {
        inflight.delete(cacheKey);
      });
    inflight.set(cacheKey, buildPromise);
    return buildPromise;
  }

  /**
   * One turn, run exclusively (never called directly — see runTurn below).
   * Subscribes before prompting (no race), streams every event to onEvent,
   * and resolves once the session reports agent_settled — NOT on
   * session.prompt()'s own resolution, which only signals preflight
   * acceptance (queued/dispatched), not turn completion.
   */
  async function runTurnExclusive(entry, { message, contextBlock, toolToken }, onEvent) {
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

  /**
   * Run one turn to completion, SERIALIZED per session entry via
   * entry.turnQueue. Two turns on the same chat_id — a double-send, or two
   * requests racing — must not run concurrently: entry.contextRef and
   * entry.toolTokenRef are shared mutable state that tools.mjs's execute()
   * reads at call time, so an overlapping second turn silently overwrites the
   * first turn's per-turn context and scope token while its tool calls are
   * still in flight (if the two turns are different scopes, a tool call can
   * then execute under the WRONG scope). entry.session.subscribe() also has
   * no per-turn partitioning, so two concurrent runTurnExclusive calls would
   * each get every event from both turns.
   */
  function runTurn(entry, args, onEvent) {
    const run = () => runTurnExclusive(entry, args, onEvent);
    const queue = entry.turnQueue || Promise.resolve();
    const next = queue.then(run, run); // run this turn regardless of the previous one's outcome
    // Keep the chain alive past a rejection so the NEXT queued turn still
    // runs; this call's OWN caller still observes the real rejection via `next`.
    entry.turnQueue = next.catch(() => {});
    return next;
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

  // Runs once per successful build (see getOrCreate), not on a timer — a
  // sustained burst that keeps pushing the cache over MAX_CACHED_SESSIONS
  // between idle sweeps must not be able to grow it unbounded in between.
  // Same non-streaming guard as evictIdle: never dispose a session with a
  // turn in flight, regardless of how stale its lastUsed looks.
  function evictLruIfOverCap() {
    while (cache.size > maxCachedSessions) {
      let lruKey = null, lruEntry = null;
      for (const [key, entry] of cache) {
        if (entry.session.isStreaming) continue;
        if (lruEntry === null || entry.lastUsed < lruEntry.lastUsed) {
          lruKey = key;
          lruEntry = entry;
        }
      }
      if (lruKey === null) break; // every cached session is mid-turn — nothing safe to evict
      lruEntry.session.dispose();
      cache.delete(lruKey);
    }
  }
  const sweepTimer = setInterval(evictIdle, SWEEP_INTERVAL_MS);
  sweepTimer.unref();

  async function deleteSession(sessionKey, { userId, sessionFile } = {}) {
    const userDir = userDirFor(userId);
    const cacheKey = `${userDir}/${sessionKey}`;
    const cached = cache.get(cacheKey);
    if (cached) {
      cached.session.dispose();
      cache.delete(cacheKey);
    }
    // Scoped to this user's directory, so a delete can only ever remove a file
    // under the caller's own path — even if the sessionKey were somehow another
    // user's chat id.
    let target = sessionFile && fs.existsSync(sessionFile) ? sessionFile : null;
    if (target && path.dirname(path.resolve(target)) !== path.resolve(dirForUser(userDir))) {
      // A stored path pointing outside the caller's directory is not something
      // to act on: refuse it and fall back to the scoped scan.
      target = null;
    }
    if (!target) target = await findExistingPath(sessionKey, userDir);
    if (target && fs.existsSync(target)) fs.unlinkSync(target);
  }

  async function abortSession(sessionKey, { userId } = {}) {
    const cached = cache.get(`${userDirFor(userId)}/${sessionKey}`);
    if (cached) await cached.session.abort();
  }

  return { getOrCreate, runTurn, deleteSession, abortSession, evictIdle, cacheSize: () => cache.size };
}
