---
name: reference-qiita-db-source-location
description: Where to find real qiita_db source (util.py, artifact.py, prep_template.py, schema SQL) when the in-repo qiita_db/ stub isn't enough
metadata:
  type: reference
---

The in-repo `qiita_db/` package (`qiita_db/__init__.py`, `qiita_db/sql_connection.py`) is a **stub** — only the `TRN` transaction wrapper survives. The real `qiita_db` source (`util.py`, `artifact.py`, `meta_util.py`, `metadata_template/prep_template.py`, `metadata_template/sample_template.py`, the full schema dump `support_files/qiita-db-unpatched.sql`, seed data `support_files/populate_test_db.sql`) was stripped in commit `23ec3e8` ("Major repo cleanup: remove dead code, flatten ezredbiom structure"). Two ways to recover it:

1. **Git history**: `git show 23ec3e8^:qiita_db/<path>` — the full pre-cleanup tree is still reachable (not on any branch HEAD, but present in history). `git ls-tree -r --name-only 23ec3e8^ -- qiita_db` lists everything that existed.
2. **Untracked reference clone**: `/home/d4sharma/qiita-web/src/qiita-spots/` — a full upstream Qiita checkout (qiita_db, qiita_pet, qiita_ware, etc.), explicitly noted as excluded/untracked in `qiita_db/TABLE_USAGE.md:3` ("excludes untracked `src/qiita-spots/` reference clone"). This is broader than the git-history option — it also has `qiita_pet` (web handlers, e.g. run_prefix-to-filename matching logic) which was never in this repo's git history at all.

Both sources agreed on every function I cross-checked. Prefer `src/qiita-spots/` first since it's a full checkout including `qiita_pet`/`qiita_ware`; fall back to git history only for things `qiita_db/TABLE_USAGE.md` says this repo's own commits touched.

See also [[project_fastq_path_construction_bug]] and [[project_run_prefix_substring_matching]], both grounded in these sources.
