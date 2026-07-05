# Simplification Plan for ezredbiom

**Date:** 2026-07-05  
**Goal:** Reduce dead code, reuse logic, simplify patterns, respect 500-line limit

---

## Summary of Findings

| Source | Key Findings |
|--------|--------------|
| **Explore** | Dead code in `qiita_fetch.py`, duplicate SQL queries, legacy `test.ipynb`, duplicate streaming |
| **SWE** | Route duplication across `chat_routes.py`/`global_chat_routes.py`, model selection state sprawl |
| **Reviewer** | 7 files exceed 500-line limit, all splittable with clear boundaries |

**Estimated total reduction:** ~267 lines (direct) + ~1,750 lines redistributed via file splits

---

## Priority 1: Dead Code Removal (Quick Wins)

### TKT-016: Remove `_qiita_fetch()` and `_study_header_cache`

**File:** `ezredbiom/backend/helpers/qiita_fetch.py`

```python
# REMOVE (lines ~119-125):
def _qiita_fetch(sql, params=(), default=None):
    """Run a Qiita-DB SELECT on a pooled connection; return rows or `default` on error/empty."""

# REMOVE (lines ~266-279):
_STUDY_HEADER_TTL_SECONDS = 3600
_study_header_cache = {}
def _fetch_study_header_cached(study_id: int):
```

**Why:** Both are unused or redundant with SQLite cache in `store/cache.py`

**Impact:** ~23 lines removed

---

### TKT-017: Remove unused `test.ipynb`

**File:** `ezredbiom/test.ipynb`

```bash
# DELETE
rm ezredbiom/test.ipynb
```

**Why:** Debug artifact from development, adds confusion

**Impact:** ~99 lines removed

---

### TKT-018: Clean up mid-module imports

**File:** `ezredbiom/backend/helpers/llm_helpers.py:55-59`

```python
# REMOVE unused imports (placed mid-module):
from store import (
    get_project_context_summary,
    get_study_detail_cache,
    upsert_study_detail_cache,
)
```

**Why:** Import placed mid-module, some items unused

**Impact:** ~5 lines removed, improved organization

---

## Priority 2: SQL Query Consolidation (Medium Effort)

### TKT-019: Consolidate duplicate study header queries

**File:** `ezredbiom/backend/helpers/qiita_fetch.py`

| Current Functions | Action |
|-------------------|--------|
| `first_studies()` (lines 51-116) | Merge into `_build_study_header_query()` |
| `_fetch_study_header()` (lines 411-459) | Use shared helper |

**Proposed:** Create `_build_study_header_query(where_clause="", params=())` helper

```python
def _build_study_header_query(where_clause="", params=()):
    """Shared query builder for study headers."""
    base = """
        SELECT s.study_id, s.email, s.principal_investigator_id AS pi,
               s.metadata_complete, s.number_samples_collected, s.number_samples_promised,
               s.reprocess:2, s.first_submission, s.last_submission,
               (SELECT string_agg(DISTINCT dt.data_type, ', ')
                FROM qiita.study_prep_template spt
                JOIN qiita.prep_template pt ON pt.prep_template_id = spt.prep_template_id
                JOIN qiita.data_type dt ON dt.data_type_id = pt.data_type_id
                WHERE spt.study_id = s.study_id) AS data_types,
               (SELECT COUNT(DISTINCT pt.prep_template_id)
                FROM qiita.study_prep_template spt
                JOIN qiita.prep_template pt ON pt.prep_template_id = spt.prep_template_id
                WHERE spt.study_id = s.study_id) AS num_preps,
               (SELECT jsonb_object_agg(pt.prep_template_id::text,
                       jsonb_build_object('data_type', dt.data_type, 'num_samples', pt.num_samples))
                FROM qiita.study_prep_template spt
                JOIN qiita.prep_template pt ON pt.prep_template_id = spt.prep_template_id
                JOIN qiita.data_type dt ON dt.data_type_id = pt.data_type_id
                WHERE spt.study_id = s.study_id) AS preps_json
        FROM qiita.study s
        WHERE 1=1
    """
    return base + where_clause, params
```

**Impact:** ~35-40 lines saved, DRY improvement

