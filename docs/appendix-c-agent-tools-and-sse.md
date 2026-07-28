# Appendix C — Agent Tools and the SSE Protocol

*The exact tool schemas the model sees, and the exact wire protocol the browser receives — the two halves of a contract that only holds together if `agent_tools.py`, `pi_translate.py`, the two chat routes, and the frontend segments model change in lockstep.*

---

## Scope: both chat types, one tool surface

Agentic tool-calling runs on both streaming endpoints — `POST /api/global-chats/<chat_id>/message/stream` (`backend/routes/global_chat_routes.py`) and `POST /api/projects/<project_id>/chats/<chat_id>/message/stream` (`backend/routes/chat_routes.py`) — through the identical sub-generator, `helpers/pi_turn.py :: stream_pi_turn`. There used to be an asymmetry here: project chat had no agentic branch at all and always ran a context-stuffed, non-agentic prompt path. That asymmetry is gone. The only real difference left between the two chat types is *scope* — a project-chat tool call is hard-bounded server-side to that project's own studies (`helpers/project_scope.py`), where a global-chat call searches the whole database — and that boundary is enforced by which `scope` claim a request's signed tool token carries, not by which route ran. See [`05-agent.md`](05-agent.md#the-hard-scope-boundary).

---

## Tool schema conventions

`backend/helpers/agent_tools.py :: TOOL_SCHEMAS` is a list of **four** entries in OpenAI function-calling format:

```json
{ "type": "function",
  "function": { "name": "...", "description": "...",
                "parameters": { "type": "object", "properties": {...}, "required": [...] } } }
```

This is served verbatim to the pi sidecar from `GET /api/internal/tools/schemas` (`routes/internal_tool_routes.py :: api_internal_tool_schemas`) at session-creation time. `pi_sidecar/tools.mjs :: jsonSchemaToTypeBox` converts each entry's plain JSON-Schema `parameters` object into pi's native TypeBox schema shape — recursively, for `object`/`array`/`string`/`integer`/`number`/`boolean`, degrading anything else to `Type.Unknown()`. No field is dropped or renamed in that conversion; `agent_tools.TOOL_SCHEMAS` remains the single source of truth, so a schema change on the Python side needs no sidecar edit.

Provider selection for the *agent loop* itself is now pi's problem, not this codebase's: `helpers/pi_client.py :: stream_chat` prefixes Anthropic model ids (e.g. `"anthropic/claude-sonnet-4-6"`) so pi resolves them against its own built-in Anthropic provider, and NRP models are registered from the roster `GET /api/internal/models` serves — filtered to `provider: "nrp"` entries whose `MODEL_METADATA["supports_tools"]` is `True`. `config.py :: get_client(model)` still exists, still returns `(client, provider)`, and is still used — but its only remaining caller is `helpers/llm_helpers.py :: llm_chat_stream`, which backs the non-agentic `/pin` acknowledgment flow, not any tool-calling path.

### The one-search-per-message gate

`search_studies` is designed to run at most once per user message. This used to be enforced by mutating the tool schema mid-loop in Python (removing `search_studies` from the list handed back to the model once it had been called); pi has no equivalent hook, so the gate now lives entirely in `pi_sidecar/sessions.mjs :: makeSearchOncePerMessageExtension` — a `tool_call` block hook that refuses a second `search_studies` call for the rest of the message, reset on `agent_start`, with a same-file refund (`makeSearchBudgetRefunder`) reading `agent_tools.ToolResult.executed` so a call rejected for empty input doesn't permanently spend the budget. [`05-agent.md`](05-agent.md#the-one-search-per-message-invariant) covers the full mechanics and the reasoning behind the optimistic-charge-then-refund design; this appendix defers to it rather than duplicating it.

The other three tools remain available on every round pi's own agent loop chooses to run — this codebase has no `max_iters` ceiling of its own any more; how many rounds a turn takes, and whether it forces a closing answer if a round ends on a bare tool result, is internal to pi.

### Running label vs. completion label

Every tool call surfaces two distinct human-readable labels, computed by two unrelated functions, at two different points in the call's lifecycle:

| Tool | In-flight label (`helpers/tool_labels.py :: _tool_label`, on `segment_tool_call`) | Completion label (`ToolResult.label`, on `segment_tool_result`) |
|---|---|---|
| `search_studies` | `"Searching: {first 3 of organism\|keywords\|qualifier\|body_site}…"`, or `"Searching Qiita…"` if none of those slots is filled | `"Searched Qiita database"` / `"Deep-searched Qiita database"` |
| `get_study_report` | `"Loading report for study {study_id}…"` | `f"Loaded study {id} report"` or `f"Study {id}"` on failure |
| `pin_study` | `"Pinning {n} study/studies…"` | `"Studies pinned"` or `"Pin studies"` on failure |
| `search_by_sample` | `"Sample search: {first 2 field=value pairs}, {first 2 keywords}…"`, or `"Searching sample metadata…"` if empty | `"Searched sample metadata"` |

The in-flight label is derived purely from the model's **arguments** — it exists so the user sees *what* is being searched for while the tool is still running, before any result exists. The completion label is fixed per tool (with `search_studies` the only one branching on an execution detail, `deep_search`) and says nothing about *what* was searched — only that a result is ready. A `_tool_label` fallback of `f"Running {name}…"` exists for any future tool name that reaches the function without a matching branch, but every currently registered tool has an explicit case.

`helpers/tool_labels.py` is a standalone module with no `agent_tools`/`pg_pool`/`qiita_core` imports, deliberately: `pi_translate.py` (a pure reducer with no database dependency) needs `_tool_label` to build `segment_tool_call` payloads, and importing it from `agent_tools.py` directly would drag in that module's whole Postgres-touching import chain (`agent_tools → study_service → pg_pool → qiita_core`) just to format a label string.

### Testing tool schemas without a live model

There is no offline CLI driver any more — `backend/agent_harness.py`, which used to call `execute_tool` directly from the command line and print the tool's full `ToolResult` without an LLM in the loop, was deleted along with the Python agent loop it drove (`run_agent_harness.sh` went with it). The closest equivalent today is calling `execute_tool` (or, for project scope, the `helpers/project_scope.py` functions `internal_tool_routes.py` dispatches to instead) directly from a test — `backend/tests/test_internal_tools_scope.py` is the existing pattern, and it is deliberately Postgres-free: every case there either short-circuits before any Qiita DB call or resolves entirely from the local SQLite `project_studies` mirror.

### How a tool call reaches Python

pi hands this codebase an **already-assembled** tool call — `{toolCallId, toolName, args}` on `tool_execution_start` — not a stream of fragments to reconstruct. There is no argument-accumulation step on the Python side any more: the deleted Python loop used to reassemble a tool call from `delta.tool_calls[].function.arguments` fragments (OpenAI-compatible models) or `input_json_delta`/`partial_json` chunks (Anthropic), keyed by chunk index or content-block id respectively, and `json.loads` the concatenated string once the stream ended — with a `try/except json.JSONDecodeError` swallowing a malformed result down to `{}`. All of that lived inside `stream_agent`/`_stream_anthropic_agent` and no longer exists; `helpers/pi_translate.py :: TurnTranslator._handle` reads `event.get("args") or {}` straight off the `tool_execution_start` event.

The call is dispatched to Flask over HTTP as `POST /api/internal/tools/<name>` with `args` as the JSON body (`pi_sidecar/tools.mjs`), authenticated with the per-turn scope token described in [`05-agent.md`](05-agent.md#the-hard-scope-boundary); `execute_tool(name, args, ...)` (`agent_tools.py`) or the project-scoped equivalents run exactly as before, and the tool's `ToolResult` is serialized back (`dataclasses.asdict`) as the HTTP response body, which pi surfaces on `tool_execution_end` as `result.details`.

---

## Per-tool reference

| Tool | Required params | Auto-pins? | Single-shot per message? |
|---|---|---|---|
| `search_studies` | none | no | **yes** — blocked by the sidecar's `tool_call` hook after the first call |
| `get_study_report` | `study_id` | no | no |
| `pin_study` | `study_ids` | yes (explicit, surfaced) | no |
| `search_by_sample` | none (but needs ≥1 of `field_filters`/`keywords`) | no | no |

### search_studies

Full-text and sample-metadata search over public Qiita studies. No parameter is required — the tool schema's `required` list is empty.

| Name | Type | Required | Description (summarized — full text in `agent_tools.py :: TOOL_SCHEMAS`) |
|---|---|---|---|
| `organism` | `array<string>` | no | Host/focal organism — common names, Latin binomials, strains, plural forms. |
| `qualifier` | `array<string>` | no | Condition/context modifiers: wild vs. captive, treated vs. control, life stage, diet. |
| `body_site` | `array<string>` | no | Anatomical location or environmental niche, with ontology synonyms. |
| `condition_or_intervention` | `array<string>` | no | Disease, treatment, or experimental manipulation, including abbreviations. |
| `project_or_pi` | `array<string>` | no | Named cohort, project, PI surname, or institution — only when the user is explicit. |
| `keywords` | `array<string>` | no | Catch-all for terms outside the typed slots, or flat keyword queries. |
| `data_types` | `array<string>` | no | AND filter over 10 valid values (`16S`, `Metagenomic`, …) — only when the user explicitly names a sequencing type. |
| `investigation_types` | `array<string>` | no | Narrower sub-filter (~18 studies matched); discouraged by its own description. |
| `limit` | `integer` | no | Clamped to 1–20 server-side; schema default 10. |

**Example call args**, as the model might fill them for "wild mice gut microbiome":

```json
{ "organism": ["mouse", "mice", "wild mouse", "Mus musculus"],
  "qualifier": ["wild", "wild-caught", "feral"],
  "body_site": ["gut", "fecal", "stool"], "limit": 8 }
```

**What the model sees back:** `_tool_search_studies` runs a text search (`search_studies_with_sql`) and a sample-metadata probe (`search_studies_by_sample_meta`) on every call — see [`04-search.md`](04-search.md) for how those are bounded and merged — then renders the merged, re-ranked, limit-trimmed list through `_format_discovery_study_list` (24,000-char budget) as `ToolResult.text`. An empty result set returns the literal string `"No matching public studies found for those keywords."` instead of an empty list.

The merge step itself is worth naming explicitly, since it determines what `via` ends up being in `ui_payload`: text-search hits (`text_studies`) and sample-metadata hits (`sample_studies`, already excluding any `study_id` the text search already found) are concatenated with text first, deduplicated by `study_id` on first occurrence — so a study appearing in both lists is tagged `via: "text"` — then re-sorted by a Python-side score (`title` match = 3, `abstract` match = 1 per keyword) and trimmed to `limit`. Sample-metadata probing prefers `organism` slot terms over the full pooled `raw_kws` when `organism` is non-empty, on the theory that the probed host-identity fields (`host_scientific_name`, etc.; see [`04-search.md`](04-search.md#the-probe)) are organism-specific and noisy against unrelated qualifier/condition terms.

**label / detail:** `"Deep-searched Qiita database"` or `"Searched Qiita database"` depending on `deep_search`; `detail` reports the trimmed result count plus a sample-metadata sub-count, e.g. `"top 8 results (incl. 3 from sample metadata of ≤40 studies)"`.

**ui_payload (success):**

```json
{ "kind": "tool_call", "tool": "search_studies",
  "args": {"keywords": [...], "data_types": [...]|null, "limit": 8},
  "sql_query": "SELECT DISTINCT ...", "result_summary": "8 studies",
  "result_studies": [{"study_id": 10317, "study_title": "...", "pi_name": "...",
                       "num_samples": 412, "data_types": "Metagenomic", "via": "text"}] }
```

`sql_query` is the literal SQL text search executed (`search_studies_with_sql(..., return_sql=True)`), not a display approximation. `via` is `"text"` or `"sample_metadata"` per study, reflecting which search found it — text hits win on overlap.

**Failure mode — shape divergence.** If no keywords are pooled at all (`raw_kws` empty across all six slots), `_tool_search_studies` returns early with a **smaller** `ui_payload`: `{"kind": "tool_call", "tool": "search_studies", "args": {"keywords": []}, "result_summary": "no keywords"}` — no `sql_query`, no `result_studies`. Any frontend renderer for this tool's `ui_payload` must treat both keys as optional.

**Side effects:** none — no pin, no write. Bounding rules (candidate caps, thread pool ≤ 16, per-statement timeout) live in `helpers/sample_search.py`; see [`04-search.md`](04-search.md).

**`deep_search` is invisible to the model.** It is not a property in this tool's JSON schema at all — the model cannot set it, request it, or even see that it exists. It originates as a plain boolean in the `/message/stream` POST body (set by the `/deepsearch` slash command or a UI toggle), but no longer travels as a plain function argument through the turn: `global_chat_routes.py` folds it into the signed scope token (`mint_scope_token(..., deep_search=deep_search, ...)`), and `routes/internal_tool_routes.py` reads it back off the *verified* token (`claims.get('deep_search')`) when dispatching a global-scope call — `execute_tool(name, args, scope='global', chat_id=..., deep_search=bool(claims.get('deep_search')))`. `helpers/pi_client.py :: stream_chat` deliberately does **not** accept it as a body field: the scope token is the only place it needs to live, and a second copy would just be an unused, potentially-stale duplicate. Its only effects are widening the sample-search candidate cap (`SAMPLE_SEARCH_DEEP_CANDIDATES=500` vs. `SAMPLE_SEARCH_DEFAULT_CANDIDATES=40`) and switching the completion label to `"Deep-searched Qiita database"`. Every other tool ignores it — `execute_tool`'s signature accepts `deep_search` for all tools uniformly, but only `_tool_search_studies` reads it. Project-scoped search (`helpers/project_scope.py`) has no `deep_search` concept at all — its scope token never carries one, and its search functions don't accept the parameter.

---

### get_study_report

Loads full sample-level metadata for one study. It does not pin the study — an earlier version auto-pinned as a side effect (wrapped in a bare `try: / except Exception: pass`, so a pin silently rejected at the 10-study cap left the model with a full report and no indication the pin had failed); that auto-pin call has been removed from `_tool_get_study_report` entirely. Pinning now only ever happens through an explicit `pin_study` call.

| Name | Type | Required | Description |
|---|---|---|---|
| `study_id` | `integer` | **yes** | The Qiita study ID to fetch. |

**What the model sees back:** `_build_full_samples_block(study_id, budget_chars=4_000)` — a compact per-sample metadata block, budget-clipped — prefixed with a one-line sample count summary.

**ui_payload:** unlike the other two non-search tools, this is **not** wrapped in a `{"kind": "tool_call", ...}` envelope. It is the raw return of `_build_samples_report_payload(study_id)` — `{"kind": "samples_report", "study_id": int, "header": {study_id, study_title, study_abstract, pi_name, pi_affiliation, num_samples, data_types, num_preps}, "samples": [...]}`. This is the same payload shape emitted by the `/report` slash-command path (`helpers/request_utils.py :: stream_samples_report`) via the `ui` SSE event — `get_study_report` reuses it verbatim so one frontend renderer (`SamplesReportBubble`) serves both entry points.

**Failure mode:** `_build_samples_report_payload` raises `ValueError` when the study is private or has zero samples/preps. Caught in `_tool_get_study_report`, which returns `text="Study {id} is private or has no accessible data in Qiita."`, `label=f"Study {id}"`, `detail="private or not found"`, and `ui_payload` left at the dataclass default of `None`.

---

### pin_study

Attaches studies to the chat for persistent context on every subsequent turn.

| Name | Type | Required | Description |
|---|---|---|---|
| `study_ids` | `array<integer>` | **yes** | Qiita study IDs to pin. |

Input is truncated to the first 10 entries before validation (`study_ids[:10]`), independent of how many are already pinned — the real cap enforcement happens inside `_pin_studies_validated` (`store/cache.py :: PINNED_STUDIES_PER_CHAT_CAP = 10`; see [Appendix B](appendix-b-sqlite-schema.md#table-chat_pinned_studies)).

**Example call args:** `{"study_ids": [10317, 11223, 11224]}`.

**What the model sees back:** a text summary built from `_pin_studies_validated`'s four-tuple: counts of `pinned_now`, `invalid` (not found/private), and `rejected` (cap reached), plus the full `all_pinned` list.

**ui_payload:**

```json
{ "kind": "tool_call", "tool": "pin_study",
  "args": {"study_ids": [10317, 11223]},
  "result_summary": "2 pinned: 10317, 11223" }
```

**Failure mode:** if every entry in `study_ids` fails `int()` coercion, the tool returns `text="No valid study IDs provided to pin."`, `label="Pin studies"`, `detail="none"`, `ui_payload=None`.

---

### search_by_sample

Searches recorded sample-level metadata directly, rather than study titles/abstracts.

| Name | Type | Required | Description |
|---|---|---|---|
| `field_filters` | `array<{field: string, value: string}>` | no | Each object requires both `field` and `value`; the array itself is optional. |
| `keywords` | `array<string>` | no | Free-text terms matched across all sample metadata fields. |
| `data_types` | `array<string>` | no | Restrict candidate studies to these data types. |
| `limit` | `integer` | no | Clamped 1–20; schema default 8. |

No top-level parameter is required, but the tool refuses to run unless **at least one** of `field_filters` or `keywords` is non-empty after filtering — see failure mode below.

**Example call args**, for "IBD patients with rectal samples":

```json
{ "field_filters": [{"field": "disease", "value": "IBD"},
                     {"field": "body_site", "value": "rectum"}],
  "limit": 8 }
```

**What the model sees back:** `search_studies_by_field_filters(..., max_candidates=200, pool_size=16)` results, rendered through the same `_format_discovery_study_list` used by `search_studies`.

**ui_payload:**

```json
{ "kind": "tool_call", "tool": "search_by_sample",
  "args": {"field_filters": [...], "keywords": [...], "data_types": [...]},
  "result_summary": "5 studies",
  "result_studies": [{"study_id": ..., "study_title": ..., "pi_name": ...,
                       "num_samples": ..., "data_types": ..., "via": "sample_metadata"}] }
```

Every entry's `via` is hardcoded to `"sample_metadata"` — unlike `search_studies`, this tool has no text-search leg to distinguish from.

**Failure mode — shape divergence.** With both `field_filters` and `keywords` empty, the early return is `{"kind": "tool_call", "tool": "search_by_sample", "args": {}, "result_summary": "no criteria"}` — again missing `result_studies`, mirroring `search_studies`'s no-keywords case.

---

There is no fifth tool. An earlier `compute_diversity` — a hard stub, live in `TOOL_SCHEMAS` and fully callable, always returning the same canned "not yet available" response regardless of arguments — has been removed entirely from both `TOOL_SCHEMAS` and `execute_tool`. Diversity computation remains unimplemented pending BIOM/OTU ingestion, tracked in [`11-roadmap.md`](11-roadmap.md).

---

## Term pooling reference

`backend/helpers/agent_tools.py :: _collect_terms(args)` is what turns `search_studies`'s six typed slots into the flat keyword lists the SQL and sample-probe layers consume. It returns a `(raw_kws, detect_kws)` pair.

| Priority | Slot | Feeds `detect_kws`? |
|---|---|---|
| 1 | `organism` | no |
| 2 | `qualifier` | no |
| 3 | `body_site` | no |
| 4 | `condition_or_intervention` | no |
| 5 | `project_or_pi` | no |
| 6 | `keywords` | **yes — the only source** |

`raw_kws` pools all six slots in that exact order, deduplicating on first occurrence (`if t not in seen`). `detect_kws` is built from the `keywords` slot alone.

**Why the order matters.** `raw_kws` is passed to `services.study_service.expand_keyword_variants`, which appends morphological variants (irregular plurals like `mouse → mice`, otherwise a naive `+s`) and then hard-truncates the result to 80 terms (`expanded[:80]`; see [`04-search.md`](04-search.md#path-2--the-canonical-sql-builder)). Because the cap is a slice, not a random sample, and because `keywords` is pooled last, **the catch-all `keywords` slot is always the first thing dropped** when a heavily-filled request crosses the cap — organism, qualifier, body-site, condition, and project/PI terms are never crowded out by an overlong catch-all list.

**Why `detect_kws` is scoped to `keywords` only.** `detect_data_types(detect_kws)` (in `study_service.py`) maps synonym tokens to canonical Qiita data types (e.g. `"shotgun"`, `"WGS"`, `"whole genome sequencing"` → `Metagenomic`) via `study_service.py :: DATA_TYPE_SYNONYMS`, and the result is AND-ed onto the search as a hard filter. If `detect_kws` pooled every slot, a biological term like `"metagenomics"` typed into `condition_or_intervention` would silently narrow the search to one data type the user never asked for. Scoping detection to the `keywords` catch-all is the mechanism that prevents that: a user who explicitly names a sequencing type is expected to either use the `data_types` parameter directly, or phrase it as a bare keyword.

`DATA_TYPE_SYNONYMS`'s ten top-level keys are exactly the ten values the `search_studies` schema documents as "valid" for the explicit `data_types` parameter — there is no canonical Qiita data type that `detect_data_types` can surface but `data_types` cannot accept directly, and vice versa. Auto-detection and explicit filtering are drawing from the same closed vocabulary; they differ only in how a caller reaches it (implicitly, via a keyword the model happens to type, versus explicitly, via the typed parameter).

### Worked example

Given these arguments (cross-slot duplicate `"mouse"` intentional, to show dedup):

| Slot | Value |
|---|---|
| `organism` | `["mouse", "mice"]` |
| `qualifier` | `["wild"]` |
| `body_site` | `["gut", "mouse"]` |
| `condition_or_intervention` | `[]` |
| `project_or_pi` | `[]` |
| `keywords` | `["gut microbiome"]` |

`_collect_terms` walks the slots in priority order, adding each string to `seen` on first sight:

```
raw_kws    = ["mouse", "mice", "wild", "gut", "gut microbiome"]
detect_kws = ["gut microbiome"]
```

The second `"mouse"` — pooled from `body_site`, after `organism` already contributed it — is dropped by the dedup check, even though it came from a different slot. `detect_kws` contains only the `keywords` entry, so `detect_data_types(["gut microbiome"])` finds no synonym match and `effective_types` is `None` unless the caller also passed `data_types` explicitly — a query about "gut microbiome" alone never triggers an assay filter.

---

## SSE wire format

`backend/helpers/llm_helpers.py :: _sse` is the single formatter used by every streaming route:

```python
def _sse(event: str, payload: dict):
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"
```

Each frame is `event: <name>\ndata: <json>\n\n` — a named SSE event with one JSON data line, terminated by a blank line. `backend/helpers/request_utils.py :: sse_response` wraps the generator in a Flask `Response` with `mimetype='text/event-stream'` and headers `Cache-Control: no-cache`, `X-Accel-Buffering: no` (the latter defeats nginx/proxy response buffering, which would otherwise hold the whole stream until it closes).

Bare `: keepalive\n\n` comment lines — no `event:`, no `data:` — are interleaved at points where a slow step (context building, pinned-report fetch) could otherwise leave the connection idle long enough for an intermediary to time it out. A line starting with `:` is an SSE comment by spec; `frontend/js/utils.js :: parseSSE` never matches it against any `event:` prefix, so it parses to a pure no-op — buffer churn with no dispatched handler.

They appear at a fixed set of call sites, all before a step that can take multiple seconds:

- `global_chat_routes.py`, immediately as `generate()` starts — before anything else is computed, so the connection has output before the client even finishes its `fetch`.
- `global_chat_routes.py`, right after the `pinned_reports` `step_done` (when the chat has pinned studies), before the `runtime` event and the call into `stream_pi_turn`.
- `chat_routes.py`, immediately as `generate()` starts, same reasoning.
- `chat_routes.py` (via `_stream_deep_context`), after the `deep_context` `step_done`, when there are mentioned or pinned studies to fetch — skipped entirely when there are none.
- `pin_flow.py :: stream_pin_flow`, after `deep_context`'s `step_done`, shared by both routes' pin flow.

Notably, the pi turn itself (`stream_pi_turn` / `TurnTranslator`) has **no** keepalive between tool calls — `token`, `segment_tool_call`, and `segment_tool_result` events are frequent enough during an agent turn that an idle-timeout gap is not expected to occur there. A long tool call on the Postgres side (a slow sample-metadata probe, say) rides inside the gap between one `segment_tool_call` and its `segment_tool_result`, same as before.

---

## All 10 events

The complete SSE vocabulary, confirmed by grepping every `_sse(...)` call site (including the ones `TurnTranslator` drives dynamically through `stream_pi_turn`) against `parseSSE`'s dispatch table — there are exactly 10, no more:

| Event | Emitted by | Purpose |
|---|---|---|
| `agent_start` | pi turns | Switches the frontend message into segments mode. |
| `segment_tool_call` | pi turns | A tool is being invoked; carries name/label/args. |
| `segment_tool_result` | pi turns | A tool call finished; carries label/detail/ui_payload. |
| `token` | pi turns, and the `/pin` acknowledgment flow | One chunk of streamed assistant text. |
| `runtime` | Both chat routes, every normal-branch turn | Names which runtime served the turn — always `{"runtime": "pi"}` today. |
| `step_start` | pi turns (compaction/retry) + pin/report flows (both routes) | A named non-tool step began. |
| `step_done` | pi turns (compaction/retry) + pin/report flows (both routes) | A named step finished. |
| `ui` | The `/report` flow (both routes) | A structured payload to render inline (samples report). |
| `done` | Every branch, both routes | Terminal event for a successful turn. |
| `error` | Every branch, both routes | Terminal event for a failed turn. |

#### event-agent_start

Synthesized by `TurnTranslator._handle` on the *first* of several pi events that indicate the model has actually started working (`agent_start`, `turn_start`, `message_start`, `message_update`, `tool_execution_start`, `compaction_start`, `auto_retry_start`) — not necessarily on pi's own literal `agent_start` event, and not on the very first event of any kind, so that a sidecar failing before the model speaks (`sidecar_error` only) doesn't flip the frontend into segments mode and hide the error text in an empty bubble. Payload: `{}` (empty object — no fields). Client: `parseSSE` dispatches to `onAgentStart`, wired in `app_state.js` to `onAgentStart(chatId) → patchLast(chatId, m => ({...m, segments: []}))`. This is the sole trigger that flips a message from `segments: null` to `segments: []` — nothing else in the frontend sets `segments` to a non-null value. Both chat routes wire the same handler set (`agentHandlers(chatId)` in `app_state.js`), so this applies identically to project chat and global chat.

#### event-segment_tool_call

Emitted by `TurnTranslator._handle` on pi's `tool_execution_start`. Payload: `{"name": str, "label": str, "args": dict}`. `args` is `event.get("args") or {}` — already-assembled by pi, no fragment accumulation on the Python side. `label` comes from `helpers/tool_labels.py :: _tool_label(toolName, args)`, a per-tool human-readable string.

`name` is the correlation key `helpers/pi_translate.py :: _tool_step_name(tool_name, tool_call_id)` builds: `f"tool_{tool_name}_{tool_call_id}"` — the **full** `toolCallId` pi assigned the call, not a truncated slice. This changed from an earlier `[:6]`-prefix scheme specifically because pi's own call ids are shaped `"tool:<epoch-ms>:<rand>"`: every id created in the same year shares the same leading digits, so a six-character prefix would read as `"tool:1"` for every call until the year 2286, and two calls to the same tool within one turn would collide onto a single correlation key. See the uniqueness note under **Ordering guarantees** below. Client: `onSegmentToolCall(chatId)` closes any open trailing text segment (marks it `done: true`) and appends `{type: 'tool', name, label, args, done: false, result: null}` to `m.segments`.

Example `name` values, given a real pi `toolCallId` of `"tool:1784970149356:aaaaaaaaaaa"`:

| Tool | `name` in `segment_tool_call` |
|---|---|
| `search_studies` | `tool_search_studies_tool:1784970149356:aaaaaaaaaaa` |
| `get_study_report` | `tool_get_study_report_tool:1784970149356:aaaaaaaaaaa` |
| `pin_study` | `tool_pin_study_tool:1784970149356:aaaaaaaaaaa` |
| `search_by_sample` | `tool_search_by_sample_tool:1784970149356:aaaaaaaaaaa` |

#### event-segment_tool_result

Emitted by `TurnTranslator._handle` on pi's `tool_execution_end`. Payload: `{"name": str, "label": str, "detail": str, "ui_payload": dict|null}`. `name` matches the `segment_tool_call` that preceded it (same `_tool_step_name` correlation key). When `event["isError"]` is true, `label` is `f"{toolName} failed"` and `detail` is the first 60 characters of the tool's own error text with a `· {dt:.1f}s` suffix (`_detail_with_elapsed`), `ui_payload: null` — a failed tool does not crash the turn; pi's own tool wrapper (`pi_sidecar/tools.mjs`) reports the failure as a normal tool result the model can read and recover from. On success, `label`/`detail`/`ui_payload` come from `result.details` — the `dataclasses.asdict()` of whatever `ToolResult` `execute_tool()` (or the project-scoped equivalent) returned — with the same elapsed-time suffix appended to `detail`. The elapsed time itself is measured on the Python side: `TurnTranslator` starts a `time.perf_counter()` clock on the matching `tool_execution_start` and reads it back here, live — not in a later replay pass, which is what used to make every persisted card read `"· 0.0s"`. Client: `onSegmentToolResult(chatId)` maps over `m.segments`, completing every tool segment matching `s.type === 'tool' && s.name === name && !s.done` — see the first-match-vs-every-match asymmetry below.

#### event-token

Emitted on pi's `message_update` events where `assistantMessageEvent.type == "text_delta"`, once per chunk. Payload: `{"token": str}`. Both chat routes now wire the identical `onTokenAgent(chatId)` handler (`app_state.js :: agentHandlers`) — there is no more separate "plain content-append" handler for one chat type and a segments-aware one for the other. `onTokenAgent` checks `m.segments`: if `null` (a defensive branch that should not fire in practice, since `agent_start` always precedes `token`) it falls back to appending to `m.content`; otherwise it appends to the last segment if that segment is `{type: 'text', done: false}`, or opens a new text segment. This is also the mechanism that closes a tool segment's implicit "gap" — a token arriving right after a `segment_tool_result` always starts a fresh text segment rather than resuming the pre-tool one.

#### event-runtime

Emitted by both `chat_routes.py` and `global_chat_routes.py`, once per normal-branch turn (not the pin or report branches), immediately before the call into `stream_pi_turn`. Payload: `{"runtime": "pi"}` — always, unconditionally; there is no other value it can take today. Client: `onRuntime(chatId)` (`app_state.js`) patches the *chat*, not the message (`patchChat(chatId, () => ({ runtime }))`), so the composer can name which runtime served the most recent turn. This event predates the deletion of the legacy Python loop, when its value genuinely varied; it is kept now as a debugging signal, not removed, even though nothing reads it to branch — see [`05-agent.md`](05-agent.md#the-runtime-pi-and-only-pi).

#### event-step_start / event-step_done

Two events, documented together because they always pair. Emitted from three places: `helpers/pi_translate.py :: TurnTranslator` (pi's own `compaction_start`/`compaction_end` and `auto_retry_start`/`auto_retry_end`, translated to a `"compaction"` or `"retry"` named step), each route's own pinned/deep-context building (`pinned_reports` in `global_chat_routes.py`, `deep_context` in `chat_routes.py`'s `_stream_deep_context`), and the shared pin/report flows (`pin_flow.py :: stream_pin_flow`'s `pin_studies`/`deep_context`/`llm_generate` steps, `request_utils.py :: stream_samples_report`'s `load_samples` step). Payload for `step_start`: `{"name": str, "label": str}`. Payload for `step_done`: `{"name": str, "label": str, "detail": str}` (detail sometimes omitted). Client: `onStepStart` sets `m.pendingStep = {name, label}`; `onStepDone` clears `pendingStep` and appends `{name, label, detail}` to `m.steps`. Neither touches `m.segments`, and both can appear on an otherwise fully agentic message — a pi turn that triggers compaction mid-stream gets both a `step_start`/`step_done` pair *and* `segment_tool_call`/`segment_tool_result` pairs in the same turn.

#### event-ui

Emitted by the shared `/report` flow (`request_utils.py :: stream_samples_report`, used by both project and global chat) after a successful `_build_samples_report_payload`. Payload is the raw payload dict itself — `{"kind": "samples_report", "study_id": ..., "header": {...}, "samples": [...]}` — not wrapped in an envelope. Client: `onUi` replaces the whole message: `patchLast(chatId, m => ({...m, ui: payload, content: ''}))`. This is the only handler that clears `content` as a side effect, and it does not touch `segments` — a `/report` message and an agentic message can both end up with a populated `m.ui`, but only the agentic one also has non-null `m.segments`.

#### event-done

Terminal event for a successful turn, on every branch of both routes. Payload always includes `{"chat_id": str, "persisted": true}`; both chat types additionally include `"pinned_studies": [...]` whenever the turn touched pins — either the pin-flow's `all_pinned` result, or, for a turn whose `ui_payload.kind == "agent_segments"`, a fresh `list_pinned_studies(chat_id, scope)` re-read after the fact, because `get_study_report`/`pin_study` may have pinned or (via `get_study_report`, historically) attempted to pin studies mid-turn without any other event reporting the updated list. A report-only turn or a plain agent turn with no tool activity omits `pinned_studies` entirely rather than sending an empty list — the frontend leaves existing pins untouched when the key is absent. Client: `onDone` calls `applyStreamDone(chatId, title, payload?.pinned_studies ?? null)` — this is also where segments get frozen into `m.ui`; see below.

#### event-error

Terminal event for a failed turn, on every branch of both routes, from the single `except Exception` wrapping the whole `generate()` body in both route handlers — including exceptions raised inside `stream_pi_turn` (a sidecar HTTP failure, a translation bug, anything `TurnTranslator` doesn't itself catch). Payload: `{"error": str}`, built by `helpers/llm_helpers.py :: friendly_llm_error(e, model)`, which rewrites known exception shapes into a user-facing sentence before falling back to `str(exc)`:

| Match | Resulting message |
|---|---|
| `helpers.pi_client.PiSidecarError` | `"The chat service is not responding. This is a backend problem, not a model one — switching models will not help."` |
| `anthropic.RateLimitError` | `"{model} rate limit reached. Please wait a moment and try again."` |
| `anthropic.APIConnectionError` / `APIStatusError` | `"{model} is currently unavailable. Check your ANTHROPIC_API_KEY and try again."` |
| Substring match on `"upstream connect error"`, `"connection refused"`, `"remote connection failure"`, `"delayed connect error"`, `"connection reset"`, `"service unavailable"`, `"502"`, `"503"`, `"504"` (case-insensitive) | `"{model} is currently unavailable on NRP-Nautilus. Try selecting a different model from the dropdown below the chat box."` |
| Anything else | `str(exc)` or `exc.__class__.__name__`, verbatim |

The `PiSidecarError` check runs first, before the connection-marker substring match, precisely because a dead sidecar's own error text (`"sidecar unreachable: connection refused"`) would otherwise match those markers and wrongly suggest switching models — every model routes through the same sidecar, so that advice would be useless.

Client: `onError` sets the global `compErr` state and patches the last message to `isStreaming: false` with a `⚠️`-prefixed fallback `content` if none was streamed. No `done` event follows an `error` event — they are mutually exclusive terminals.

**A turn that errors is not persisted at all.** `append_global_chat_messages(...)` / `append_chat_messages(...)` is called exactly once per `generate()` invocation, immediately after the streaming work finishes successfully — and it is inside the same `try` block the error handler wraps. If any exception (from `stream_pi_turn`, from a context-building step, from anything) is raised before that call is reached, the `except` block yields `error` and returns; the call to persist the turn never happens. This means a mid-stream failure discards **both** the user's message and any partial assistant output — nothing about the failed turn survives a reload, only the transient frontend state (`compErr`, and whatever segments were built live in `m.segments`, left unfrozen since `applyStreamDone` never runs on this path).

---

## The persisted `ui_payload` shape

On `done`, an agent turn's accumulated segments are frozen into one JSON structure and written to the `ui_payload` column of `global_chat_messages` / `project_chat_messages` (see [Appendix B](appendix-b-sqlite-schema.md#table-global_chat_messages)):

```json
{ "kind": "agent_segments",
  "segments": [
    {"type": "text", "content": "Here's what I found...", "done": true},
    {"type": "tool", "name": "tool_search_studies_tool:1784970149356:aaaaaaaaaaa", "label": "Searched Qiita database",
     "args": {"keywords": ["mouse", "gut"]}, "done": true,
     "result": {"label": "Searched Qiita database", "detail": "top 8 results · 1.2s",
                "ui_payload": {"kind": "tool_call", "tool": "search_studies", "...": "..."}}}
  ] }
```

**Text segment:** `{"type": "text", "content": str, "done": bool}`.
**Tool segment:** `{"type": "tool", "name": str, "label": str, "args": obj, "done": bool, "result": null | {"label": str, "detail": str, "ui_payload": obj|null}}`. The inner `result.ui_payload` is whichever tool-specific shape the per-tool section above describes:

| Tool | Inner `result.ui_payload["kind"]` | Present on failure? |
|---|---|---|
| `search_studies` | `"tool_call"` (reduced fields if no keywords) | yes — reduced shape, not `null` |
| `get_study_report` | `"samples_report"` (no `"tool_call"` envelope) | no — `null` on private/not-found |
| `pin_study` | `"tool_call"` | no — `null` on no valid IDs |
| `search_by_sample` | `"tool_call"` (reduced fields if no criteria) | yes — reduced shape, not `null` |

A frontend renderer switching on `result.ui_payload.kind` must therefore handle `"tool_call"`, `"samples_report"`, and `null`/absent as three genuinely distinct cases per tool, not just success vs. failure.

> **This shape used to be built twice, independently, in two languages — now it is built once, in Python, from one pass.** `helpers/pi_translate.py :: TurnTranslator` accumulates `.segments` as it walks pi's event stream (the same walk that drives the live SSE frames), and `helpers/pi_turn.py :: stream_pi_turn` returns `{"kind": "agent_segments", "segments": translator.segments}` for both `chat_routes.py` and `global_chat_routes.py` to persist via `append_chat_messages(..., assistant_ui_payload=ui_payload)` / `append_global_chat_messages(...)`. Client-side, `app_state.js :: applyStreamDone` still independently re-derives the same structure from the live `m.segments` React state and freezes it into `m.ui` — that half of the dual-authoring hazard is unchanged, because the frontend has no way to receive the server's already-built copy before the stream ends. `tests/test_pi_translate.py` is the parity test for the *server* half, fed a real captured pi event stream. See [`06-streaming-and-chat.md`](06-streaming-and-chat.md) for the full round-trip and what's left of the hazard.

**The first-match-vs-every-match asymmetry — still present, on different code.** On `segment_tool_result`, the server and client still use different matching strategies over an otherwise-identical loop:

- **Server** (`helpers/pi_translate.py :: TurnTranslator._close_tool_segment`): `for seg in self.segments: if ... : ...; break` — completes the **first** not-done tool segment whose `name` matches, then stops.
- **Client** (`app_state.js :: onSegmentToolResult`): `(m.segments || []).map(s => ... ? {...} : s)` — completes **every** not-done tool segment whose `name` matches.

These still produce identical output, but the reason changed. It used to hold because tool calls executed strictly sequentially (at most one open call at any time) *and* the correlation name was call-id-suffixed; now the sequencing guarantee is weaker to state (see **Ordering guarantees** below), so the invariant that matters is narrower and stronger: `_tool_step_name` correlates on pi's **full**, provider-assigned `toolCallId` rather than a `[:6]` prefix, which is unique per call by construction — two concurrent or interleaved calls to the *same* tool in one turn get two distinct names regardless of execution order, so there is never more than one not-done segment sharing a `name` for either loop to find. `tests/test_pi_translate.py :: test_distinct_calls_to_the_same_tool_do_not_collide` pins exactly this case (two `get_study_report` calls open at once) and asserts each result correlates back to its own call. If tool naming ever again collapses toward non-uniqueness, the two implementations would diverge silently, the same way they always could.

---

## Event ordering guarantees

- `agent_start` always precedes any `segment_tool_call` or `segment_tool_result` in a given turn — `TurnTranslator` synthesizes it on the first "the model started" pi event, before any tool-call event can occur.
- Every `segment_tool_call` is eventually paired with a `segment_tool_result` carrying the same `name`, **or** the turn terminates via `done`/`error` first (a sidecar crash or a dropped connection mid-tool-call leaves an orphaned `done: false` tool segment in the frontend's in-memory state — `applyStreamDone` does not force-close it, it only force-closes trailing **text** segments).
- `token` events may interleave freely with `segment_tool_call`/`segment_tool_result` pairs — the model can emit narrative text before, between, and after tool calls in the same turn, and each such run becomes its own `{type: 'text', ...}` segment, split wherever a tool call interrupts it.
- **Multiple tool calls within one turn are not guaranteed to execute (or be reported) strictly sequentially any more.** The deleted Python loop ran each call in a `for` loop, one at a time, so a call's `segment_tool_result` was always emitted before the next call's `segment_tool_call`. That guarantee was specific to a loop this codebase no longer runs — tool iteration now happens inside pi, which this codebase does not control or fully observe from the outside. `pi_translate.py`'s own test suite exercises the case where two `tool_execution_start` events for the same tool name arrive before either `tool_execution_end` (see the asymmetry note above), and the translator is correct under that ordering because correlation is by full call id, not by there being at most one open call. Whether pi's real runtime ever actually overlaps two tool calls in production is not something this codebase asserts either way — only that the translator no longer *depends* on it not happening.
- `step_start`/`step_done` pairs (pi's compaction/retry translation, and the pin/report flows on both routes) always nest correctly — the codebase never emits two consecutive `step_start` events for different `name`s without an intervening `step_done`.
- `done` is always the last event of a successful turn; `error` is always the last event of a failed one. The two are mutually exclusive per turn, and no event of any kind follows either.
- Whatever decides to emit closing prose after a tool-only round, or to give up after too many rounds without concluding — the equivalents of the deleted loop's "forced synthesis" and `max_iters` ceiling — is now internal to pi. This codebase has no visibility into whether a given turn's `token` events came from an ordinary round or from pi recovering a bare tool result into an answer; there is no server-side log line or SSE event that reports it either way.

---

## Constants referenced in this appendix

All are read from `backend/config.py` and documented fully in [`appendix-d-configuration.md`](appendix-d-configuration.md); listed here as a quick lookup for the numbers this appendix cites inline.

| Constant | Default | Governs |
|---|---|---|
| `SAMPLE_SEARCH_DEFAULT_CANDIDATES` | `40` | Sample-probe candidate cap, `deep_search=False` |
| `SAMPLE_SEARCH_DEEP_CANDIDATES` | `500` | Sample-probe candidate cap, `deep_search=True` |
| `PINNED_STUDIES_PER_CHAT_CAP` (`store/cache.py`) | `10` | `pin_study`'s cap |
| `PI_SCOPE_TOKEN_TTL_SECONDS` | `600` | How long a minted scope token authorizes tool calls for one turn |
| `max_tokens` (Anthropic calls only, `/pin` flow) | `4096` | Hard cap on one `llm_chat_stream` completion's output — this is the non-agentic pin-acknowledgment path, not the tool-calling loop, which pi controls internally |
| Tool-count in `TOOL_SCHEMAS` | `4` | Fixed — adding a fifth tool requires touching every file this appendix describes |

---

*See also: [`05-agent.md`](05-agent.md) for how the model is prompted to fill tool arguments and choose between dimensions, and the design rationale behind the one-search-per-message invariant · [`06-streaming-and-chat.md`](06-streaming-and-chat.md) for the full SSE-to-hydration round trip, the dual-authoring hazard, and the segment tri-state · [`04-search.md`](04-search.md) for how `search_studies` and `search_by_sample` bound their underlying queries · [`appendix-d-configuration.md`](appendix-d-configuration.md) for `MODEL_METADATA` and the sample-search candidate caps.*