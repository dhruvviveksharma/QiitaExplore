---
name: tkt082-per-sample-fastq-scope
description: Confirmed scope of TKT-082 for per_sample_FASTQ artifacts — the "empty per_sample_FASTQ/<id>/ dir" phenomenon is expected, not corruption; no third "genuinely purged" bug class found.
metadata:
  type: project
---

Investigated 2026-09-24 whether "purged/empty per_sample_FASTQ/<id>/ directory" reports
(examples: artifacts 2516, 2214) are fully explained by TKT-082 (see
`~/qiita-web/TICKETS/tickets.md` line ~2479, "Artifact Path SQL Ignores
`data_directory.subdirectory`") or are a distinct issue.

**Finding: fully explained by TKT-082, no separate bug class found.**

- Queried `qiita.artifact_filepath` / `filepath` / `data_directory` for artifact_type =
  'per_sample_FASTQ': exactly **13 distinct artifacts** have any `subdirectory = false` row
  (matches the ticket's cited count exactly). All 13 are "mixed": one `subdirectory=true` row
  in mountpoint `per_sample_FASTQ` (the `artifact_<id>.html` summary — this is why
  `per_sample_FASTQ/<id>/` looks "empty" of `.fastq.gz`: that mountpoint was *never* where
  these 13 artifacts' sequence files lived) + many `subdirectory=false` rows in mountpoint
  `raw_data` (the real `.fastq.gz` files, flat, no artifact_id segment).
- Checked all 2862 filepath rows across all 13 affected artifacts: the TKT-082-correct
  formula (`{mountpoint}/{artifact_id}/{filepath}` only when `subdirectory=true`, else flat
  `{mountpoint}/{filepath}`) resolves to a real file on disk **100% of the time (2862/2862)**.
  The current unconditional/buggy formula only resolves 13/2862 (just the html summaries,
  which happen to have subdirectory=true).
- Affected artifact IDs (all 13): 2214, 2451, 2512, 2514, 2516, 2561, 2590, 2648, 2942, 2967,
  2985, 3013, 3030.
- Spot-checked 5 random `subdirectory=true` per_sample_FASTQ artifacts (out of 6671 total)
  for a THIRD class — "correct-formula path still missing from disk" (genuine purge
  unrelated to the bug): 1123/1123 files existed. No evidence of that third class in this
  sample (not exhaustive — 6671 artifacts / ~1.8M files total were not fully scanned).

**Why this matters:** confirms TKT-082's fix (branch on `subdirectory` per
`helpers/fastq_manifest.py:103`'s existing pattern) is sufficient for every known
per_sample_FASTQ "missing file" report so far — no separate "files deleted from disk while
DB still claims they exist" bug needs to be chased for this artifact type. If a *new*
"empty directory" report surfaces for an artifact NOT in the 13-ID list above, re-run this
same check before assuming it's the same root cause (don't assume TKT-082 explains
everything forever — re-verify per case).

**How to apply:** if `TKT-082` or per_sample_FASTQ path questions come up again, use the 13
IDs above to sanity-check ticket claims quickly, and reuse the query pattern (join
`artifact_filepath`→`filepath`→`data_directory`, filter `artifact_type='per_sample_FASTQ'`,
`GROUP BY artifact_id HAVING COUNT(DISTINCT subdirectory) > 1` to find "mixed" legacy
artifacts) rather than re-deriving it. Connection pattern used: direct psycopg2 via
`qiita_core.qiita_settings.qiita_config`, same as `helpers/artifact_graph.py` (run under the
`qiita-web` conda env, which has psycopg2; base conda env does not).
