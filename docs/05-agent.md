# 05 — The Agent

*How the model is constrained into producing grounded answers, and why the most important constraint is a mutation to the tool schema rather than a sentence in the prompt.*

Prerequisites: [`04-search.md`](04-search.md) — the tools call into the search layer.

---

## Which runtime is running

> **This chapter describes the legacy in-process Python loop (`backend/helpers/agent.py :: stream_agent`), which is no longer the default.** Chat now runs on **pi** — an external Node agent runtime in a sidecar process — with Flask translating pi's events back into the same SSE contract described in [`06-streaming-and-chat.md`](06-streaming-and-chat.md). The tool set, the search layer, and every scope rule are unchanged and still live in Python; what moved is the loop, the conversation history, and context compaction.
>
> `PI_BACKEND_GLOBAL` and `PI_BACKEND_PROJECT` (`backend/config.py`) both default to **true**. Setting either to `false` reverts that chat type to the loop described below — a rollback that needs no deploy. Nothing exercises the legacy path in normal use any more, so treat it as drifting rather than known-good.
>
> The mechanics below still describe what the tools do and why they are shaped that way, which is runtime-independent. Read them for the tool design; read `backend/helpers/pi_translate.py` for what the frontend actually receives today.

Historically, chat forked on one predicate:

```python
if model_supports_tools(model):   # agentic path — this chapter
else:                             # legacy path — regex planner, one search, plain stream
```

`backend/config.py :: model_supports_tools` reads `supports_tools` from `MODEL_METADATA`. **Every configured model now returns `True`**, so the `else` branch became unreachable and was deleted. `gemma-small` was the sole exception, on the belief that it could not emit streaming tool calls; checked against the live NRP endpoint, it can. The predicate is retained because `/api/internal/models` uses it to filter the roster it serves the sidecar, and a future model that genuinely cannot call tools must still be excluded.

The second fork **no longer exists**:

> Project chats used to be non-agentic — `backend/routes/chat_routes.py` never imported `stream_agent`, so tool cards appeared in global chat only. On the pi path both chat types run the same loop. The difference is now scope, and it is enforced rather than requested: every project-chat tool call is hard-bounded to the workspace's own studies server-side (`backend/helpers/project_scope.py`), where it used to be a sentence in the prompt.

---

## pi backend (2026, behind feature flags)

