# Graph Report - .  (2026-08-16)

## Corpus Check
- 161 files · ~243,561 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1608 nodes · 3022 edges · 101 communities (94 shown, 7 thin omitted)
- Extraction: 95% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 134 edges (avg confidence: 0.75)
- Token cost: 0 input · 530,657 output

## Community Hubs (Navigation)
- BIOM Auto-Pick & Merge Routes
- Qiita Core Config & Exceptions
- E2E Auth Client Tests
- Qiita DB SQL Transaction
- Project & Chat CRUD Store
- Platform Auth Integration Memory
- Pinned Context Budget Tests
- Qiita CLI Script Commands
- Ticket Backlog & Constraints
- BIOM Merge Execution Tests
- Qiita WhoAmI Auth Tests
- Artifact Graph & Routes
- Chat Persistence Tests
- Sample Search & SQL Layer
- Session Auth Store & Middleware
- Agentic Chat Tool Execution
- Study Browse Routes
- Search Relevance Scoring
- Project Scope Authorization Tests
- Chat SSE Request Utils
- Search Visibility Parity Tests
- Agent Harness CLI Tool
- Project Routes API
- Agent Tooling Ticket Notes
- Global Chat Messaging E2E
- Agentic Tool-Calling Loop
- Merge Workspace CRUD Store
- Pinning E2E Journey Tests
- Project CRUD Unit Tests
- Pin Command Store Tests
- Postgres Connection Pool
- Auth Routes & Qiita Client
- LLM Query Planning
- Agent Tool Call Unit Tests
- Frontend UI Components
- Frontend API Utils
- LLM Context & Qiita Fetch
- Legacy PAT Claim Flow
- SQLite Schema Integrity Tests
- Qiita-Env CLI Script
- Changelog & Feature Notes
- Pin & Ack SSE Flow
- PI Relevance Filter Tests
- Blocked Study Visibility Tests
- Chat/Search Consistency Tests
- Project Architecture Guidance
- Project Studies Unit Tests
- Chat Routes & Model Config
- Backend Test Fixtures
- Deep Search Parity Tests
- Project Journey E2E Tests
- Agent Chat UI Screenshot
- Project Chat E2E Tests
- Auth Client Fixture Tests
- WhoAmI Mock Response Tests
- Qiita Cron Job Script
- Merge Card UI Screenshot
- Merge Artifacts Frontend View
- Dashboard UI Screenshot
- Multi-Agent Team Roles
- Merge Detail Frontend View
- Pinned Study Cache Store
- Frontend Loading Animations
- Frontend Icon Components
- CI Workflow Config
- Full Test Suite Runner
- Merge Provenance Tree UI
- Job Recovery CLI Script
- SQLite Schema Bootstrap
- Global Chat CRUD E2E
- URL Hash Sync Hook
- Agent Harness Runner Script
- Chat Width Layout Plan
- Backend Test Runner Script
- Cache Hit Rate Benchmark
- Concurrent Load Benchmark
- Slurm Plugin Launcher Script
- Private Plugin Executor Script
- Project Study ID Helpers
- Detail Cache Benchmark
- Barnacle Startup Script
- Chat Composer & Model Selector
- Search Latency Benchmark
- All-Studies Cron Script
- Qiita Logo (Dark)
- Qiita Logo (Light)
- Qiita Pointer Logo

## God Nodes (most connected - your core abstractions)
1. `_conn()` - 65 edges
2. `Active Tickets` - 51 edges
3. `_now()` - 33 edges
4. `_as_dict()` - 26 edges
5. `_resolve_user()` - 25 edges
6. `Transaction` - 23 edges
7. `get_project()` - 20 edges
8. `agent-tooling Agent` - 20 edges
9. `stream_chat()` - 19 edges
10. `get_study_detail_cache()` - 18 edges

## Surprising Connections (you probably didn't know these)
- `Per-Card "Pin" Button` --conceptually_related_to--> `pin_study_to_chat()`  [INFERRED]
  assets/agent-chat.png → qiita_explore/backend/store/cache.py
- `TKT-044: Merge Can Silently Substitute a Different Artifact` --references--> `_resolve_artifact()`  [EXTRACTED]
  TICKETS/tickets.md → qiita_explore/backend/helpers/merge_helpers.py
- `TKT-048: Project Chat Derives Two Budgets From the Same Context Window` --references--> `_pinned_per_study_budget()`  [EXTRACTED]
  TICKETS/tickets.md → qiita_explore/backend/helpers/pinned_context.py
- `10 Study Result Cards (ID, title, data-type tags, sample count, PI)` --conceptually_related_to--> `search_studies_by_sample_meta()`  [INFERRED]
  assets/agent-chat.png → qiita_explore/backend/helpers/sample_search.py
