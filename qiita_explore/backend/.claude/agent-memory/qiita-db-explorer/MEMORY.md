# Memory Index

- [qiita_db source location](reference_qiita_db_source_location.md) — real qiita_db/qiita_pet source lives in git history (23ec3e8^) and untracked src/qiita-spots/ clone
- [FASTQ path construction bug](project_fastq_path_construction_bug.md) — qiita_fetch.py:440 & artifact_graph.py:109 hardcode artifact_id subdir, ignore data_directory.subdirectory; verified broken on disk
- [run_prefix substring matching](project_run_prefix_substring_matching.md) — sample→FASTQ mapping via run_prefix needs substring match (not startswith) + longest-prefix-first collision guard