**Update:** the asymmetry above is closed, behind a flag. A second, parallel agent runtime — [pi](https://github.com/earendil-works/pi) (`@earendil-works/pi-coding-agent`), run as a standalone Node process in `qiita_explore/pi_sidecar/` — now backs **both** chat types, gated independently:

- `config.PI_BACKEND_GLOBAL` — when true, `global_chat_routes.py`'s agentic branch calls the pi sidecar instead of `stream_agent`.
- `config.PI_BACKEND_PROJECT` — when true, project chat becomes agentic for the first time: `chat_routes.py` gets the same tool loop as global chat, with every tool call **hard-scoped server-side** to the project's studies (`helpers/project_scope.py`) rather than the soft, prompt-only boundary `_build_project_study_context` relies on.

Both default to `False` — the paths this chapter describes below (`stream_agent`, the OpenAI/Anthropic loops, the legacy project-chat token accumulator) are unchanged and remain the default runtime. Flip a flag to cut over; flip it back to revert without a deploy.

**Why a separate process, not a rewrite of `stream_agent`.** pi is a TypeScript agent runtime with no Python bindings and no HTTP server of its own — `qiita_explore/pi_sidecar/server.mjs` is a small `node:http` service that wraps it. The sidecar:
- Owns conversation history and context management: one pi session (JSONL, tree-structured) per chat, with pi's own auto-compaction — not the 10-message truncation `_normalize_messages` does today, and not the 3-tier char-budget cascade `_build_project_study_context` does for project chat.
- Holds **no** qiita_db/Postgres access and **no** built-in tools (`bash`/`read`/`edit`/`write` are explicitly disabled — `noTools: "builtin"` in `sessions.mjs`). Its four tools (`search_studies`, `get_study_report`, `pin_study`, `search_by_sample`) are thin `fetch` wrappers that call back into Flask.
- Fetches its tool schemas from `GET /api/internal/tools/schemas` at session-creation time rather than hardcoding them, so `agent_tools.TOOL_SCHEMAS` stays the single source of truth — a schema change on the Python side needs no sidecar edit.

**The hard scope boundary.** The sidecar runs on the intermediate node while Flask runs on barnacle, so `/api/internal/tools/*` is a genuine cross-machine surface and is guarded as one. Every request — schema reads and tool calls alike — passes `_guard()`: a source-IP allowlist (`PI_ALLOWED_TOOL_CALLERS`) plus the `X-Pi-Secret` shared secret, failing closed if the secret is unset. Tool calls then need a third, independent credential, so no single leaked value is enough: a stolen secret is unusable from an unlisted host, and a captured scope token is unusable without the secret.

`POST /api/internal/tools/<name>` (`routes/internal_tool_routes.py`) authenticates each call with a short-lived HMAC-signed "scope token" (`helpers/scope_token.py`, ~10 min TTL), minted per-turn by the chat route — the sidecar never sees a database credential or a raw `user_id`/`project_id` it could forge. When the token's scope is `"project"`, `search_studies`/`search_by_sample` are re-ranked over the project's own SQLite-mirrored studies (never a Postgres query against the full database), and `get_study_report`/`pin_study` refuse any `study_id` that isn't a project member — checked *before* `execute_tool()` runs. Project ownership is independently re-verified at this route (`get_project(project_id, user_id)`), since `get_project_studies_only()` itself performs no such check.

**Translation, not a rewrite of the SSE contract.** `helpers/pi_translate.py` maps pi's event stream onto the exact same 10 SSE events and segment shapes `stream_agent` + `global_chat_routes.py` produce today (`translate()` for the live stream, `build_segments()` for the persisted `ui_payload`) — the frontend (`app_state.js`, `components.js`) needed zero changes. The tool-call↔result correlation name (`tool_{name}_{call_id[:6]}`) is byte-identical to `_execute_tool_call`'s in this file.

See [`06-streaming-and-chat.md`](06-streaming-and-chat.md) for the full event-translation table and [`appendix-d-configuration.md`](appendix-d-configuration.md#pi-sidecar) for the sidecar's environment variables.

---

## What `stream_agent` is

`backend/helpers/agent.py :: stream_agent` is a **generator that yields typed dictionaries, not SSE strings**:

```python
stream_agent(messages, *, system_prompt, model, study_context_text,
             scope, chat_id, max_iters=4, deep_search=False)
    -> Generator[dict, None, None]
```

Keeping SSE formatting out of the agent is what allows `backend/agent_harness.py` — an offline CLI driver — to consume the same generator and print to a terminal. The route is the only place that knows about the wire format.

Five yield types: `agent_start`, `token`, `reasoning`, `segment_tool_call`, `segment_tool_result`.

> **`reasoning` never reaches the browser.** Reasoning-capable models emit `delta.reasoning_content`, and `stream_agent` faithfully yields it as `{"type": "reasoning", ...}`. **No route translates it into an SSE event, and no browser handler exists for it.** The web path silently discards every reasoning token; only `agent_harness.py` consumes them. This is a gap, not a design choice — the plumbing exists on one end and stops halfway. Surfacing it would give users visibility into the model's deliberation at no backend cost.

---

## The loop

```mermaid
stateDiagram-v2
    [*] --> Iterate: agent_start

    state Iterate {
        [*] --> Schema
        Schema: build active_tools
        note right of Schema
            search_already_done?
              yes → 4 tools (search_studies removed)
              no  → all 5 tools
        end note
        Schema --> Stream: chat.completions.create(stream=True)
        Stream --> Accumulate: content · reasoning · tool_call fragments
    }

    Iterate --> Done: finish_reason ≠ "tool_calls"<br/>(model produced prose)
    Iterate --> Execute: finish_reason == "tool_calls"

    Execute: run each call, yield<br/>segment_tool_call + segment_tool_result
    Execute --> Iterate: append tool results,<br/>next iteration (max 4)
    Execute --> Exhausted: iteration == max_iters − 1

    Exhausted: log warning
    Exhausted --> ForcedSynthesis
    Done --> ForcedSynthesis: if no prose was emitted
    Done --> [*]: prose emitted
    ForcedSynthesis: re-call model with NO tools,<br/>stream result as tokens
    ForcedSynthesis --> [*]
```

Up to four iterations. Each one streams a completion, accumulating three things in parallel: prose content, reasoning content, and tool-call fragments — the last reassembled from streamed deltas into `tool_call_map[index] = {id, name, arguments}` by string-concatenating name and argument fragments as they arrive.

The loop exits when `finish_reason != "tool_calls"` or no tool calls were requested. Otherwise it appends the assistant message with its `tool_calls`, executes each call in index order, appends a `{"role": "tool", ...}` message per result, and iterates.

### The one-search invariant

This is the best idea in the codebase, and it is worth understanding as a general technique.

The system prompt tells the model to call `search_studies` exactly once. Prompts are advisory — a model that gets disappointing results will happily search again with different terms, burning iterations and latency while producing a worse answer than synthesising what it already has.

So the constraint is not left to the prompt:

```python
if search_already_done:
    active_tools = [t for t in TOOL_SCHEMAS if t["function"]["name"] != "search_studies"]
else:
    active_tools = TOOL_SCHEMAS
```

**Once a search completes, the tool ceases to exist.** The model is not refused, not scolded, not corrected — the capability is simply absent from the schema it is given on the next turn. There is no failure mode to recover from because there is no failure. The same gating is implemented in the Anthropic path.

The general principle: *when a constraint matters, encode it in the interface rather than the instructions.* A prompt rule is a request; a schema mutation is a fact. Everything the model cannot do, it cannot attempt.

The bound this buys is concrete: at most one expensive search per turn, so a turn's latency is bounded by one search plus at most four completions.

### Forced synthesis

A tool-calling loop has an ugly failure mode: the model calls a tool, receives the result, and stops — producing a turn whose visible output is a tool card and nothing else. The user sees results with no answer.

```python
if not final_had_synthesis and api_msgs and api_msgs[-1].get("role") == "tool":
    # re-call the model with NO tools and stream the result
```

If the loop ended on a tool message with no prose, the model is called once more **with no tools at all**, and that response streams as tokens. With no tools available, the only thing it can do is answer. The user never sees an empty turn.

Note the interaction with `max_iters`: hitting the ceiling while still requesting tools logs a warning *and* falls into forced synthesis, so exhaustion also degrades into an answer rather than silence.

---

## Two providers, one loop

`get_client(model)` returns `(client, provider)`, and `stream_agent` dispatches to `_stream_anthropic_agent` when the provider is Anthropic. The two implementations are parallel, not shared, because the streaming protocols differ structurally:

| Concern | OpenAI / NRP-Nautilus | Anthropic |
|---|---|---|
| Tool schema | `{"type":"function","function":{name,description,parameters}}` | `{name, description, input_schema}` |
| System prompt | A message with `role: "system"` | A separate `system=` parameter |
| Tool arguments | `delta.tool_calls[].function.arguments` fragments | `input_json_delta` accumulating `partial_json` |
| Continue signal | `finish_reason == "tool_calls"` | `stop_reason == "tool_use"` |
| Tool results | `{"role": "tool", "tool_call_id": ...}` | A **user** message containing `[{"type":"tool_result", ...}]` |
| Reasoning | `delta.reasoning_content` | not handled |

`_openai_tools_to_anthropic` performs the schema translation, so tools are declared once in OpenAI format and rewritten on demand.

A third provider would need: schema translation, system-prompt placement, a streaming accumulator for tool arguments, the stop-signal mapping, the tool-result message shape — and both the search-gating and forced-synthesis behaviours, which are currently duplicated rather than factored out. That duplication is the maintenance cost of this design, and it is real: a fix applied to one path can silently miss the other. TKT-032 tracks consolidating it.

---

## The four tools

Full schemas in [`appendix-c-agent-tools-and-sse.md`](appendix-c-agent-tools-and-sse.md). What follows is why each is shaped the way it is.

### `search_studies` — typed slots, not a query string

The obvious design is one `query` string. This tool instead offers **six typed arrays**:

`organism` · `qualifier` · `body_site` · `condition_or_intervention` · `project_or_pi` · `keywords`

Two concrete problems drove that.

**Problem one: which terms get dropped.** Terms are pooled by `_collect_terms` in a fixed priority order — the order listed above — deduplicated preserving first occurrence. Downstream, `expand_keyword_variants` caps the result at 80 terms. Because `keywords` is pooled **last**, it is what gets truncated first:

```mermaid
flowchart LR
    O["organism"] --> P["_collect_terms<br/>pool in priority order"]
    Q["qualifier"] --> P
    B["body_site"] --> P
    C["condition_or_intervention"] --> P
    PI["project_or_pi"] --> P
    K["keywords<br/><i>catch-all</i>"] --> P
    P --> E["expand_keyword_variants<br/>· plural/irregular variants<br/>· <b>cap 80</b> ✂"]
    E --> S["SQL"]

    style K stroke-dasharray: 4 4
```

A search for a mouse study cannot lose the word "mouse" to a flood of incidental terms. With one flat list, truncation order would be arbitrary.

**Problem two: accidental filtering.** `_collect_terms` returns two lists. `raw_kws` is everything, used for matching. `detect_kws` is **the `keywords` slot only**, and it alone feeds `detect_data_types`, which maps synonyms like "shotgun" onto canonical data types and applies them as an **AND filter**.

The scoping prevents a specific bug. A user asking about *"metagenomics research in captive primates"* has "metagenomics" land in `condition_or_intervention`. If every slot fed type detection, that word would silently add `data_type = 'Metagenomic'` as a hard filter — quietly excluding every 16S study the user wanted. Restricting detection to the explicit catch-all slot means an assay filter is applied only when the user's phrasing put the term there.

`data_types` may also be passed explicitly. `investigation_types` exists but is discouraged in the schema description, because it is sparsely populated (see [`04-search.md`](04-search.md)).

### `get_study_report`

Loads full sample-level metadata for one study and **pins it as a side effect** — the model asking for a report is taken as intent to keep that study in context.

The pin routes through `_pin_studies_validated`, so it respects the cap of 10. But the call is wrapped in a bare `except Exception: pass`: **if the cap rejects the pin, nothing is reported.** The report still renders, the study is silently not pinned, and neither the model nor the user learns why. Worth surfacing.

### `pin_study`

Explicit pinning, capped at 10 per chat. Reports back which IDs were pinned, which were invalid, and which were rejected — unlike the auto-pin path above.

### `search_by_sample`

Structured `{field, value}` filters against sample metadata, for when the user names a field explicitly. Its schema description contrasts it with `search_studies` to steer selection, since the two overlap: `search_studies` always runs a sample probe alongside its text search, so `search_by_sample` is for the case where the *field* is known, not merely the value.

> `compute_diversity` was a fifth tool: a hard stub, live in the schema, that the model could call and be apologised to. It has been removed from both `TOOL_SCHEMAS` and the `execute_tool` dispatch. Diversity computation remains unimplemented pending BIOM/OTU ingestion — tracked in [`11-roadmap.md`](11-roadmap.md).

---

## What the LLM does not do

Stated plainly, because the opposite is a reasonable assumption:

**The model never writes SQL.** It emits JSON conforming to a fixed schema. Python reads that JSON and composes parameterized SQL, with every value bound rather than interpolated.

Two properties follow, and the second matters more:

- **Injection is structurally impossible**, not defended against. There is no path from model output to SQL text. The only interpolated values anywhere are table names and `LIMIT`/`OFFSET`, all `int()`-cast first (see [`04-search.md`](04-search.md)).
- **Every query is bounded by construction.** The tool schema caps `limit` at 20; candidate sets are capped at 40 or 500; statement timeouts are attached at the connection. The model cannot express an unbounded query because the vocabulary contains no way to say it.

The cost is expressiveness. Questions the tool set cannot phrase cannot be asked: *"what is the average sample count per data type"*, *"which PIs publish across the most body sites"*, anything requiring aggregation, grouping, or a join the builders do not implement. Users hit this wall, and the answer today is "that query isn't available."

Letting the model author constrained SQL is genuine future work, with a real threat model attached — see [`11-roadmap.md`](11-roadmap.md).

---

## Observability

Each iteration logs time-to-first-token, total elapsed time, content and reasoning lengths, finish reason, and tool-call count. Each tool execution logs its name, elapsed time, and result size, and appends a `· {n}s` suffix to the label the user sees — so tool timing is visible in the UI without opening logs.

**Tool failures do not kill the stream.** `_execute_tool_call` catches exceptions, yields a `"{name} failed"` result segment, and returns the error text *as the tool's content* — so the model receives the failure as an observation and can recover, apologise, or try a different tool. A crashed tool degrades one step rather than the turn.

`backend/agent_harness.py` runs the whole loop from the command line, which is the fastest way to iterate on prompts or tool schemas without a browser — and the only way to observe `reasoning` output.

> **`AGENT_DEBUG` does not affect the server.** Its only reader is `agent_harness.py`. Setting it in the backend's `.env` has no effect on the Gunicorn process — a natural assumption that is wrong, and an easy few minutes lost. Server-side agent logging is whatever `logging.basicConfig(level=INFO)` in `run.py` produces.

---

## When this loop is finally deleted

The legacy path stays reachable behind `PI_BACKEND_GLOBAL` / `PI_BACKEND_PROJECT`, but the intent is to remove it once pi has proven parity. Two things should be written down before then, because both are easy to get wrong at that moment.

**`backend/helpers/pi_translate.py` should be re-pointed, not deleted.** It exists to preserve the legacy SSE vocabulary (`segment_tool_call`, `step_start`, `token`, …) so that swapping the agent runtime needed no frontend change — deliberately, since the alternative was editing four JS files in the same commit that replaced the loop, with no way to bisect a regression. When `agent.py` goes, the right move is to retarget the translator at pi's native event names and update the frontend in one reviewable change. Deleting the translator and expecting pi's raw events to reach the browser would break every rendering path at once.

**The one-search-per-message invariant is enforced in two unrelated places.** The legacy loop mutates the tool schema mid-run (`agent.py`, `active_tools`); pi cannot do that — `setActiveTools` only takes effect on the next agent turn — so the sidecar uses a `tool_call` block hook plus a result-driven refund (`pi_sidecar/sessions.mjs`). They are the same rule with no shared code. Whichever survives, check the other's tests came with it: the refund in particular exists because a `search_studies` call rejected for empty input used to spend the budget permanently, leaving the model blocked on its own retry.

---

*See also: [`06-streaming-and-chat.md`](06-streaming-and-chat.md) for how these yields become browser state · [`appendix-c-agent-tools-and-sse.md`](appendix-c-agent-tools-and-sse.md) for the full schemas · [`04-search.md`](04-search.md) for what the tools query · [`appendix-d-configuration.md`](appendix-d-configuration.md) for the model roster.*
