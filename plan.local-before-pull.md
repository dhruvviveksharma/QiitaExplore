# Sample Aggregation: Demultiplexed artifacts as a file source + per-study artifact picker


## Context

The Sample Aggregation tab only knows `per_sample_FASTQ` / `FASTA` artifacts, and links samples to files by
`run_prefix` (`helpers/fastq_manifest.py`). Older 454 / split-libraries studies such as **1070** have none of
those. Their reads live only in **Demultiplexed** artifacts: one multiplexed `seqs.fastq.gz` per artifact, with
each read header starting with `@<sample_id>_N`. So all 118 samples of 1070 show "—" and can't be exported.

**Scale (verified read-only 2026-09-24):**
- **545 of the 818** public studies that have Demultiplexed artifacts have **no per-sample files at all**.
- All 5,650 public Demultiplexed artifacts have `preprocessed_demux`, `preprocessed_fastq` and `preprocessed_fasta`.
- One study has **502** Demultiplexed artifacts.

**User decisions:**
1. **Both:** Demultiplexed becomes a source, **and** each study gets a picker for which artifacts feed the
   availability columns and the CSV. For 1070 that means choosing between untrimmed 252 and the 90/100/150 bp
   trims 7265 / 6343 / 6485.
2. **Real per-sample membership, not "trust the prep":** find out which samples actually have reads, and cache it.

**How "has reads" is checked:** `seqs.demux` is HDF5 with **one group per sample id** (plus per-sample read counts
in attrs). Listing its keys takes 0.04–0.7 s, even on a 5 GB file. On 12 random public artifacts, the keys were
always a subset of the prep's sample ids (1070/6485 has 114 keys vs 118 prep samples). That's much cheaper than
decompressing and scanning `seqs.fastq.gz`. Artifacts are immutable, so the result is cached forever, the same
way `biom_sample_cache` is (`helpers/biom_samples.py`). h5py 3.14 is already in the `qiita-web` env.

## Decisions taken here (flagged, easy to flip)

- **Artifacts the picker lists:** `per_sample_FASTQ`, `FASTA` and `Demultiplexed`. SFF and BIOM are left out,
  because they can't produce a per-sample file row.
- **Default selection** (when a study is added, and for existing rows):
  - if the study has any per-sample artifacts, select exactly those. This matches today's behavior.
  - otherwise, select every Demultiplexed artifact whose command is **not** `Trimming`, i.e. the untrimmed
    split-libraries output of each prep. For 1070 that is `[252]`.
- **Demux CSV rows:** one row per sample, pointing at the artifact's shared `seqs.fastq.gz`, with
  `file_type = preprocessed_fastq`. `seqs.fna.gz` is not emitted; it holds the same reads.
- **CSV gains a 6th column, `artifact_id`**, appended last so positional consumers don't break. Without it, a
  sample exported from two trims shows up in two rows that differ only by path.
- **Sample table gains a "Demux" column** (✓ / —) next to FASTQ / FASTA.

## Backend

