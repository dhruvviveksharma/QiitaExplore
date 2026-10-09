# Plan: relate prep to artifact; select, export and isolate per row (sample x artifact)

## Context
In the aggregation table a sample in two artifacts is two rows, but selection is stored per *sample*
(`aggregation_samples`: aggregation, study, sample), so ticking either row ticks both. You asked whether a
prep can be related to an artifact so samples, their preps and their artifacts can be isolated.

**Yes — verified read-only on real data (American Gut 10317):** Qiita stores it in
`qiita.preparation_artifact`; our files query already joins it and then drops it. All 5,969 artifacts belong to
exactly one prep, and for all 104,975 sample-artifact matches the artifact's prep is one of the sample's preps.
Your example `10317.000001299` is in preps 1115 + 1134: artifact 2947 -> prep 1115, artifact 2247 -> prep 1134,
yet today both rows show "1115, 1134".

Decided with you:
- **Per-row selection.** A row = (sample, artifact) — which fixes its prep. Each row has its own checkbox; the
  export contains exactly the checked rows.
- **Counts are checked rows** ("K / N rows"), not samples.

What this settles from earlier tickets: **TKT-094** (rows/paths/filters scoped to the row's prep) and the
grouped-`sort=artifact` limit — a row now has exactly one prep and one artifact.

## Design

**Prep of an artifact:** new `helpers/study_samples.artifact_preps(study_id)` -> `{artifact_id: prep_id}`
(`study_artifact` JOIN `preparation_artifact`, ~6k rows on AGP, memoized 1 h next to `prep_membership`). Entries
in the availability cache are unchanged (no cache version bump, no group-tuple change).

**Row = `(sample_id, artifact_id)`.** A sample with no file row under the study's filter gets one placeholder row
(`artifact_id` None, checkbox disabled, FASTQ "—") so file-less samples stay visible, as today.

**Selection storage:** new table `aggregation_files(aggregation_id, study_id, sample_id, artifact_id, added_at)`,
PK all four, composite FK cascade to `aggregation_studies` (same shape as `aggregation_samples`, which becomes
legacy). Selected rows persist independent of the filter; export / counts intersect with it, as today.
`aggregation_studies` gains `rows_v INTEGER DEFAULT 0` and `file_rows INTEGER` (denominator for "K / N rows").

**Existing aggregations (no silent loss):** a study with `rows_v = 0` is migrated lazily the first time it is
touched (samples page, rows PATCH, facets, export): its checked samples x their artifacts (from
`get_sample_files`) become rows, `file_rows` is set, `rows_v = 1`, legacy rows deleted. Until then its badge falls
back to the legacy sample count. Newly added studies are created with `rows_v = 1` and *every* (sample, artifact)
row checked (file-less samples have no row, so cannot be checked — "9,674 of 9,674 selected" becomes
"9,672 / 9,672 rows" for 14382). Re-adding a study still doesn't undo unticks. Artifacts that appear in Qiita
later are not auto-checked (use Select all).

## Backend

### New `backend/helpers/aggregation_rows.py` (~90 lines)
Moves `_sort_key` and `_order_pairs` out of `aggregation_routes.py` (450 lines now; the cap is 500) and replaces
them with `order_rows(ids, all_files, membership, artifact_prep, file_filter, group, sort, desc)` ->
`(row_keys, groups)`, `row_keys = [(sample_id, artifact_id|None, prep_id|None)]`:
- per sample, in the incoming files-first / id order: its file rows under the filter (`group_matches`), sorted by
  artifact id, prep = `artifact_prep[artifact_id]`; no file row -> placeholder `(sid, None, prep)`, where prep is
  None ungrouped, and grouped is each member prep passing the Data type filter (or None = "No prep");
- `sort=prep|artifact` is now a plain per-row key (no more lowest/highest-of-several rule): value or "none last",
  negated for desc; grouped: `sort=prep` orders the groups, `sort=artifact` the rows inside each; stable;
- `groups` (grouped only) = `[{prep_id, data_type, count}]`, count = rows; `data_type` from membership.

### `backend/store/db.py`, `backend/store/aggregation_crud.py`
- Create `aggregation_files`; migration entries for `rows_v`, `file_rows` (the existing `_ensure_columns` list).
- `_STUDIES_SQL` `selected_rows` = `CASE rows_v WHEN 1 THEN COUNT(aggregation_files) ELSE COUNT(aggregation_samples) END`
  (replaces `selected_samples`).
- `add_study_to_aggregation(..., rows=())`: inserts the (sample, artifact) rows, sets `rows_v=1`, `file_rows=len(rows)`.
- `set_aggregation_rows(agg, user, study, *, add=(), remove=(), clear=False)` (rows are `(sample_id, artifact_id)`);
  `selected_rows_in(agg, study, sample_ids)` -> set of `(sid, aid)` (IN over <= 500 sample ids);
  `selected_by_study(agg)` -> `{study: {(sid, aid)}}`; `migrate_study_rows(agg, study, sample_files)` (idempotent).
- `set_aggregation_samples`, `selected_in` and the legacy insert are removed except what the migration reads.

### `backend/routes/aggregation_routes.py`
- Samples GET: after scope + Show (sample-level, unchanged) call `order_rows`; page the **row keys** (so `total`,
  offset and limit are rows, no more frontend explode). Each row dict: `sample_id, artifact_id, prep_id` (the
  artifact's prep; membership prep for placeholders), `selected` (row in `selected_rows_in`), `file`
  (`{r1, r2, barcodes, data_type, processing}` from `page_files`, matched by artifact), `fastq`/`fasta` for that
  artifact, `data_types`, `fields`. `prep_ids`/`files` lists are dropped from rows. `groups` as before;
  `with_files` = file rows under the filter. Docstring updated.
- Rows PATCH (replaces the samples PATCH body): `{"add": [{"sample_id", "artifact_id"}], "remove": [...]}` or bulk
  `{"select": "all" | "none" | "with_files" | "matching", "q"}`: all = every (sample, artifact) of the study
  (filter-independent, as before); with_files = rows under the filter, restricted by `q`, replacing the selection;
  matching = rows of samples matching `q`, added. Route path stays `…/samples` (PATCH).
- Facets: `selected` = total checked rows; `exportable` = checked rows that pass the study's filter
  (`selected ∩ {(sid, e[2]) …}`).
- Export: `selected` is the pair allowlist; a small `_ready()` helper migrates `rows_v = 0` studies first.

### `backend/helpers/fastq_manifest.py`, `sample_files.py`
- `build_export_rows`: `if (sample_id, artifact_id) in allow` (the group already carries `artifact_id`);
  `_export_groups` unchanged apart from the allowlist type. `summarize_sample_files` untouched.

## Frontend — `frontend/js/aggregation_detail.js` (399 -> ~380: the explode goes away)
- Render rows 1:1 from the response (drop the `r.files` explode / `fileCell` loop); continuation dimming =
  "same `sample_id` as the previous row" (keeps the existing `.agg-row-cont` CSS). Prep ID cell = the row's
  `prep_id`; Artifact/R1/R2/Barcodes from `row.file`.
- Checkbox toggles one row: `agg.setRows(aid, sid, {add|remove: [{sample_id, artifact_id}]})`, flipping only that
  row locally; disabled for placeholder rows. Row keys `sample_id|artifact_id|prep_id`.
- Counts: study badge `K / N rows` (`selected_rows` / `file_rows`; legacy fallback `K samples`); toolbar
  "K of N rows selected"; tab header unchanged otherwise. `aggregations.js`: rename `setSamples` -> `setRows`.

## Docs / tests
- `docs/appendix-a-api-reference.md` (samples GET/PATCH shapes), `docs/appendix-b-sqlite-schema.md`
  (`aggregation_files`, new columns, `aggregation_samples` legacy). Update TKT-094 as done; append: Prep picker
  filter and "select whole prep" header checkbox remain tickets.
- `test_aggregations.py` / `test_aggregation_filters.py` / `test_fastq_manifest.py`: store (rows add/remove/clear,
  `selected_rows_in`, cascade, lazy migration incl. idempotence and legacy-count fallback), routes (row paging,
  per-row `selected`, row PATCH add/remove/bulk, facets counts, export respects a single unticked row), ordering
  (`order_rows`: sort asc/desc per row, grouped by artifact prep, placeholders, multi-prep sample with two preps
  lands each artifact under its own prep), `artifact_preps` (SQL, memo). New `tests/test_aggregation_rows.py`.

## Verification
1. `pytest` (same 2 e2e exceptions); `wc -l` all touched files < 500; Babel compile + the SSR render check used
   before (default state, multi-artifact sample, placeholder row).
2. Helper check on real data: for 10317.000001299 the rows are (2247, prep 1134) and (2947, prep 1115);
   `artifact_preps(10317)` has 5,969 entries; 14382 add-study produces 9,672 rows; export of one unticked row of
   a two-artifact sample omits exactly that row.
3. Restart 5002 (schema + routes). In the browser on 14382 and 10317: ticking one row of 10317.000001299 leaves
   the other unticked; export contains only ticked rows; badge reads "K / N rows"; Group by prep puts each row
   under its own artifact's prep; an existing (pre-change) aggregation still shows its selection after first open.
