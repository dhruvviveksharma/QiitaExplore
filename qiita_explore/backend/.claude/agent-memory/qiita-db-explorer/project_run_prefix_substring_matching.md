---
name: project-run-prefix-substring-matching
description: Sample-to-FASTQ-file mapping via prep_template's run_prefix column requires substring matching, not startswith, against the stored filepath
metadata:
  type: project
---

Qiita has no explicit sample↔file link table for `per_sample_FASTQ` artifacts — `qiita.artifact_filepath` is just `(artifact_id, filepath_id)`. Mapping is done by matching each sample's `run_prefix` value (from the dynamic `qiita.prep_{prep_template_id}` table, `sample_values->>'run_prefix'`) against filenames. See [[reference_qiita_db_source_location]] for where the canonical matching code lives (`qiita_pet/handlers/api_proxy/studies.py:277-386`, `study_files_get_req()`).

**Gotcha (verified live):** `qiita_db.util.insert_filepaths` (util.py:613-703) renames uploaded files to `"{obj_id}_{original_basename}"` when `data_directory.subdirectory=false` for that mountpoint. So the run_prefix no longer prefixes the *stored* filename — it appears as a substring after a numeric prefix. Verified example: run_prefix `SRR1561443` → stored filepath `360_SRR1561443.fastq.gz` (study 10219, prep 499, artifact 2214).

**How to apply:** any script joining `prep_template_sample` → `prep_{id}` (run_prefix) → `preparation_artifact` → `artifact_filepath` → `filepath` must match run_prefix as a **substring** of `filepath.filepath` (e.g. `filepath LIKE '%' || run_prefix || '%'`), not `startswith`/`LIKE run_prefix || '%'`. Also apply the same collision safeguard the upstream code uses (`qiita_pet/handlers/api_proxy/studies.py:329-331`): sort candidate run_prefixes by length descending before matching, so a short prefix (e.g. `100`) doesn't wrongly swallow a file that actually belongs to a longer one (`1002`).
