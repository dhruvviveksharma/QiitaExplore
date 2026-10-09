---
name: committed-projects-db-users-table
description: ezredbiom/backend/data/projects.db is tracked in git despite a .gitignore rule, and contains a dead `users` table with password_hash values that has zero code references.
metadata:
  type: project
---

`ezredbiom/backend/data/projects.db` (the live local SQLite store) is tracked by git
(`git ls-files` confirms it), even though `ezredbiom/backend/data/.gitignore` explicitly
lists `projects.db` to be ignored — it was likely force-added before the ignore rule
existed, so git keeps tracking edits to it. Verified 2026-07-01 on branch UI/UX-improvements.

The committed copy contains a `users` table (`user_id`, `username`, `email`,
`password_hash`, `role`, `created_at`) with 3 rows. This table does **not** appear in
`ezredbiom/backend/store/db.py`'s `_create_schema()` and has **zero** references anywhere
in `ezredbiom/backend/store/*.py` or routes — it's dead schema from a removed/unbuilt auth
feature, but the committed binary still carries real `password_hash` values in git history.

**Why:** Discovered while cataloging all SQL tables in the repo (both Postgres and SQLite)
for a read-only schema audit. Not something visible from reading `db.py` alone — required
inspecting the actual tracked binary with `sqlite3 -readonly`.

**How to apply:** If asked about SQLite schema, note the discrepancy between the *code*
schema (`db.py`, 14 tables: meta, projects, project_studies, project_chats,
project_chat_messages, project_context_summaries, global_chats, global_chat_messages,
study_detail_cache, chat_pinned_studies, merge_workspaces, merge_workspace_studies,
merge_jobs, biom_sample_cache) and the *committed file's* actual schema (adds a 15th,
orphaned `users` table). This is a candidate for a `TICKETS/tickets.md` entry — recommend
it to the user rather than editing tickets.md directly (per read-only role), covering: (1)
untrack `projects.db` from git / scrub from history since it holds credential-shaped data,
(2) decide whether the `users` table should be removed entirely or is leftover from planned
auth work worth asking about.