### 1. Storage: `store/db.py`, `store/aggregation_crud.py`, `store/cache.py`
- Add migration columns to the existing `ALTER TABLE` list in `db.py`:
  - `("aggregation_studies", "artifact_ids_json", "TEXT")`: the picked artifact ids. **NULL means use the
    default**, which covers aggregations created before this change.
  - `("study_detail_cache", "artifact_files_json", "TEXT")`: see §3. `sample_files_json` stops being
    written. Leave the column in place (SQLite column drops aren't worth it) and mention it in the docs.
- New table `demux_sample_cache(artifact_id INTEGER PRIMARY KEY, sample_ids_json TEXT, cached_at TEXT)`, shaped
  like `biom_sample_cache`. Add `get_/upsert_demux_sample_cache` in `cache.py` modeled on
  `get_/upsert_biom_sample_cache`, and export both from `store/__init__.py`.
- `add_study_to_aggregation(..., artifact_ids)`:
  - stores `json.dumps(artifact_ids)`.
  - `fastq_artifact_count` becomes `len(artifact_ids)`, so the existing header count now reads as "selected
    artifacts".
- New `set_aggregation_artifacts(aggregation_id, user_id, study_id, artifact_ids)`:
  - updates `artifact_ids_json` and `fastq_artifact_count`, touches the aggregation, and returns the full
    aggregation. Same shape as `set_aggregation_samples`.
- `_studies()` decodes `artifact_ids_json` to a list, or None.

### 2. Listing eligible artifacts: new `helpers/study_seq_artifacts.py` (~110 lines)
- `list_seq_artifacts(study_id)` → `[{artifact_id, artifact_type, data_type, prep_template_id, command,
  label, parent_id}]`.
  - Uses one `pooled_fetchall` over `study_artifact` / `artifact` / `artifact_type` / `preparation_artifact` /
    `prep_template` / `data_type`, LEFT JOINing `software_command` and `parent_artifact`.
  - Filters to `artifact_type IN ('per_sample_FASTQ','FASTA','Demultiplexed')`.
  - `label` example: `"6485 · Trimming (150 bp) · 16S · prep 432"`. The bp comes from
    `command_parameters->>'length'` when present.
- `default_artifact_ids(arts)` implements the default rule above.
- `resolve_artifact_ids(stored, study_id)` → `stored` if not None, else `default_artifact_ids(list_seq_artifacts(study_id))`.
- `get_demux_sample_ids(artifact_id, abs_path)`:
  - reads `h5py.File(path)["/"].keys()`, cached forever via `demux_sample_cache`. Mirrors
    `biom_samples.get_biom_sample_ids`.
  - on a read error, logs it and returns `[]` without caching, so a transient qmounts hiccup isn't cached.

### 3. Availability + export: `helpers/fastq_manifest.py` (292 lines now; stays under 400)
- **SQL:** `_STUDIES_FILES_SQL` gains a Demultiplexed branch
  (`artifact_type = 'Demultiplexed' AND filepath_type IN ('preprocessed_fastq','preprocessed_demux')`) and
  an `AND a.artifact_id = ANY(%s)` filter. Everything is keyed by the **selected** artifact ids from now on.
- **`_study_groups(study_ids, selected, artifact_ids)`:**
  - per-sample groups are unchanged.
  - Demultiplexed groups carry `samples = [(sid, None) for sid in get_demux_sample_ids(...)]`, and `files`
    holds just the `preprocessed_fastq` file. The `.demux` path is built with the same subdirectory rule as
    `build_manifest_rows`. Factor that one line into `_rel_path(mountpoint, subdirectory, artifact_id, filename)`
    so there is still one path builder.
- **`build_csv_rows`:**
  - Demultiplexed branch: every allowed sample in `samples` yields
    `(study, sample, fastq_path, data_type, "preprocessed_fastq", artifact_id)`.
  - per-sample rows gain `artifact_id` too.
  - `CSV_HEADER` and both `to_csv` paths add `artifact_id`.
- **`summarize_sample_files` / `get_sample_files(study_id, artifact_ids)`:**
  - the per-study cache (memo + `artifact_files_json`, 6h) now holds **per-artifact** maps for the study's
    per-sample artifacts: `{aid: {sid: [fastq, fasta]}}`. It's computed as today, but not merged. It has to be
    per-artifact so it can be filtered by the selection.
  - `get_sample_files` merges only the selected artifacts, adds the demux ids of the selected Demultiplexed
    artifacts (cached forever), and returns `{sid: [fastq, fasta, demux]}`.
- **`fetch_aggregate_csv_rows(selected, artifacts_by_study)`** passes the resolved ids through.
  `count_fastq_artifacts` becomes unused and is removed.

### 4. Routes: `routes/aggregation_routes.py` (244 → ~300)
- `POST …/studies`: `artifact_ids = default_artifact_ids(list_seq_artifacts(study_id))` is passed to
  `add_study_to_aggregation` (this replaces `count_fastq_artifacts`).
- **New `GET …/studies/<sid>/artifacts`** → `{"artifacts": [...list_seq_artifacts + "selected": bool]}`.
- **New `PATCH …/studies/<sid>/artifacts`**, body `{"artifact_ids": [int,...]}`:
  - 400 if it's not a list of ints, or any id isn't one of the study's eligible artifacts. An empty list is allowed.
  - returns the full aggregation, following the no-refresh pattern.
- `GET …/samples`, and `PATCH …/samples` with `select: with_files`: call
  `get_sample_files(sid, resolve_artifact_ids(study["artifact_ids"], sid))`. Rows gain `"demux": bool`.
- `export.csv` builds `artifacts_by_study` with `resolve_artifact_ids` for each selected study.

## Frontend

### 5. `js/aggregations.js`
- Add `setArtifacts(id, studyId, artifact_ids)` to `useAggregations`: PATCH, then `replace()`, like `setSamples`.

### 6. `js/aggregation_detail.js` (262 → ~300)
- In `AggregationSampleTable`, fetch `GET …/artifacts` on mount.
- **Reuse `FacetMultiSelect`** (`browse_filters.js`; already on the page, loaded earlier in `index.html`). It's
  the CLAUDE.md-sanctioned `useDropdown` multi-select, stays open on toggle, and has a search box, which matters
  for the 502-artifact study.
  - Props: `label="Artifacts"`, `searchable`, options `{name: label}`. Map names back to ids on change, which works
    because labels start with the unique artifact id.
  - On change: `await agg.setArtifacts(...)`, patch the local `selected` flags, then `load(0, …)`, because
    availability changed.
- Add a **Demux** column (✓ / —). Bump `colSpan` to `columns.length + 5`.
- Header sub-line wording: "sequence-file artifacts" becomes "selected artifacts". The count already sums
  `fastq_artifact_count`.
- `index.html`: bump `?v=` on `aggregation_detail.js` and `aggregations.js`.

## Tests (`backend/tests/`, run in the `qiita-web` conda env)
- `test_fastq_manifest.py`:
  - `build_csv_rows` with a Demultiplexed group (legacy flat `preprocessed_data/252_seqs.fastq.gz` and modern
    `Demultiplexed/6485/seqs.fastq.gz`), checking that only allowed samples are emitted.
  - the `artifact_id` column, in both CSV variants.
  - `get_sample_files` filtering by artifact ids, and the demux merge (with `get_demux_sample_ids` stubbed).
  - update the existing caching tests to the per-artifact shape.
- New `test_study_seq_artifacts.py`:
  - `default_artifact_ids` (per-sample wins; otherwise non-Trimming demux; empty study → []).
  - `get_demux_sample_ids` against a tiny h5py file in tmp_path: it reads keys, caches, and doesn't cache errors.
  - labels.
- `test_aggregations.py`:
  - update `stub_qiita` (swap `count_fastq_artifacts` for `list_seq_artifacts`; `get_sample_files(sid, ids)`).
  - add: GET artifacts marks the defaults selected; PATCH with a valid list updates `fastq_artifact_count`;
    PATCH with a foreign id → 400; a legacy NULL row resolves to the default; the samples route returns `demux`.

## Docs / tickets
- `docs/appendix-a-api-reference.md`: the two new routes, the 6th CSV column, and the `demux` row field.
- `docs/appendix-b-sqlite-schema.md`: `artifact_ids_json`, `artifact_files_json`, `demux_sample_cache`, and a
  note that `sample_files_json` is no longer written.
- `TICKETS/tickets.md`: ticket anything deferred. Candidates: showing per-sample read counts from `.demux` attrs,
  and a Demultiplexed branch for the study-modal QIIME2 manifest.

## Verification
1. `pytest backend/tests/test_fastq_manifest.py backend/tests/test_aggregations.py backend/tests/test_study_seq_artifacts.py`
   passes, then the full suite.
2. Check every emitted path exists on disk: use `fetch_aggregate_csv_rows({1070: all 118 ids}, {1070: [252]})`
   in a dev env with `QIITA_EXPERIMENT_DB_PATH` set, then `os.path.isfile` each path. Expect 114 rows, all
   pointing to `/qmounts/qiita_data/preprocessed_data/252_seqs.fastq.gz`. Repeat with `[6485]` → 114 rows under
   `Demultiplexed/6485/`. Regression check: re-run the memory's per-sample targets (14200/138344 paired,
   1939/2516 legacy flat) with their default selection; the rows must be unchanged apart from the new column.
3. Golden path in the UI, started only with `bash qiita_explore/start_barnacle.sh`:
   - add 1070 to an aggregation and expand its card. The Artifacts picker shows 252 / 6343 / 6485 / 7265 with
     252 checked.
   - "With files" shows 114, and the Demux column shows ✓.
   - switch to 6485; the table reloads with no page refresh.
   - download the CSV and check its rows and the `artifact_id` column.
   - add 14200: its picker defaults to the per-sample artifact, and FASTQ shows R1+R2 as before.