---

### TKT-020: Consolidate sample fetch functions

**Files:** `ezredbiom/backend/helpers/qiita_fetch.py`

| Current | Merge Into |
|---------|------------|
| `_fetch_study_samples()` (lines 141-173) | `_fetch_samples()` with optional `limit` param |
| `_fetch_full_sample_metadata()` (lines 228-244) | |

**Impact:** ~20 lines saved

---

## Priority 3: Backend Route Consolidation

### TKT-021: Extract shared `request_utils.py`

**New File:** `ezredbiom/backend/helpers/request_utils.py`

```python
"""Shared request utilities for route handlers."""
from functools import wraps
from flask import request, g

def get_user_id():
    """Extract user_id from request headers or g."""
    return request.headers.get('X-User-ID') or g.get('user_id', 'local_user')

def parse_study_ids_from_request():
    """Parse study_id(s) from request JSON/body."""
    data = request.get_json(silent=True) or {}
    study_id = data.get('study_id')
    study_ids = data.get('study_ids', [])
    if study_id and study_id not in study_ids:
        study_ids.append(study_id)
    return study_ids

def build_full_messages(chat_id, role, content, ui_payload=None):
    """Normalize message format across routes."""
    msg = {'role': role, 'content': content}
    if ui_payload:
        msg['ui_payload'] = ui_payload
    return msg
```

**Usage:** Import in `chat_routes.py`, `global_chat_routes.py`, `project_routes.py`, `merge_routes.py`

**Impact:** ~30-40 lines deduplicated across 4 files

---

### TKT-022: Consolidate SSE streaming patterns

**Files:** `ezredbiom/backend/helpers/agent.py`

| Current | Issue |
|---------|-------|
| `_stream_anthropic_agent()` (lines 49-152) | Duplicated in OpenAI path |
| OpenAI streaming (lines 189-317) | Same structure, different provider |

**Proposed:** Extract base streaming class/function:

```python
class StreamingAgent:
    """Base class for streaming agents."""
    
    def __init__(self, messages, tools, model):
        self.messages = messages
        self.tools = tools
        self.model = model
    
    def stream(self, yield_fn):
        """Common streaming loop."""
        for i in range(max_iters):
            response = self._call_model()
            self.messages.append(response)
            
            if not response.tool_calls:
                yield from yield_fn(response)
                return
            
            for call in response.tool_calls:
                yield from self._execute_tool(call)
```

**Impact:** ~40 lines saved, improved maintainability

---

## Priority 4: Frontend Simplification

### TKT-023: Extract `useModelSelection()` hook

**File:** `ezredbiom/frontend/js/app_state.js`

```javascript
// EXTRACT to hooks/useModelSelection.js
export function useModelSelection(chatId, defaultModel) {
  const [selectedModel, setSelectedModel] = useState(
    () => localStorage.getItem(`model:chat:${chatId}`) 
      || localStorage.getItem('llm:model') 
      || defaultModel
  );

  useEffect(() => {
    localStorage.setItem(`model:chat:${chatId}`, selectedModel);
  }, [chatId, selectedModel]);

  return [selectedModel, setSelectedModel];
}
```

**Remove from `app_state.js`:** ~25 lines

---

### TKT-024: Consolidate date formatting

**File:** `ezredbiom/frontend/js/utils.js`

```javascript
// ADD to utils.js
export function formatDate(dateStr) {
  return new Date(dateStr).toLocaleDateString('en-US', { 
    month: 'short', 
    day: 'numeric' 
  });
}

// UPDATE usages in app_render.js (lines 96-98, 159-162, etc.)
```

**Impact:** ~5 lines saved, consistency

---

### TKT-025: Simplify slash command matching

**File:** `ezredbiom/frontend/js/app_state.js`

```javascript
// BEFORE (lines 628-632):
const slashMatches = useMemo(() => {
  if (!input.startsWith('/')) return [];
  return SLASH_COMMANDS.filter(c => c.cmd.startsWith(input.toLowerCase()));
}, [input]);

// AFTER:
const slashMatches = input.startsWith('/')
  ? SLASH_COMMANDS.filter(c => c.cmd.startsWith(input.toLowerCase()))
  : [];
```

