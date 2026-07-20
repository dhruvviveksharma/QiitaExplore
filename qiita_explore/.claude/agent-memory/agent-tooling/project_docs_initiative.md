---
name: project-docs-initiative
description: qiita-web has a maintained docs/ directory (chapters 00-11 + appendices) written by a multi-agent doc effort; agent-tooling changes must keep three specific files in sync.
metadata:
  type: project
---

As of Jul 2026, `/Users/dhruvsharma/Downloads/Projects/qiita-web/docs/` is a maintained, actively-written documentation set covering the whole qiita-web app: numbered chapters `00-orientation.md` through `11-roadmap.md`, plus `appendix-a-api-reference.md`, `appendix-b-sqlite-schema.md`, `appendix-c-agent-tools-and-sse.md`, `appendix-d-configuration.md`. It was produced by a team of concurrent sub-agents (seen in one session: `main`, `api-ref`, `config-ref`, `frontend-doc`, `merge-doc`, `schema-ref`), each owning specific files, cross-linking into siblings that may not exist yet at write time (links were written forward-looking and later verified to resolve correctly once siblings landed).

**Why:** the docs are written to a specific, consistent voice — no line numbers (cite as `file.py :: function_name`), tables over prose where a table is scannable, real bugs/gotchas/tickets called out explicitly with blockquote callouts rather than smoothed over, an italic one-line purpose statement under the H1, and a `*See also: ...*` footer linking sibling docs. They are treated as a genuine reference, not a one-off writing exercise — e.g. `appendix-a-api-reference.md` already says "Full event payload documentation lives in appendix-c-agent-tools-and-sse.md" rather than re-deriving it.

**How to apply:** for agent-tooling subsystem work specifically, three files are the authoritative documentation and were cross-verified field-by-field against source in Jul 2026 (read them directly for current contract details — do not rely on this memory for the specifics, they belong in the docs and will drift if duplicated here). Any change to `agent.py`, `agent_tools.py`, `global_chat_routes.py`, or the frontend segments model (`app_state.js`, `components.js`) that alters tool schemas, SSE event shapes, or the persisted `ui_payload` structure should also update:
- `docs/appendix-c-agent-tools-and-sse.md` — exact tool schemas and exact wire protocol (the reference)
- `docs/05-agent.md` — why the agent loop is shaped the way it is (the design rationale)
- `docs/06-streaming-and-chat.md` — the SSE-to-frontend-hydration contract and the dual-authoring hazard

Before writing new docs in this set, read at least one existing sibling (e.g. `appendix-b-sqlite-schema.md` or `04-search.md`) to match voice, and check whether the target chapter already exists — several were being written concurrently in the same session.