- `TKT-016: Parallelize Header Enrichment + Reuse Connection Pool` --references--> `search_studies_by_sample_meta()`  [EXTRACTED]
  TICKETS/tickets.md → qiita_explore/backend/helpers/sample_search.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Shared Persistent Agent Memory System Pattern** — claude_agents_planner, claude_agents_reviewer, claude_agents_swe, claude_agents_tester, claude_agents_team_lead [EXTRACTED 1.00]
- **Qiita Platform Integration Knowledge Cluster** — claude_agents_qiita_platform_expert, claude_agent_memory_qiita_platform_expert_memory, claude_agent_memory_qiita_platform_expert_auth_no_impersonation_no_study_list, claude_agent_memory_qiita_platform_expert_dev_auth_without_authrocket, claude_agent_memory_qiita_platform_expert_miint_naming, claude_agent_memory_qiita_platform_expert_oidc_pat_flow_gotchas [EXTRACTED 1.00]
- **500-Line File Split Backlog** — tickets_tickets_tkt_011, tickets_tickets_tkt_013, tickets_tickets_tkt_014, tickets_tickets_tkt_036, tickets_tickets_tkt_037, tickets_tickets_tkt_038, tickets_tickets_tkt_039, tickets_tickets_tkt_040 [EXTRACTED 1.00]
- **Agentic study-search pipeline (query -> tool call -> SQL -> results)** — assets_agent_chat_ibs_query, assets_agent_chat_shotgun_followup_query, assets_agent_chat_deep_search_tool_call, assets_agent_chat_sql_query_expander, assets_agent_chat_study_result_cards [INFERRED 0.85]
- **Study result card actions (Pin / Merge)** — assets_agent_chat_study_result_cards, assets_agent_chat_pin_button, assets_agent_chat_merge_button [EXTRACTED 1.00]
- **Workspace / global chat sidebar navigation** — assets_agent_chat_qiitaexplorer_ui, assets_agent_chat_workspace_irritable_bowel, assets_agent_chat_workspace_gut_microbiome, assets_agent_chat_global_chats_section [EXTRACTED 1.00]
- **Study Card Interaction Pattern (browse, badge, pin, merge)** — concept_browse_studies_view, concept_gold_studies_badge, concept_pin_study_feature, concept_merge_study_feature [INFERRED 0.85]
- **Chat Navigation and Composer Entry Points** — concept_global_chats_sidebar, concept_workspaces_sidebar, concept_chat_composer, concept_model_selector [INFERRED 0.75]
- **Study Merge Workflow** — assets_merge_workspace_image, prep_compatibility_check, merge_queue, qiita_explore_frontend_js_merge_workspace_module [INFERRED 0.75]

## Communities (101 total, 7 thin omitted)

### Community 0 - "BIOM Auto-Pick & Merge Routes"
Cohesion: 0.09
Nodes (46): autopick_artifact(), autopick_reason(), check_namespace_compatibility(), _namespace(), Auto-pick BIOM artifact per study and validate namespace compatibility for…, Return the best BIOM artifact for merging given a study's artifact list.…, Return canonical namespace common to all studies, or '' if no intersection.…, Return a human-readable explanation for why this artifact was auto-picked. (+38 more)

### Community 1 - "Qiita Core Config & Exceptions"
Cohesion: 0.06
Nodes (34): ConfigParser_Error, ConfigurationManager, object, Get the configuration of the main section, Holds the QIITA configuration Parameters ---------- conf_fp: str, optional…, Get the configuration of the job_scheduler section, Get the configuration of the postgres section, Get the configuration of the redis section (+26 more)

### Community 2 - "E2E Auth Client Tests"
Cohesion: 0.07
Nodes (27): AuthedClient, AuthError, Authenticated HTTP client for e2e tests. Every non-auth endpoint is default-…, Login or an authenticated call failed outright., POST expecting an SSE stream response; caller iterates resp.iter_lines()., backend(), client(), fresh_db() (+19 more)

### Community 3 - "Qiita DB SQL Transaction"
Cohesion: 0.07
Nodes (24): _checker(), create_new_transaction(), object, Returns a postgres cursor Returns ------- psycopg2.cursor The psycopg2 cursor…, Rollbacks the current transaction and raises a useful error The error message…, Add a sql query to the transaction Parameters ---------- sql : str The sql…, Internal function that actually executes the transaction The `execute` function…, Executes the transaction Returns ------- list of DictCursor The results of all… (+16 more)

### Community 4 - "Project & Chat CRUD Store"
Cohesion: 0.16
Nodes (42): _load_pinned_study_meta(), Pinned studies for a chat, with the denormalized title. The single read of this…, upsert_project_study_summary(), add_study_to_project(), append_chat_messages(), create_chat(), create_project(), _decode_ui() (+34 more)

