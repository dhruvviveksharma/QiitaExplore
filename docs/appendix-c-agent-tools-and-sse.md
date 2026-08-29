# Appendix C — Agent Tools and the SSE Protocol

*The exact tool schemas the model sees, and the exact wire protocol the browser receives — the two halves of a contract that only holds together if `agent_tools.py`, `agent.py`, `global_chat_routes.py`, and the frontend segments model change in lockstep.*

---

## Scope: both chat streams

Agentic tool-calling runs on both streaming endpoints:

| Route | Schemas | Search |
|---|---|---|
| `POST /api/global-chats/<chat_id>/message/stream` (`global_chat_routes.py`) | `TOOL_SCHEMAS` | Public Qiita |
| `POST /api/projects/<pid>/chats/<cid>/message/stream` (`chat_routes.py`) | `PROJECT_TOOL_SCHEMAS` | Local SQLite membership only |

`execute_tool` routes by `scope` before dispatch. Project scope rejects global tool names fail-closed. `/pin` and `/report` slash commands remain non-agentic on both routes.

---

## Tool schema conventions

`backend/helpers/agent_tool_schemas.py` defines two schema lists in OpenAI function-calling format:

- **`TOOL_SCHEMAS`** — global chat: `search_studies`, `search_by_sample`, `get_study_report`, `pin_study`.
- **`PROJECT_TOOL_SCHEMAS`** — project chat: `search_project_studies`, `get_project_study_report`, `pin_study`.

Each route passes its list as the required `tools=` argument to `stream_agent`. The list is passed directly as `tools=` to `client.chat.completions.create(...)` for every NRP-Nautilus (`provider: "nrp"`) model. For Anthropic models (`provider: "anthropic"` in `config.py :: MODEL_METADATA`), `backend/helpers/agent.py :: _openai_tools_to_anthropic` reshapes each entry into `{"name", "description", "input_schema"}` — `input_schema` is the OpenAI `parameters` object, passed through unchanged. No field is added, dropped, or renamed in translation, so the JSON Schema itself — types, the `required` list, and every prose description the model reads to decide what synonyms to fill in — is identical between providers. The translation runs once per turn, at the top of `_stream_anthropic_agent`, not once per loop iteration.

Provider selection happens in `config.py :: get_client(model)`, which `stream_agent` calls once to get `(llm_client, provider)`. Chat routes never branch on tool capability — every model in the roster supports tool calls (the old `model_supports_tools()` helper is gone).

### The `active_tools` mutation

`search_studies` and `search_project_studies` are designed to run at most once per turn. Both provider loops track a local `search_already_done` flag, set when `_execute_tool_call` returns `is_search=True` (either search tool name), and once set, filter the schema handed to the model on every subsequent round:

- OpenAI path (`stream_agent`): `active_tools = [t for t in TOOL_SCHEMAS if t["function"]["name"] != "search_studies"]`
- Anthropic path (`_stream_anthropic_agent`): `curr_tools = [t for t in anth_tools if t["name"] != "search_studies"]`

This is enforced at the schema level, not just by the tool's own description ("Issue EXACTLY ONE call per user request…") — after the first call, the model cannot see `search_studies` as an option at all. The other four tools remain available on every iteration up to `max_iters` (default `4` in `stream_agent`; the CLI harness raises it to `8`). [`05-agent.md`](05-agent.md#the-one-search-invariant) calls this the *one-search invariant* and covers the design rationale (schema mutation over prompt instruction); this appendix covers only the mechanics.

### Running label vs. completion label

Every tool call surfaces two distinct human-readable labels, computed by two unrelated functions, at two different points in the call's lifecycle:

| Tool | In-flight label (`agent.py :: _tool_label`, on `segment_tool_call`) | Completion label (`ToolResult.label`, on `segment_tool_result`) |
|---|---|---|
| `search_studies` | `"Searching: {first 3 of organism\|keywords\|qualifier\|body_site}…"`, or `"Searching Qiita…"` if none of those slots is filled | `"Searched Qiita database"` / `"Deep-searched Qiita database"` |
| `get_study_report` | `"Loading report for study {study_id}…"` | `f"Loaded study {id} report"` or `f"Study {id}"` on failure |
| `pin_study` | `"Pinning {n} study/studies…"` | `"Studies pinned"` or `"Pin studies"` on failure |
| `search_by_sample` | `"Sample search: {first 2 field=value pairs}, {first 2 keywords}…"`, or `"Searching sample metadata…"` if empty | `"Searched sample metadata"` |
| `compute_diversity` | `"Computing diversity…"` | `"Diversity (unavailable)"` |

