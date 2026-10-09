---
name: project-fastq-path-construction-bug
description: qiita_fetch.py and artifact_graph.py hardcode an artifact_id subdirectory segment in file paths, ignoring data_directory.subdirectory — produces nonexistent paths for some artifacts
metadata:
  type: project
---

Verified 2026-09-04 against the live `qiita-db-rc` Postgres DB and the real `/qmounts/qiita_data` filesystem (study 10219, prep 499, artifact 2214, a real `per_sample_FASTQ` artifact).

Two places in this repo build a full filesystem path with the same SQL fragment:
- `qiita_explore/backend/helpers/qiita_fetch.py:440` (`_fetch_study_detail_from_qiita()`)
- `qiita_explore/backend/helpers/artifact_graph.py:109` (`_build()`)

```sql
dd.mountpoint || '/' || a.artifact_id || '/' || f.filepath AS full_path
```

This always inserts `artifact_id` as a subdirectory, but the canonical qiita_db logic (`qiita_db.util._path_builder`, see [[reference_qiita_db_source_location]]) only does this when `data_directory.subdirectory = true` **for that specific file's `data_directory_id`** — and different rows for the same conceptual mountpoint (e.g. two different `raw_data` rows) can disagree. Confirmed live: artifact 2214's files live under `data_directory_id=5` (`mountpoint='raw_data', subdirectory=false`). The correct path `/qmounts/qiita_data/raw_data/360_SRR1561443.fastq.gz` exists on disk; the path this SQL builds, `/qmounts/qiita_data/raw_data/2214/360_SRR1561443.fastq.gz`, does not.

**Why:** the SQL never joins/checks `dd.subdirectory`, so it silently assumes every mountpoint uses the "subdirectory per artifact_id" convention. That assumption is false for at least the `raw_data` mountpoint on this deployment.

**How to apply:** any new feature that resolves artifact file paths (e.g. a per-sample FASTQ path CSV) must NOT copy this SQL pattern. Join `data_directory.subdirectory` and branch in application code, mirroring `_path_builder` in `qiita_db/util.py`. Flag this as a `TICKETS/tickets.md` entry rather than fixing it directly (read-only agent scope) — not yet ticketed as of 2026-09-04.

Also note: `QIITA_BASE_DATA_DIR` (the `/qmounts/qiita_data` prefix, used by `_abs()` in `qiita_fetch.py:457-460`, and identically in `biom_samples.py:9`, `artifact_graph.py:15`, `merge_executor.py:83`) is **not set in the live `.env`** — only present in `.env.bak.20260718004907` / `.env.bak.1783923287`. Without it, `_fetch_study_detail_from_qiita()`'s `full_path` values are relative (`mountpoint/artifact_id/filepath`), not absolute.
