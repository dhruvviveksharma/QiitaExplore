# CLAUDE.md

# Memory

## Me
Dhruv (dhruvviveksharma@gmail.com)

## Projects
| Name | What |
|------|------|
| **qiita-web** | Local Qiita microbiome data analysis frontend. Studies stored in `test_data_studies/studies/{id}/` with sample_template, prep_template, and otu_table.biom |

## Import Workflow
Each study directory needs:
- `sample_template_{id}.txt` — sample metadata (downloaded from Qiita study page)
- `prep_template_{id}.txt` — prep metadata (from Qiita prep info page)
- `otu_table.biom` — BIOM artifact (from Qiita artifact download)

Load via `commands.sh` which calls: `qiita db load-study`, `qiita db load-sample-template`, `qiita db load-prep-template`, `qiita db load-artifact`

## Important Points
Whenever we are interacting with the chatbot, I must see status of what function/tool is being used. I need this information as the user so I know something is working in the background.

---

# Architecture

| Layer      | Tech                                     | Location |
|------------|------------------------------------------|----------|
| Backend    | Gunicorn (`start_barnacle.sh`); port auto: master→5001, else→5002 | `qiita_explore/start_barnacle.sh` → `qiita_explore/backend/run.py` (`run:app`) |
| Frontend   | React (Babel standalone, no build step)  | `qiita_explore/frontend/js/` |
| Local DB   | SQLite under `/ddn_scratch/.../QiitaExploreDB/{deployment\|dev}/` | set by `detect_env.sh` via `QIITA_EXPERIMENT_DB_PATH` |
| Qiita DB   | PostgreSQL (read-only via `TRN`)         | `qiita_db.sql_connection` |
| LLM        | gemma3 via NRP-Nautilus (OpenAI-compat)  | `qiita_explore/backend/helpers/llm_helpers.py` |

**Backend runtime:** We do not run `python run.py` or Flask’s dev server. Start and test the API only with `bash qiita_explore/start_barnacle.sh` (Gunicorn, 4 workers, 2 threads, `gthread`).

---

# Established Patterns

- **No-refresh sidebar**: After add/remove/create/delete, update local React state from the response body — never re-fetch. Use `setOpenProject`, patch `openProject.chats`, etc.
- **Lazy detail fetching**: Study detail (preps, artifacts) only fetched on modal open or study add — never on page load. Uses `study_detail_cache` (6h TTL).
- **LLM context**: Built via helpers in `qiita_explore/backend/helpers/` (e.g. `_study_detail_block()` in `qiita_fetch.py`) — includes `data_types`, `num_samples`, `num_preps`, and prep metadata where applicable.

---

# Dev Workflow

- **Start backend**: `bash qiita_explore/start_barnacle.sh` only (port/data root from current branch via `detect_env.sh`; frontend `api-base` must match)
- **Verify UI changes**: Run barnacle, open browser, test golden path before marking done
- **Tickets**: Unplanned work goes in `~/qiita-web/TICKETS/tickets.md`, not inline



---

# Hard Constraints

- No file in `qiita_explore/` may exceed 500 lines. If approaching limit, split and ticket it.
- Unplanned work → `TICKETS/tickets.md`, not speculative code.

---

# Behavioral Guidelines

Behavioral guidelines to reduce common LLM coding mistakes.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