The in-flight label is derived purely from the model's **arguments** — it exists so the user sees *what* is being searched for while the tool is still running, before any result exists. The completion label is fixed per tool (with `search_studies` the only one branching on an execution detail, `deep_search`) and says nothing about *what* was searched — only that a result is ready. A `_tool_label` fallback of `f"Running {name}…"` exists for any future tool name that reaches the function without a matching branch, but every currently registered tool has an explicit case.

### Testing tool schemas without a live model

`backend/agent_harness.py --tool <name> --args '<json>'` calls `execute_tool` directly — the same dispatcher `stream_agent` uses — with no LLM in the loop at all. It prints the tool's full `ToolResult.text` (what the model would have read), `label`, `detail`, and elapsed time, via a traced wrapper (`_traced_execute_tool`) that the harness monkey-patches over `agent_mod.execute_tool`. This is the fastest way to check a schema or tool-body change against real data: `bash run_agent_harness.sh --tool search_studies --args '{"organism":["mouse"]}'` exercises the full text-search + sample-probe path and prints the exact string the LLM would receive, without spending an LLM call or needing a tool-capable model configured at all.

### How a tool call is assembled from streaming deltas

Neither provider hands `stream_agent` a complete tool call in one piece — both stream it in fragments that have to be accumulated before `execute_tool` can run.