**Impact:** ~5 lines saved, minor perf gain

---

## Priority 5: File Splits (500-Line Limit)

### TKT-026: Split `app_state.js` (672 → ~300 lines)

| New File | Contents | Est. Lines |
|----------|----------|------------|
| `sse_helpers.js` | SSE event handlers, stream transformers | ~150 |
| `loaders.js` | Expand existing: loadProjectDetail, loadGlobalChats, etc. | ~120 |
| `search_helpers.js` | doSearch function | ~80 |
| `chat_actions.js` | newProjChat, deleteProjChat, pinStudy, etc. | ~100 |
| `app_state.js` | Remaining hook (~200 lines) | ~200 |

---

### TKT-027: Split `app_render.js` (601 → ~200 lines)

| New File | Contents | Est. Lines |
|----------|----------|------------|
| `sidebar_renderer.js` | Sidebar with projects/chats | ~170 |
| `browse_renderer.js` | Browse grid, filters | ~140 |
| `chat_renderer.js` | Chat messages, segments | ~160 |
| `composer_renderer.js` | Input composer, pin bar | ~100 |
| `app_render.js` | Topbar, modals, routing | ~130 |

---

### TKT-028: Split `components.js` (689 → ~250 lines)

| New File | Contents | Est. Lines |
|----------|----------|------------|
| `model_picker.js` | Model selection dropdown | ~100 |
| `pinned_bar.js` | Pinned studies UI | ~80 |
| `slash_commands.js` | Command palette | ~120 |
| `components.js` | Remaining core components (~200 lines) | ~200 |

---

### TKT-029: Split `agent_tools.py` (548 → ~250 lines)

| New File | Contents | Est. Lines |
|----------|----------|------------|
| `agent_tool_schemas.py` | TOOL_SCHEMAS, ToolResult dataclass | ~200 |
| `agent_tool_executors.py` | All _tool_* functions | ~300 |
| `agent_tools.py` | Re-export layer (~100 lines) | ~100 |

---

### TKT-030: Split `qiita_fetch.py` (535 → ~200 lines)

| New File | Contents | Est. Lines |
|----------|----------|------------|
| `qiita_queries.py` | Raw fetch functions | ~230 |
| `qiita_study_context.py` | Context builders | ~170 |
| `qiita_fetch.py` | Re-export layer | ~120 |

---

## Implementation Order

```
Phase 1: Dead Code (1 day)
├── TKT-016: Remove _qiita_fetch() and _study_header_cache
├── TKT-017: Remove test.ipynb
└── TKT-018: Clean up mid-module imports

Phase 2: SQL & Logic Consolidation (2 days)
├── TKT-019: Consolidate study header queries
└── TKT-020: Consolidate sample fetch functions

Phase 3: Backend Reuse (2 days)
├── TKT-021: Extract request_utils.py
└── TKT-022: Consolidate SSE streaming

Phase 4: Frontend Simplification (2 days)
├── TKT-023: Extract useModelSelection hook
├── TKT-024: Consolidate date formatting
└── TKT-025: Simplify slash matching

Phase 5: File Splits (3-4 days)
├── TKT-026: Split app_state.js
├── TKT-027: Split app_render.js
├── TKT-028: Split components.js
├── TKT-029: Split agent_tools.py
└── TKT-030: Split qiita_fetch.py
```

---

## Impact Summary

| Category | Lines Removed/Deduplicated |
|----------|---------------------------|
| Dead code | ~127 |
| SQL consolidation | ~55 |
| Backend route reuse | ~40 |
| Frontend simplification | ~35 |
| File splits | ~1,750 redistributed |
| **Total** | **~257 direct + architectural cleanup** |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| File splits break imports | Update imports in one commit, test immediately |
| Consolidating queries changes behavior | Add integration tests before/after |
| Removing caching breaks perf | Benchmark before/after with real data |

---

## Success Criteria

- [ ] No file exceeds 500 lines
- [ ] No unused functions/variables detected by static analysis
- [ ] All SQL queries use shared helpers where applicable
- [ ] Routes share utility functions via `request_utils.py`
- [ ] Frontend extracts reusable hooks to `hooks/` directory