### Community 5 - "Platform Auth Integration Memory"
Cohesion: 0.07
Nodes (44): Auth: No Impersonation, No Study List, fetch_caller_study_access, qiita-web Multi-User Auth Integration Design, No List-Studies Endpoint, No Principal-Impersonation Surface, require_study_access guard, Dev Auth Without AuthRocket, Bootstrap Chicken-and-Egg Admin Problem (+36 more)

### Community 6 - "Pinned Context Budget Tests"
Cohesion: 0.07
Nodes (19): _header(), pinned(), fixture, qfetch(), Pinned-study context budgeting, and the stub fallback in the discovery…, The pinned path calls _get_or_fetch_full_samples 5x per turn, so a cache that…, The regression: sufficiency compared len(samples) against num_samples, which…, The old code fetched a hardcoded 200 rows, so the char budget could never be… (+11 more)

### Community 7 - "Qiita CLI Script Commands"
Cohesion: 0.08
Nodes (32): argument, clear_sysmessage(), command, db(), ebi(), group, load_artifact(), load_prep_template() (+24 more)

### Community 8 - "Ticket Backlog & Constraints"
Cohesion: 0.06
Nodes (41): 500-Line File Cap Hard Constraint, Active Tickets, TKT-002: Silent Exception Handling Makes Debugging Difficult, TKT-003: Undefined Variables on Qiita Fetch Failure (Resolved), TKT-004: Race Condition in SSE Response (pin after done), TKT-006: Pin Studies in Chat Bar + Enter to Start Global Chat (Resolved), TKT-011: Split Oversized Files (500-line cap), TKT-012: Merge Page Request Fan-outs (+33 more)