**OpenAI-compatible path** (`stream_agent`'s main loop): each chunk's `delta.tool_calls` is a list of partial updates keyed by `tc.index`, not by call ID — `tool_call_map[idx]` starts as `{"id": "", "name": "", "arguments": ""}` and is mutated in place as chunks arrive: `tc.id` (once, when present), `fn.name` and `fn.arguments` are **concatenated** onto the running strings on every chunk that carries them, since providers may split a function name or a JSON arguments string across multiple deltas. Only after the stream ends (`finish_reason == "tool_calls"`) does the loop `json.loads(tc["arguments"] or "{}")` each accumulated string, in index order.

**Anthropic path** (`_stream_anthropic_agent`): a `content_block_start` event with `cb.type == "tool_use"` opens `current_block = {"id": cb.id, "name": cb.name}` and resets `current_json = ""`; subsequent `content_block_delta` events of type `input_json_delta` append `d.partial_json` to `current_json`; `content_block_stop` parses the fully-accumulated string and appends `{"id", "name", "args"}` to `tool_uses`. Unlike the OpenAI path, the tool's `name` arrives whole in one event rather than being built incrementally.

**Both paths swallow a malformed-JSON arguments string the same way** — `json.loads(...)` wrapped in `try/except json.JSONDecodeError`, defaulting to `{}` rather than raising. A tool call with unparseable arguments still executes with empty args; for tools with required parameters (`get_study_report`, `pin_study`, `compute_diversity`), the tool's own coercion (e.g. `int(args.get("study_id") or 0)` → `0`) or emptiness check then produces that tool's normal failure `ToolResult` rather than crashing the turn.

**A fixed, provider-asymmetric token cap.** Every Anthropic call in this module — both the main loop's `messages.stream(...)` and the forced-synthesis fallback — passes `max_tokens=4096` explicitly; the Anthropic SDK requires it. The OpenAI-compatible `client.chat.completions.create(...)` calls pass no `max_tokens` at all, deferring entirely to the endpoint's own default. A very long synthesized answer can therefore be cut off mid-sentence on an Anthropic model in a way it structurally cannot be on an NRP-Nautilus model, purely because of this asymmetry — nothing in the SSE wire format signals that a `token` stream ended because of a length cap versus a natural stop.

---

## Per-tool reference

| Tool | Required params | Auto-pins? | Single-shot per turn? |
|---|---|---|---|
| `search_studies` | none | no | **yes** — removed from schema after first call |
| `get_study_report` | `study_id` | **yes** (silent failure on cap) | no |
| `pin_study` | `study_ids` | yes (explicit, surfaced) | no |
| `search_by_sample` | none (but needs ≥1 of `field_filters`/`keywords`) | no | no |
| `compute_diversity` | `study_ids` | no | no — but always a stub response regardless |

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
| `limit` | `integer` | no | Clamped to 1–20 server-side; schema default 8. |

**Example call args**, as the model might fill them for "wild mice gut microbiome":

```json
{ "organism": ["mouse", "mice", "wild mouse", "Mus musculus"],
  "qualifier": ["wild", "wild-caught", "feral"],
  "body_site": ["gut", "fecal", "stool"], "limit": 8 }
```

**What the model sees back:** `_tool_search_studies` runs a text search (`search_studies_with_sql`) and a sample-metadata probe (`search_studies_by_sample_meta`) on every call — see [`04-search.md`](04-search.md) for how those are bounded and merged — then renders the merged, re-ranked, limit-trimmed list through `_format_discovery_study_list` (8,000-char budget) as `ToolResult.text`. An empty result set returns the literal string `"No matching public studies found for those keywords."` instead of an empty list.

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

**`deep_search` is invisible to the model.** It is not a property in this tool's JSON schema at all — the model cannot set it, request it, or even see that it exists. It originates as a plain boolean in the `/message/stream` POST body (defaulting to `true` when omitted; the frontend always sends `deep_search: true`), threads through `stream_agent(..., deep_search=deep_search)` as a Python keyword argument, and reaches `_tool_search_studies` via `execute_tool(..., deep_search=deep_search)`. Its only effects are widening the sample-search candidate cap (`SAMPLE_SEARCH_DEEP_CANDIDATES=500` vs. `SAMPLE_SEARCH_DEFAULT_CANDIDATES=40`) and switching the completion label to `"Deep-searched Qiita database"`. Every other tool ignores it — `execute_tool`'s signature accepts `deep_search` for all tools uniformly, but only `_tool_search_studies` reads it.

---

### get_study_report

Loads full sample-level metadata for one study and auto-pins it.

| Name | Type | Required | Description |
|---|---|---|---|
| `study_id` | `integer` | **yes** | The Qiita study ID to fetch. |

**What the model sees back:** `_build_full_samples_block(study_id, budget_chars=4_000)` — a compact per-sample metadata block, budget-clipped — prefixed with a one-line sample count summary.

**ui_payload:** unlike the other three non-stub tools, this is **not** wrapped in a `{"kind": "tool_call", ...}` envelope. It is the raw return of `_build_samples_report_payload(study_id)` — `{"kind": "samples_report", "study_id": int, "header": {study_id, study_title, study_abstract, pi_name, pi_affiliation, num_samples, data_types, num_preps}, "samples": [...]}`. This is the same payload shape emitted by the `/report` slash-command path (`helpers/request_utils.py :: stream_samples_report`) via the `ui` SSE event — `get_study_report` reuses it verbatim so one frontend renderer (`SamplesReportBubble`) serves both entry points.

**Side effect — silent pin.** After building the payload, the tool calls `_pin_studies_validated(chat_id, scope, [study_id])` wrapped in a bare `try: / except Exception: pass`. If the chat is already at the 10-study pin cap, the pin is silently rejected — the model gets a full report, the frontend never learns the pin failed, and there is no `rejected` count surfaced anywhere in this tool's output (contrast with `pin_study` below, which does surface it).

**Failure mode:** `_build_samples_report_payload` raises `ValueError` when the study is private or has zero samples/preps. Caught in `_tool_get_study_report`, which returns `text="Study {id} is private or has no accessible data in Qiita."`, `label=f"Study {id}"`, `detail="private or not found"`, and `ui_payload` left at the dataclass default of `None`. No pin is attempted on this path.

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

### compute_diversity

> **Stub.** `_tool_compute_diversity` ignores its arguments entirely — the parameter is named `_args` to signal this in the source — and always returns the same canned, non-computed response. It is present in `TOOL_SCHEMAS` and fully callable; nothing in the schema or system prompt currently discourages the model from selecting it. Treat any description of this tool as aspirational until TKT-010 (BIOM/OTU ingestion) lands.

| Name | Type | Required | Description |
|---|---|---|---|
| `study_ids` | `array<integer>` | **yes** | Study IDs to compute diversity for. Accepted by the schema; never read. |
| `metric` | `string` | no | e.g. `"shannon"`, `"bray_curtis"`. Accepted by the schema; never read. |

**Always returns:** `text="Diversity analysis is not yet available. BIOM/OTU ingestion is pending (TKT-010). …"`, `label="Diversity (unavailable)"`, `detail="pending TKT-010"`, `ui_payload=None`. No branch of this function can produce a different result.

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
- `global_chat_routes.py`, right after the `pinned_reports` `step_done` (when present).
- `chat_routes.py`, after `build_context`'s `step_done` and after `deep_context`'s `step_done`.
- `pin_flow.py :: stream_pin_flow`, after `deep_context`'s `step_done`, shared by both routes' pin flow.

Notably, the agentic path itself (`stream_agent`'s tool loop) has **no** keepalive between tool calls — `token`, `segment_tool_call`, and `segment_tool_result` events are frequent enough during an agent turn that an idle-timeout gap is not expected to occur there.

---

## All SSE events

The complete SSE vocabulary, confirmed by grepping every `_sse(...)` call site against `parseSSE`'s dispatch table:

| Event | Emitted by | Purpose |
|---|---|---|
| `agent_start` | Agent turns (both routes) | Switches the frontend message into segments mode. |
| `segment_tool_call` | Agent turns | A tool is being invoked; carries name/label/args. |
| `segment_tool_result` | Agent turns | A tool call finished; carries label/detail/ui_payload. |
| `token` | Agent turns, pin/report refusal | One chunk of streamed assistant text. |
| `step_start` | Context prep, pin/report flows | A named non-tool step began. |
| `step_done` | Context prep, pin/report flows | A named step finished. |
| `ui` | `/report` member flow | A structured payload to render inline (samples report). |
| `done` | All successful turns | Terminal event. |
| `error` | All failed turns | Terminal event. |

#### event-agent_start

Emitted once at the start of each agent turn on both routes. Payload: `{}` (empty object). Client: `onAgentStart` sets `segments: []`, flipping from steps/content rendering to segment rendering.

#### event-segment_tool_call

Emitted by `_execute_tool_call` in `agent.py` before a tool runs. Payload: `{"name": str, "label": str, "args": dict}`. `name` is the synthetic, call-id-suffixed identifier `f"tool_{name}_{call_id[:6]}"` — see the uniqueness note under **Ordering guarantees** below. `label` comes from `agent.py :: _tool_label(name, args)`, a per-tool human-readable string. Client: `onSegmentToolCall(chatId)` closes any open trailing text segment (marks it `done: true`) and appends `{type: 'tool', name, label, args, done: false, result: null}` to `m.segments`.

Example synthetic `name` values, given a provider `call_id` of `"call_a1b2c3d4e5"`:

| Tool | `name` in `segment_tool_call` |
|---|---|
| `search_studies` | `tool_search_studies_a1b2c3` |
| `get_study_report` | `tool_get_study_report_a1b2c3` |
| `pin_study` | `tool_pin_study_a1b2c3` |
| `search_by_sample` | `tool_search_by_sample_a1b2c3` |
| `compute_diversity` | `tool_compute_diversity_a1b2c3` |

`call_id[:6]` takes the first six characters of whatever ID the provider assigned that call — OpenAI-compatible providers and Anthropic both generate their own opaque call IDs, so the six-character slice is provider format, not a value this codebase controls.

#### event-segment_tool_result

Emitted by `_execute_tool_call` after the tool returns (or raises). Payload: `{"name": str, "label": str, "detail": str, "ui_payload": dict|null}`. `name` matches the `segment_tool_call` that preceded it. On a caught exception, `label` is `f"{name} failed"` and `detail` is `f"{str(exc)[:60]} · {dt:.1f}s"`, with `ui_payload: null` — the tool loop does not crash the whole turn on a single tool exception; it feeds `f"Tool {name} failed: {exc}"` back to the model as the tool result and continues. On success, `detail` is the tool's own `ToolResult.detail` with an appended `· {elapsed:.1f}s`. Client: `onSegmentToolResult(chatId)` maps over `m.segments`, completing every tool segment matching `s.type === 'tool' && s.name === name && !s.done` — see the first-match-vs-every-match asymmetry below.

#### event-token

Emitted during agent turns and pin/report refusal paths. Payload: `{"token": str}`. Both chat call sites use `onTokenAgent(chatId)`, which appends to the last open text segment or opens a new one. `/pin` and `/report` branches that never enter `stream_agent` still append to `m.content` via the same handler's fallback when `segments` is null.

#### event-step_start / event-step_done

Two events, documented together because they always pair. Emitted by context-prep steps (`build_context`, `deep_context`, `pinned_reports` in both routes) and by the shared pin/report flows (`pin_flow.py :: stream_pin_flow`, `request_utils.py :: stream_samples_report`). Payload for `step_start`: `{"name": str, "label": str}`. Payload for `step_done`: `{"name": str, "label": str, "detail": str}` (detail sometimes omitted). Client: `onStepStart` sets `m.pendingStep`; `onStepDone` appends to `m.steps`. Agent turns use `m.segments` instead once `agent_start` fires.

#### event-ui

Emitted by the shared `/report` flow (`request_utils.py :: stream_samples_report`, used by both project and global chat) after a successful `_build_samples_report_payload`. Payload is the raw payload dict itself — `{"kind": "samples_report", "study_id": ..., "header": {...}, "samples": [...]}` — not wrapped in an envelope. Client: `onUi` replaces the whole message: `patchLast(chatId, m => ({...m, ui: payload, content: ''}))`. This is the only handler that clears `content` as a side effect, and it does not touch `segments` — a `/report` message and an agentic message can both end up with a populated `m.ui`, but only the agentic one also has non-null `m.segments`.

#### event-done

Terminal event for a successful turn, on both paths. Payload always includes `{"chat_id": str, "persisted": true}`; global chat additionally includes `"pinned_studies": [...]` whenever the turn touched pins — either the pin-flow's `all_pinned` result, or (for agent turns specifically) a fresh `list_pinned_studies(chat_id, SCOPE_GLOBAL)` re-read after the fact, because `get_study_report` and `pin_study` may have pinned studies mid-turn without any other event reporting the updated list. Client: `onDone` calls `applyStreamDone(chatId, title, payload?.pinned_studies ?? null)` — this is also where segments get frozen into `m.ui`; see below.

#### event-error

Terminal event for a failed turn, on both paths, from the single `except Exception` wrapping the whole `generate()` body in both route handlers — including exceptions raised deep inside `stream_agent`'s tool loop, since nothing there catches beyond the per-tool `try/except` in `_execute_tool_call`. Payload: `{"error": str}`, built by `helpers/llm_helpers.py :: friendly_llm_error(e, model)`, which rewrites three classes of exception into a user-facing sentence before falling back to `str(exc)`:

| Match | Resulting message |
|---|---|
| `anthropic.RateLimitError` | `"{model} rate limit reached. Please wait a moment and try again."` |
| `anthropic.APIConnectionError` / `APIStatusError` | `"{model} is currently unavailable. Check your ANTHROPIC_API_KEY and try again."` |
| Substring match on `"upstream connect error"`, `"connection refused"`, `"remote connection failure"`, `"delayed connect error"`, `"connection reset"`, `"service unavailable"`, `"502"`, `"503"`, `"504"` (case-insensitive) | `"{model} is currently unavailable on NRP-Nautilus. Try selecting a different model from the dropdown below the chat box."` |
| Anything else | `str(exc)` or `exc.__class__.__name__`, verbatim |

Client: `onError` sets the global `compErr` state and patches the last message to `isStreaming: false` with a `⚠️`-prefixed fallback `content` if none was streamed. No `done` event follows an `error` event — they are mutually exclusive terminals.

**A turn that errors is not persisted at all.** `append_global_chat_messages(...)` is called exactly once per `generate()` invocation, immediately after the streaming work finishes successfully — and it is inside the same `try` block the error handler wraps. If any exception (from `stream_agent`, from a context-building step, from anything) is raised before that call is reached, the `except` block yields `error` and returns; the call to persist the turn never happens. This means a mid-stream failure discards **both** the user's message and any partial assistant output — nothing about the failed turn survives a reload, only the transient frontend state (`compErr`, and whatever segments were built live in `m.segments`, left unfrozen since `applyStreamDone` never runs on this path).

---

## `stream_agent` yield types vs. SSE events

`stream_agent` (and `_stream_anthropic_agent`) yield five typed dict shapes internally. Only four are ever translated onto the wire:

| `stream_agent` yield `"type"` | SSE event emitted | Where |
|---|---|---|
| `agent_start` | `agent_start` | `global_chat_routes.py` |
| `token` | `token` | `global_chat_routes.py` |
| `segment_tool_call` | `segment_tool_call` | `global_chat_routes.py` |
| `segment_tool_result` | `segment_tool_result` | `global_chat_routes.py` |
| `reasoning` | **none** | not translated by any route |

**The gap.** `reasoning` is yielded whenever an OpenAI-compatible reasoning model (e.g. `minimax-m2`) returns a non-empty `delta.reasoning_content` chunk — visible in `stream_agent`'s main loop. No route — not `global_chat_routes.py`, not any other — has a branch that forwards `"reasoning"` events onto the SSE wire, and `parseSSE` has no `onReasoning` handler to receive one even if it did. The **only** consumer of this yield type in the entire codebase is `backend/agent_harness.py`, the CLI debugging tool, which patches `agent_mod.execute_tool` and prints reasoning tokens dimmed to the terminal. In the web product, a reasoning model's thinking is silently dropped — the user sees only the tool-call segments and the final synthesized answer, with no indication that reasoning happened at all. Note also that `_stream_anthropic_agent` has no equivalent branch — it never yields `"reasoning"` in the first place, since Claude's streaming API does not expose a comparable field through this integration.

---

## The persisted `ui_payload` shape

On `done`, an agent turn's accumulated segments are frozen into one JSON structure and written to the `ui_payload` column of `global_chat_messages` (see [Appendix B](appendix-b-sqlite-schema.md#table-global_chat_messages)):

```json
{ "kind": "agent_segments",
  "segments": [
    {"type": "text", "content": "Here's what I found...", "done": true},
    {"type": "tool", "name": "tool_search_studies_a1b2c3", "label": "Searched Qiita database",
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
| `compute_diversity` | — | always `null`, every call |

A frontend renderer switching on `result.ui_payload.kind` must therefore handle `"tool_call"`, `"samples_report"`, and `null`/absent as three genuinely distinct cases per tool, not just success vs. failure.

> **This shape is built twice, independently, and must be kept in agreement by hand.** Server-side, `global_chat_routes.py`'s `generate()` closure assembles `segments_list` as SSE events stream through, and writes it via `append_global_chat_messages(..., assistant_ui_payload=ui_payload)`. Client-side, `app_state.js :: applyStreamDone` builds the identical structure from the live `m.segments` React state and freezes it into `m.ui` for the current render — this is what the user sees immediately, before any reload. As of this writing the two agree field-for-field, including `args`. If you touch either assembly site, touch both, and re-verify by hydrating a reloaded page — see [`06-streaming-and-chat.md`](06-streaming-and-chat.md) for the full round-trip.

**The first-match-vs-every-match asymmetry.** On `segment_tool_result`, the two assemblies use different matching strategies over an otherwise-identical loop:

- **Server** (`global_chat_routes.py`): a plain `for seg in segments_list: if ... : ...; break` — completes the **first** not-done tool segment whose `name` matches, then stops.
- **Client** (`app_state.js :: onSegmentToolResult`): `(m.segments || []).map(s => ... ? {...} : s)` — completes **every** not-done tool segment whose `name` matches.

These produce identical output today **only because** the synthetic `name` (`f"tool_{name}_{call_id[:6]}"`) is suffixed with the LLM's tool-call ID and is therefore unique per invocation — there is never more than one not-done segment with a given `name` for either loop to find. If tool naming ever changes to something non-unique (e.g. dropping the call-id suffix, or reusing names across a synthesized multi-call batch), the two implementations diverge silently: the server would keep completing only the oldest open call of that name, while the client would complete all open calls of that name at once, and a page reload would render differently than the live stream did. Treat call-id-suffixed uniqueness as a load-bearing invariant of this whole subsystem, not an implementation detail.

---

## Event ordering guarantees

- `agent_start` always precedes any `segment_tool_call` or `segment_tool_result` in a given turn — it is the first event `stream_agent` yields, unconditionally, before entering the tool loop.
- Every `segment_tool_call` is eventually paired with a `segment_tool_result` carrying the same `name`, **or** the turn terminates via `done`/`error` first (a client or server crash mid-tool-call, or the generator raising past `_execute_tool_call`'s own `try/except`, leaves an orphaned `done: false` tool segment in the frontend's in-memory state — `applyStreamDone` does not force-close it, it only force-closes trailing **text** segments).
- `token` events may interleave freely with `segment_tool_call`/`segment_tool_result` pairs — the model can emit narrative text before, between, and after tool calls in the same turn, and each such run becomes its own `{type: 'text', ...}` segment, split wherever a tool call interrupts it.
- **Multiple tool calls within one LLM round never interleave with each other.** If a single completion requests several tools at once (e.g. `pin_study` alongside `get_study_report`), `_execute_tool_call` is invoked once per call, in a strict `for` loop — order-preserved via `sorted(tool_call_map)` by chunk index on the OpenAI path, or plain list order (the order `content_block_stop` events arrived) on the Anthropic path. Call *N*'s `segment_tool_result` is always emitted before call *N+1*'s `segment_tool_call` — there is no concurrent tool execution and no possibility of two open (`done: false`) tool segments coexisting from the same round, which is part of why the call-id-suffixed uniqueness discussed above is sufficient rather than merely convenient.
- `step_start`/`step_done` pairs (context prep and pin/report flows on both paths) always nest correctly — the codebase never emits two consecutive `step_start` events for different `name`s without an intervening `step_done`.
- `done` is always the last event of a successful turn; `error` is always the last event of a failed one. The two are mutually exclusive per turn, and no event of any kind follows either.
- **Forced synthesis can emit `token` events after the last `segment_tool_result` with no intervening tool call.** Both `stream_agent` and `_stream_anthropic_agent` track whether the LLM's final round produced any text (`final_had_synthesis`). If the loop exits — either because `max_iters` was exhausted while the model was still requesting tools, or because the model's very last round was pure tool-calling with no accompanying text — and the last message on the running transcript is a tool result, the code issues one extra, non-streaming-tools completion call solely to obtain closing prose, and re-emits its `token` events on the wire. This means a turn's `token` events are not guaranteed to be contiguous with the round that triggered them; a client cannot assume "no more text is coming" just because the most recent event was `segment_tool_result`. Hitting `max_iters` itself is only logged server-side (`logger.warning("agent hit max_iters=%d without stopping", max_iters)`) — no SSE event reports it, so a turn that silently truncated its own tool-calling loop looks, from the wire, identical to one that finished normally.

---

## Constants referenced in this appendix

All are read from `backend/config.py` and documented fully in [`appendix-d-configuration.md`](appendix-d-configuration.md); listed here as a quick lookup for the numbers this appendix cites inline.

| Constant | Default | Governs |
|---|---|---|
| `SAMPLE_SEARCH_DEFAULT_CANDIDATES` | `40` | Sample-probe candidate cap, `deep_search=False` |
| `SAMPLE_SEARCH_DEEP_CANDIDATES` | `500` | Sample-probe candidate cap, `deep_search=True` |
| `PINNED_STUDIES_PER_CHAT_CAP` (`store/cache.py`) | `10` | `pin_study` and the `get_study_report` auto-pin |
| `max_iters` (`stream_agent` parameter) | `4` (route) / `8` (CLI harness) | Tool-loop round ceiling before forced synthesis |
| `max_tokens` (Anthropic calls only) | `4096` | Hard cap on one completion's output, both main-loop and forced-synthesis calls |
| Tool-count in `TOOL_SCHEMAS` | `5` | Fixed — adding a sixth tool requires touching every file this appendix describes |

---

*See also: [`05-agent.md`](05-agent.md) for how the model is prompted to fill tool arguments and choose between dimensions, and the design rationale behind the one-search invariant and forced synthesis · [`06-streaming-and-chat.md`](06-streaming-and-chat.md) for the full SSE-to-hydration round trip, the dual-authoring hazard, and the segment tri-state · [`04-search.md`](04-search.md) for how `search_studies` and `search_by_sample` bound their underlying queries · [`appendix-d-configuration.md`](appendix-d-configuration.md) for `MODEL_METADATA`, `model_supports_tools`, and the sample-search candidate caps.*