# QiitaExplore — Technical Documentation

This set documents QiitaExplore **as built**: how each part works, why it is shaped that way, and what is known to be wrong with it.

It is written to be accurate rather than flattering. Known defects are documented in the chapter where they live, and indexed in [`11-roadmap.md`](11-roadmap.md).

---

## Where to start

**New contributor** → [`00`](00-orientation.md) orientation · [`01`](01-architecture.md) architecture · [`08`](08-frontend.md) frontend · [`04`](04-search.md) search

Start with orientation even if you are in a hurry. It establishes the two-Qiitas distinction, and nearly every confusing thing in this codebase traces back to it.

**Operator** → [`09`](09-operations.md) operations · [`01`](01-architecture.md) architecture · [`appendix-d`](appendix-d-configuration.md) configuration

**Working on chat or the agent** → [`05`](05-agent.md) agent · [`06`](06-streaming-and-chat.md) streaming · [`appendix-c`](appendix-c-agent-tools-and-sse.md) tools and SSE

Read the dual-authoring section of `06` before changing anything in the streaming path. It is the one contract here that is authored twice and has no test guarding it.

**Platform migration** → [`00`](00-orientation.md) orientation · [`03`](03-data-access-and-caching.md) data access · [`11`](11-roadmap.md) roadmap

**Security review** → [`02`](02-authentication.md) authentication, in full. Its closing section states what the design does and does not defend against, including two open access-control defects.

---

## File map

### Narrative

| File | Covers |
|---|---|
| [`00-orientation.md`](00-orientation.md) | What the system is · **the two Qiitas** · glossary |
| [`01-architecture.md`](01-architecture.md) | Topology · request lifecycle · per-worker state · the three PostgreSQL paths |
| [`02-authentication.md`](02-authentication.md) | Paste-PAT flow · sessions · CSRF · tenancy · legacy claim |
| [`03-data-access-and-caching.md`](03-data-access-and-caching.md) | Reading classic Qiita · three cache layers · context budgeting |
| [`04-search.md`](04-search.md) | Three search paths · the SQL builder · per-study JSONB probes |
| [`05-agent.md`](05-agent.md) | The tool loop · the one-search invariant · the five tools |
| [`06-streaming-and-chat.md`](06-streaming-and-chat.md) | SSE protocol · segments · **the dual-authoring hazard** |
| [`07-merge-and-biom.md`](07-merge-and-biom.md) | Merge workspaces · artifact graphs · jobs · two known defects |
| [`08-frontend.md`](08-frontend.md) | No-build-step React · the mega-hook · routing that isn't |
| [`09-operations.md`](09-operations.md) | Running it · failure modes · capacity |
| [`10-testing.md`](10-testing.md) | Test tiers · parity testing · coverage gaps |
| [`11-roadmap.md`](11-roadmap.md) | Platform migration · two blockers · known-debt index |

### Reference

| File | Covers |
|---|---|
| [`appendix-a-api-reference.md`](appendix-a-api-reference.md) | All 52 endpoints |
| [`appendix-b-sqlite-schema.md`](appendix-b-sqlite-schema.md) | All 16 tables · 11 indexes · migrations |
| [`appendix-c-agent-tools-and-sse.md`](appendix-c-agent-tools-and-sse.md) | 5 tool schemas · 10 SSE events · the `ui_payload` shape |
| [`appendix-d-configuration.md`](appendix-d-configuration.md) | Every environment variable · model roster · tunables |

---

## Relationship to the other docs in this repo

| Document | Still owns | Note |
|---|---|---|
| [`README.md`](../README.md) | Product framing, screenshots, quick start | **Stale on architecture.** Says four agent tools (there are five), predates authentication entirely, and says Flask serves the frontend when nginx does. Where it disagrees with this set, this set is current. |
| [`INSTALL.md`](../INSTALL.md) | First-time setup — conda, pip, config file | [`09-operations.md`](09-operations.md) covers running and diagnosing, never setup. |
| [`CLAUDE.md`](../CLAUDE.md) | Development conventions, the 500-line cap, ticket policy | Also stale in two places: it names `gemma3` as the LLM (the roster is in [`appendix-d`](appendix-d-configuration.md)) and references a `test_data_studies/` directory that no longer exists. |
| [`TICKETS/tickets.md`](../TICKETS/tickets.md) | Full ticket bodies | These docs cite `TKT-0NN` and a one-line impact. Never restated here. |
| `TICKETS/qiita-auth-integration.md` | Design history | **Describes an OIDC proxy design that was never built.** If you are reading it to learn how auth works, read [`02-authentication.md`](02-authentication.md) instead. |

---

## Conventions

**Citations name symbols, not lines.**

```
backend/helpers/agent.py :: stream_agent
frontend/js/app_state.js :: applyStreamDone
backend/nginx.conf (the `proxy_buffering off` block)
```

Paths are relative to `qiita_explore/`. Line numbers are omitted deliberately — they go stale on the first reformat, and a symbol name survives edits that move it.

**Enumerable sets live in exactly one place — an appendix.** Endpoints, tables, tool schemas, SSE events, environment variables. Narrative chapters reference members by name and link to the anchor; they never re-enumerate. Anchors are stable identifiers rather than prose headings: `#search_studies`, `#api_auth_connect`, `#table-auth_sessions`, `#event-segment_tool_call`.

**Current state and future work are kept apart.** Anything not shipped is confined to [`11-roadmap.md`](11-roadmap.md) or marked in place. Stubs are described as stubs — `compute_diversity` is in the live tool schema and returns an apology, and this set says so rather than describing what it will one day compute.

**Numbers are measured or absent.** The only performance figures cited are TKT-024's benchmarked 86 ms – 13.5 s range and the outputs of `backend/tests/benchmarks/`. Nothing is estimated.

**Uncertainty is marked.** Where something was not verified — the nginx gzip/SSE interaction in [`09`](09-operations.md) is the current example — it carries an explicit *Unverified* callout rather than a confident sentence.

---

## Maintaining this

The claims most likely to rot, and how to check them:

| Claim | Check |
|---|---|
| 52 endpoints, split by module | `grep -c "@app.route" backend/routes/*.py` |
| 16 tables, 11 indexes | `CREATE TABLE` / `CREATE INDEX` in `backend/store/db.py` |
| 5 tools, 10 SSE events | `TOOL_SCHEMAS` in `agent_tools.py`; `_sse(` call sites vs. `parseSSE`'s dispatch table |
| 10 models | `ALLOWED_MODELS` in `config.py` |
| 2 remaining `TRN` call sites | `grep -rn "TRN" --include="*.py" backend/` — most hits are docstrings explaining avoidance |
| Every `path :: symbol` citation resolves | Grep each symbol name in the named file |

If you change the SSE segment contract, [`06-streaming-and-chat.md`](06-streaming-and-chat.md) carries a five-item checklist of everything that must change with it.