### Community 9 - "BIOM Merge Execution Tests"
Cohesion: 0.08
Nodes (25): Path, Execute a BIOM merge job locally via subprocess (dev mode). TODO (before…, Fetch sample metadata for each study and write a combined TSV. Returns True if…, Blocking function — meant to run inside a ThreadPoolExecutor worker.…, run_merge_job(), _write_merged_sample_metadata(), _fetch_full_sample_metadata(), Return sample metadata rows as [{sample_id, fields}] capped to limit. (+17 more)

### Community 10 - "Qiita WhoAmI Auth Tests"
Cohesion: 0.10
Nodes (11): Outcome of a whoami call. `transient_error=True` means the call failed for a…, WhoAmIResult, _connect(), _human(), mock_whoami(), A client-supplied user_id must never override the session identity., Patch routes.auth_routes.whoami to a controllable fake, keyed by token string…, Connect a fresh session for a synthetic principal; returns (headers, user_id). (+3 more)

### Community 11 - "Artifact Graph & Routes"
Cohesion: 0.10
Nodes (28): _abs(), _build(), fetch_artifact_graph(), Build a flat artifact+job processing graph for a study from the Qiita DB. Uses…, Flat list of artifact+job nodes for a study's processing graph., get_biom_sample_ids(), Per-BIOM sample membership — read from HDF5 and cache forever (artifacts are…, Return sample IDs from a BIOM file. Uses h5py direct read; falls back to… (+20 more)

### Community 12 - "Chat Persistence Tests"
Cohesion: 0.06
Nodes (18): Tests for chat functionality., Messages remain after re-fetching chat., Regression: pins vanished on restart because nothing asserted that a re-fetched…, List all chats in a project., Get chat with message history., Test global chat functionality., Test project chat CRUD., Create a new global chat. (+10 more)

### Community 13 - "Sample Search & SQL Layer"
Cohesion: 0.09
Nodes (28): perform_as_transaction(), r""" SQL Connection object (:mod:`qiita_db.sql_connection`)…, Opens, adds and executes sql as a single transaction Parameters ---------- sql…, _get_candidate_ids(), _hydrate_headers(), _parallel_probe(), _probe_exists(), _probe_fields_raw() (+20 more)

### Community 14 - "Session Auth Store & Middleware"
Cohesion: 0.15
Nodes (17): datetime, Default-deny auth guard: resolves the session cookie to g.user_id and rejects…, register_auth_middleware(), create_session(), get_session_by_token(), _hash_token(), _parse_iso(), User + session CRUD for Qiita-identity (paste-PAT) authentication. Sessions are… (+9 more)

### Community 15 - "Agentic Chat Tool Execution"
Cohesion: 0.16
Nodes (28): _allowed_project_study_ids(), _collect_terms(), _empty_input_result(), _execute_global_tool(), _execute_project_tool(), execute_tool(), Tool registry and execution dispatch for the agentic chat loop., Flatten study dicts into the {study_id, study_title, pi_name, num_samples,… (+20 more)

### Community 16 - "Study Browse Routes"
Cohesion: 0.12
Nodes (28): _fetch_prep_metadata_summary(), _fetch_study_samples(), first_studies(), is_study_public(), Return deterministic first studies by study_id from PostgreSQL., Return True only if the study has at least one public artifact., Return sample list for a study using dynamic sample_{study_id} table., Return one row of sequencing metadata for a prep template. (+20 more)

### Community 17 - "Search Relevance Scoring"
Cohesion: 0.11
Nodes (18): apply_pi_veto(), compute_total_relevance(), finalize_search_results(), pi_detail_suffix(), Unified relevance scoring and PI resolution for study search., Short PI clause for tool detail / UI banners., Drop studies not matching resolved PIs when veto is active., Score sample metadata layer, sort by relevance, apply PI veto, optional trim. (+10 more)

### Community 18 - "Project Scope Authorization Tests"
Cohesion: 0.09
Nodes (11): agent_tools(), cache(), _collect(), fixture, Unit tests for project-scoped chat authorization and tools., Direct DB insert of a pin for a non-member study must not surface., TestAgentDedup, TestPinValidationProjectScope (+3 more)

### Community 19 - "Chat SSE Request Utils"
Cohesion: 0.16
Nodes (23): build_full_msgs(), parse_chat_stream_body(), pin_response(), pinned_payload(), Shared request-parsing and SSE-response helpers for the chat stream routes., Load a study's full sample report, yielding SSE step/ui events. Returns…, Parse the common fields of a chat-stream POST body. Returns (user_content,…, The pinned-studies envelope every pin/unpin response carries. One read of… (+15 more)

### Community 20 - "Search Visibility Parity Tests"
Cohesion: 0.12
Nodes (15): POST /api/search and return set of study_id ints from results., search_ids(), 1.2 — /api/search never surfaces non-public studies regardless of query., TestSearchNeverReturnsBlocked, e2e, parametrize, Structural parity tests — HTTP, no LLM. Verify that both the chat planner path…, 3.1 — /api/studies/<id>/detail proves non-public studies are blocked at the… (+7 more)

### Community 21 - "Agent Harness CLI Tool"
Cohesion: 0.16
Nodes (18): bold(), _c(), cyan(), dim(), green(), _LogTee, magenta(), main() (+10 more)

### Community 22 - "Project Routes API"
Cohesion: 0.16
Nodes (21): _get_or_fetch_full_samples(), Return cached full sample rows for a study, falling back to a Qiita fetch +…, api_add_study(), api_create_project(), api_delete_project(), api_enrich_all_studies(), api_get_project(), api_list_projects() (+13 more)

### Community 23 - "Agent Tooling Ticket Notes"
Cohesion: 0.11
Nodes (21): agent-tooling Agent, Frontend Segments Model, Agentic SSE Event Contract, Status Visibility Hard Requirement, Tool/Function Status Visibility Requirement, model_supports_tools(), AgentMessageBubble(), SamplesReportBubble() (+13 more)

### Community 24 - "Global Chat Messaging E2E"
Cohesion: 0.13
Nodes (13): POST a message to a chat and consume the SSE stream. Global chat by default;…, stream_chat(), e2e_llm, TestGlobalChatMessaging, e2e, e2e_llm, E2E tests verifying that studies actually reach the LLM's context. There is no…, Structured signals that a pinned study entered the prompt. (+5 more)

### Community 25 - "Agentic Tool-Calling Loop"
Cohesion: 0.19
Nodes (19): get_client(), Return (client, provider_str) for the given model., _execute_tool_call(), _is_dedup_search_tool(), _openai_tools_to_anthropic(), Streaming tool-calling loop for the agentic chatbot., Streaming agentic loop. Yields typed dicts for the route to forward as SSE:…, Human-readable step label for a tool call while it runs. (+11 more)

### Community 26 - "Merge Workspace CRUD Store"
Cohesion: 0.19
Nodes (19): purge_expired_sessions(), Hard-delete sessions past absolute expiry (revoked rows are kept for audit)., _now(), add_study_to_workspace(), create_merge_job(), create_workspace(), _hydrate_study(), CRUD operations for merge_workspaces, merge_workspace_studies, and merge_jobs. (+11 more)

### Community 27 - "Pinning E2E Journey Tests"
Cohesion: 0.20
Nodes (9): _add_study_to_project(), _get_chat(), _pin(), e2e, E2E tests for pinning studies to a chat — both the global-chat and project-chat…, Public study not saved in the project must be rejected., TestPinningGlobalScope, TestPinningProjectScope (+1 more)

### Community 28 - "Project CRUD Unit Tests"
Cohesion: 0.10
Nodes (10): Tests for CRUD operations on projects., List projects for new user returns empty list., List projects returns created project., Get a single project by ID., Get non-existent project returns None., Test project create, read, update, delete., Projects are isolated by user_id., Project includes study count via studies_count field. (+2 more)

### Community 29 - "Pin Command Store Tests"
Cohesion: 0.12
Nodes (9): cache(), fixture, Unit tests for /pin store-layer: pin_study_to_chat, unpin, list, scope…, Rows pinned before study_title existed must still load — the chip falls back to…, A pin that doesn't land must not answer 200 {'ok': True} — that silence is what…, Response contract only. The meta round-trip through SQLite is covered by…, request_utils(), test_pin_without_title_falls_back_to_null() (+1 more)

### Community 30 - "Postgres Connection Pool"
Cohesion: 0.12
Nodes (13): get_pool(), pooled_fetchall(), Shared, thread-safe Postgres connection pool for read-only Qiita queries.…, Lazily create the module-level connection pool (fork-safe: built on first use,…, Run a single read-only SELECT on a dedicated pooled connection; return all rows., build_relevance_score(), build_where_from_plan(), detect_data_types() (+5 more)

### Community 31 - "Auth Routes & Qiita Client"
Cohesion: 0.19
Nodes (17): Synchronous client for the Qiita control-plane's auth/whoami endpoint. Mirrors…, Call GET /api/v1/auth/whoami with `pat` as a bearer token. Returns: - ok=True,…, whoami(), api_auth_connect(), api_auth_legacy_default(), api_auth_login_url(), api_auth_logout(), api_auth_me() (+9 more)

### Community 32 - "LLM Query Planning"
Cohesion: 0.15
Nodes (13): browse_query_to_sql(), _extract_pi_text(), _keyword_clause_sql(), _keyword_informativeness(), _pick_keywords(), Higher score → more useful as a search term., Keep the most informative keywords, preserving input order among ties., Return PI name text from common browse-box patterns, or None. (+5 more)

### Community 33 - "Agent Tool Call Unit Tests"
Cohesion: 0.16
Nodes (13): _collect(), _make_result(), fixture, Unit tests for helpers.agent._execute_tool_call(). Drives the generator…, is_search_studies is True only when name=='search_studies'., is_search_studies is False for any tool other than search_studies., Drive a generator to completion; return (events, return_value)., When execute_tool raises, yields failure segment_tool_result; returns… (+5 more)

### Community 34 - "Frontend UI Components"
Cohesion: 0.11
Nodes (6): _CLAUDE_MODELS, FIELD_GROUPS, _META_TYPES, _NRP_MODELS, _PLUS_ACTIONS, SLASH_COMMANDS

### Community 35 - "Frontend API Utils"
Cohesion: 0.13
Nodes (10): apiDel(), apiFetch(), apiJson(), apiPatch(), apiPost(), fetchStudyDetail(), TODO: revert fallback to port 5001 before committing to master, _STATE_CHANGING_METHODS (+2 more)

### Community 36 - "LLM Context & Qiita Fetch"
Cohesion: 0.18
Nodes (16): _build_project_study_context(), _normalize_messages(), LLM context builders, SSE formatter, and streaming wrappers., One study, minimal lines for global discovery (no sample metadata dump)., _study_detail_block(), _study_discovery_compact_block(), _truncate(), _build_samples_context_text() (+8 more)

### Community 37 - "Legacy PAT Claim Flow"
Cohesion: 0.17
Nodes (13): api_auth_claim_default(), _already_claimed(), claim_eligible(), claim_legacy_default(), legacy_default_counts(), LegacyClaimConflict, Exception, Atomic one-time claim of legacy 'default'-owned rows to a configured Qiita… (+5 more)

### Community 38 - "SQLite Schema Integrity Tests"
Cohesion: 0.11
Nodes (11): Tests for SQLite schema and data integrity., All expected indexes exist., Foreign key enforcement is enabled., Test data integrity constraints., Deleting project cascades to studies and chats., Verify database schema is correctly created., Same study cannot be added twice to same project., Timestamps are stored in UTC ISO format. (+3 more)

### Community 39 - "Qiita-Env CLI Script"
Cohesion: 0.16
Nodes (17): add_portal(), argument, clean_test(), command, drop(), env(), group, make() (+9 more)

### Community 40 - "Changelog & Feature Notes"
Cohesion: 0.12
Nodes (17): Changes — Qiita Explorer (March 17 2026), <meta name="api-base"> Configurable API URL Pattern, Barnacle Integration — Configurable API Base + Startup Script, Git Branch Reconciliation (rebase), GET /api/studies/<id>/samples/<sample_id> Endpoint, Sample Preview Card Feature, Changes — Qiita Explorer UI (March 2026), POST /api/projects/<id>/studies/enrich-all Endpoint (+9 more)

### Community 41 - "Pin & Ack SSE Flow"
Cohesion: 0.18
Nodes (15): _sse(), Shared pin-and-acknowledge SSE flow for project and global chat streams., Validate + pin studies, generate an LLM ack, persist it, and yield SSE events.…, stream_pin_flow(), _build_pinned_reports_context(), _pinned_manifest_line(), _pinned_per_study_budget(), Pinned-study context assembly — split out of helpers/qiita_fetch.py to keep… (+7 more)

### Community 42 - "PI Relevance Filter Tests"
Cohesion: 0.18
Nodes (9): prepare_pi_filter(), Return (pi_texts, resolved, veto_applied, applied_filters_pi dict)., Look up PI persons in qiita.study_person by name/affiliation ILIKE., resolve_pi(), TestBrowseQueryPiGating, TestPreparePiFilter, TestResolvePi, patch() (+1 more)

### Community 43 - "Blocked Study Visibility Tests"
Cohesion: 0.17
Nodes (12): e2e, e2e_llm, parametrize, Tests that non-public studies are unreachable through every channel. Extend…, 1.6 — get_study_report and pin_study tool calls reject non-public studies., 1.1 — /api/studies/<id>/detail returns 404 for non-public studies., 1.3 — /api/studies/first never surfaces non-public studies., 1.4 — Chat does not return data for non-public studies when asked directly. (+4 more)

### Community 44 - "Chat/Search Consistency Tests"
Cohesion: 0.18
Nodes (12): e2e, e2e_llm, parametrize, Tests that discoverable studies surface through both /api/search and chat.…, 2.1 — /api/search surfaces the expected study for the given query., 2.2 — Chat surfaces the expected study for the given query., 2.3 — Both frontend search and chat search step agree on returning the expected…, All required study IDs must appear in the LLM output for the query. (+4 more)

### Community 45 - "Project Architecture Guidance"
Cohesion: 0.14
Nodes (16): CLAUDE.md Project Instructions, Architecture: Frontend (React/Babel standalone), Architecture: LLM via NRP-Nautilus, Architecture: Local SQLite DB, Architecture: Qiita PostgreSQL (read-only via TRN), Dev Port 5002 Convention, Rationale: Dev Port Must Revert Before Master, Behavioral Guideline: Goal-Driven Execution (+8 more)

### Community 46 - "Project Studies Unit Tests"
Cohesion: 0.12
Nodes (9): Tests for project studies functionality., Remove a study from a project., Adding same study twice doesn't create duplicates., Add multiple different studies to a project., Test adding/removing studies from projects., Study remains after re-fetching project., Removing non-existent study returns project unchanged., Add a study to a project. (+1 more)

### Community 47 - "Chat Routes & Model Config"
Cohesion: 0.20
Nodes (11): context_budget_chars(), OpenAI function-calling schemas for agent tools., friendly_llm_error(), _detect_mentioned_study_ids(), Return project study IDs explicitly mentioned in user_content. Matches 'study…, api_create_chat(), api_delete_chat(), api_get_chat() (+3 more)

### Community 48 - "Backend Test Fixtures"
Cohesion: 0.19
Nodes (14): crud(), db_conn(), fresh_db(), global_chat_crud(), fixture, Pytest fixtures for qiita_explore tests., qiita_db/qiita_core are vendored classic-Qiita packages this sandbox can't…, Each test gets a fresh temporary database. Also installs the… (+6 more)

### Community 49 - "Deep Search Parity Tests"
Cohesion: 0.17
Nodes (10): llm_judge(), Shared helper functions for e2e parity tests. All HTTP calls go through an…, Ask an LLM to evaluate whether the assistant's answer meets the rubric.…, e2e, e2e_llm, E2E tests for the /deepsearch command. Requires a running barnacle backend:…, Single-turn /deepsearch for the American Gut Project., Two-turn /deepsearch: wild mice then filter to shotgun metagenomics. Both turns… (+2 more)

### Community 50 - "Project Journey E2E Tests"
Cohesion: 0.14
Nodes (6): e2e, E2E tests for the project journey: create a project, add a study to it (no-…, The no-refresh pattern: add/remove-study responses ARE the full updated project…, num_samples/data_types are filled asynchronously after add (a background thread…, TestProjectLifecycle, TestStudiesInProject

### Community 51 - "Agent Chat UI Screenshot"
Cohesion: 0.15
Nodes (14): "Deep-searched Qiita database" Tool Call Card (14.2s, 81 sample-metadata hits from <=500 studies), Global Chats Sidebar Section, User Query: "Find me 10 studies related to the irritable bowel syndrome", Agent Chat Screenshot, QiitaExplorer Chat UI, Follow-up User Message: "Let's look at shotgun specifically", Collapsible "SQL query" Detail Row, "Gut microbiome" Workspace (sidebar) (+6 more)

### Community 52 - "Project Chat E2E Tests"
Cohesion: 0.18
Nodes (7): e2e, e2e_llm, E2E tests for project chats: creating a chat with a first message runs a…, `/report` with a study not in the project streams refusal text, no ui event., TestProjectChatCrud, TestProjectChatMessaging, TestProjectChatReportGate

### Community 53 - "Auth Client Fixture Tests"
Cohesion: 0.18
Nodes (10): api_client(), _app(), auth_db_path(), fixture, Tests for the paste-PAT Qiita authentication system: sessions, legacy-claim,…, Import run.app once per test module — expensive (imports every route module)…, Fresh test client (empty cookie jar) per test — the shared _app import must…, A deployed DB can carry a stale pre-auth `users` table (see… (+2 more)

### Community 54 - "WhoAmI Mock Response Tests"
Cohesion: 0.22
Nodes (4): _FakeHttpxResponse, parametrize, Only a 401 (or a 200 with a non-human kind) definitively says the PAT is bad.…, TestQiitaClientWhoami

### Community 55 - "Qiita Cron Job Script"
Cohesion: 0.26
Nodes (12): command, commands(), empty_trash_upload_folder(), generate_biom_and_metadata_release(), generate_plugin_releases(), group, option, purge_filepaths() (+4 more)

### Community 56 - "Merge Card UI Screenshot"
Cohesion: 0.18
Nodes (12): Per-Card / Header "Merge" Button, Per-Card "Pin" Button, 10 Study Result Cards (ID, title, data-type tags, sample count, PI), Merge Workspace Screenshot, Merge Workspace UI Feature, Merge and BIOM Documentation, Merge Queue, Prep Compatibility Check (+4 more)

### Community 57 - "Merge Artifacts Frontend View"
Cohesion: 0.23
Nodes (5): ArtifactOutputsView(), BiomCard(), buildPipeline(), filterGraphByPrep(), prepReachableSet()

### Community 58 - "Dashboard UI Screenshot"
Cohesion: 0.27
Nodes (11): QiitaExplorer Dashboard Screenshot, Browse Studies View, Chat Composer Input Bar, Deep Search Toggle, Global Chats Sidebar, GOLD Studies Badge, Merge Study Action, Merges Tab (+3 more)

### Community 59 - "Multi-Agent Team Roles"
Cohesion: 0.31
Nodes (11): Planner Agent, Planner Plan Mode, Reviewer Agent, Reviewer Severity Levels (BLOCKING/SUGGESTION/NITPICK/UNCLEAR), SWE Agent, SWE [PLAN MODE] Flag, team-lead Orchestrator Agent, [ESCALATE] Keyword Interrupt (+3 more)

### Community 60 - "Merge Detail Frontend View"
Cohesion: 0.22
Nodes (4): BLANK, deriveCols(), SamplePeek(), StudySampleTable()

### Community 61 - "Pinned Study Cache Store"
Cohesion: 0.29
Nodes (9): get_biom_sample_cache(), list_pinned_studies(), _load_pinned_studies(), _normalize_scope(), pin_study_to_chat(), Study caching, project summaries, and pinned-study management., Attach a study to a chat. Caps at PINNED_STUDIES_PER_CHAT_CAP. `study_title` is…, unpin_study_from_chat() (+1 more)

### Community 62 - "Frontend Loading Animations"
Cohesion: 0.51
Nodes (9): _drawDuplex(), HelixLoader(), InfinityLoader(), _LP, _lRgba(), _makeSegs(), _ringCrossings(), _setupCanvas() (+1 more)

### Community 64 - "CI Workflow Config"
Cohesion: 0.29
Nodes (8): Qiita CI Workflow, coveralls_finish job, lint job (ruff), main test job (qiita_db / qiita_pet+core+ware matrix), Postgres 13.4 Service Container, redbiom + webdis Setup Steps, TKT-007: Refactor Away from qiita_db.TRN / qiita_core, TKT-009: DuckDB / MIINT (needs scoping)

### Community 65 - "Full Test Suite Runner"
Cohesion: 0.43
Nodes (6): die(), E2E_RUN_ID, fail(), pass(), run_full_suite.sh script, warn()

### Community 67 - "Job Recovery CLI Script"
Cohesion: 0.67
Nodes (6): _flush_queues(), _get_jids_to_recover(), _qiita_queue_log_parse(), qiita_recover_jobs(), _retrieve_queue_jobs(), _submit_jobs()

### Community 68 - "SQLite Schema Bootstrap"
Cohesion: 0.47
Nodes (5): _bootstrap(), _create_schema(), SQLite schema creation, migration helpers, and core connection utilities., Migrate a stale pre-auth `users` table left behind by an older, pre-…, _reconcile_legacy_users_table()

### Community 69 - "Global Chat CRUD E2E"
Cohesion: 0.33
Nodes (3): e2e, E2E tests for the global chat creation + messaging journey: create a chat, send…, TestGlobalChatCrud

### Community 70 - "URL Hash Sync Hook"
Cohesion: 0.60
Nodes (5): buildHash(), hashToView(), parseHash(), useUrlSync(), viewToPath()

### Community 72 - "Agent Harness Runner Script"
Cohesion: 0.33
Nodes (5): AGENT_DEBUG, HARNESS_LOG_FP, QIITA_CONFIG_FP, QIITA_EXPERIMENT_DB_PATH, run_agent_harness.sh script

### Community 73 - "Chat Width Layout Plan"
Cohesion: 0.50
Nodes (5): Unify Chat/Composer Width Plan, --chat-max-w CSS Custom Property (860px), .chat-messages-wide / .composer-wide Classes, 1040px Responsive Breakpoint, TKT-037: Split app_render.js (601 → ~200 lines)

### Community 74 - "Backend Test Runner Script"
Cohesion: 0.60
Nodes (3): fail(), pass(), run_tests.sh script

### Community 75 - "Cache Hit Rate Benchmark"
Cohesion: 0.50
Nodes (4): fetch_detail(), Benchmark 4: Cache hit rate Simulates a realistic researcher session — some…, Return (ok, cached) using the endpoint's real 'cached' field — not a guess., run()

### Community 76 - "Concurrent Load Benchmark"
Cohesion: 0.60
Nodes (4): Benchmark 3: Concurrent user load Simulates N researchers sending search…, run(), run_at_concurrency(), single_search()

### Community 80 - "Slurm Plugin Launcher Script"
Cohesion: 0.40
Nodes (4): argument, command, Starts the plugin environment, start()

### Community 81 - "Private Plugin Executor Script"
Cohesion: 0.40
Nodes (4): argument, command, execute(), Executes the task given by job_id The parameters url and output_dir are…

### Community 82 - "Project Study ID Helpers"
Cohesion: 0.50
Nodes (4): allowed_project_study_ids(), get_project_studies_only(), Lightweight fetch — returns only the studies list, skipping chats and messages., Normalized set of study IDs currently in a project.

### Community 83 - "Detail Cache Benchmark"
Cohesion: 0.67
Nodes (3): hit(), Benchmark 2: Cache hit speedup Hits /api/studies/<id>/detail cold (first…, run()

### Community 84 - "Barnacle Startup Script"
Cohesion: 0.50
Nodes (3): QIITA_CONFIG_FP, QIITA_EXPERIMENT_DB_PATH, start_barnacle.sh script

### Community 85 - "Chat Composer & Model Selector"
Cohesion: 0.67
Nodes (3): Chat Composer ("use '/' for commands, ask or search for studies"), Model Selector Showing "minimax-m2", MODEL_METADATA (LLM model registry incl. minimax-m2)

## Ambiguous Edges - Review These
- `qiita-platform-expert Memory Index` → `qiita-platform-expert Agent`  [AMBIGUOUS]
  .claude/agents/qiita-platform-expert.md · relation: references
- `No Principal-Impersonation Surface` → `qiita-web Multi-User Auth Integration Design`  [AMBIGUOUS]
  .claude/agent-memory/qiita-platform-expert/auth_no_impersonation_no_study_list.md · relation: rationale_for

## Knowledge Gaps
- **84 isolated node(s):** `FIELD_GROUPS`, `_META_TYPES`, `SLASH_COMMANDS`, `_PLUS_ACTIONS`, `_NRP_MODELS` (+79 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `qiita-platform-expert Memory Index` and `qiita-platform-expert Agent`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **What is the exact relationship between `No Principal-Impersonation Surface` and `qiita-web Multi-User Auth Integration Design`?**
  _Edge tagged AMBIGUOUS (relation: rationale_for) - confidence is low._
- **Why does `agent-tooling Agent` connect `Agent Tooling Ticket Notes` to `Chat Width Layout Plan`, `Sample Search & SQL Layer`, `Chat Routes & Model Config`, `Agent Chat UI Screenshot`, `Merge Card UI Screenshot`, `Agentic Tool-Calling Loop`?**
  _High betweenness centrality (0.076) - this node is a cross-community bridge._
- **Why does `search_studies_by_sample_meta()` connect `Sample Search & SQL Layer` to `Ticket Backlog & Constraints`, `Agentic Chat Tool Execution`, `Study Browse Routes`, `Agent Tooling Ticket Notes`, `Merge Card UI Screenshot`?**
  _High betweenness centrality (0.073) - this node is a cross-community bridge._
- **Why does `Active Tickets` connect `Ticket Backlog & Constraints` to `CI Workflow Config`, `Chat Width Layout Plan`, `Platform Auth Integration Memory`, `Agent Tooling Ticket Notes`?**
  _High betweenness centrality (0.072) - this node is a cross-community bridge._
- **What connects `FIELD_GROUPS`, `_META_TYPES`, `SLASH_COMMANDS` to the rest of the system?**
  _84 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `BIOM Auto-Pick & Merge Routes` be split into smaller, more focused modules?**
  _Cohesion score 0.09013605442176871 - nodes in this community are weakly interconnected._