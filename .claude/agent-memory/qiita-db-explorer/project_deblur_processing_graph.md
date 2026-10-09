---
name: deblur-processing-graph
description: Deblur artifact lineage in Qiita PostgreSQL — three distinct categories of BIOM artifacts with different parent_artifact situations, including a migration gap for deprecated deblur versions.
metadata:
  type: project
---

Three categories of deblur/BIOM artifacts in qiita.artifact (verified 2026-06-19):

**Category 1: Deblur 2021.09 (command_id=540) — NORMAL workflow, always has parent**
- 17,470 BIOM artifacts with full parent chain: per_sample_FASTQ → Split libraries FASTQ (cmd 60) → Demultiplexed (cmd 57 Trimming) → BIOM (cmd 540)
- All have parent_artifact rows pointing to Demultiplexed parent

**Category 2: Deprecated Deblur (command_ids 118, 134) — MIGRATION GAP, no parent_artifact rows**
- 9,360 BIOM artifacts with command_id set (118=deblur 1.0.4, 134=deblur 1.1.0, both deprecated/inactive)
- NO rows in qiita.parent_artifact for these artifacts
- BUT command_parameters JSON contains 'Demultiplexed sequences' key with a valid artifact_id — ALL 9,360 have this
- The referenced Demultiplexed artifacts DO exist in qiita.artifact
- This is a data migration gap: parent rows were not backfilled when these old jobs were imported
- These are NOT true direct uploads — they were processed by deblur but the graph edge is missing

**Category 3: Truly uploaded BIOMs (command_id IS NULL, no parent) — DIRECT UPLOADS**
- 12,969 total BIOM artifacts with NULL command_id and no parent_artifact row
- 126 are public visibility, 12,843 are sandbox
- Of the public ones: 16S (113), Metabolomic (5), Metagenomic (3), ITS (2), Metatranscriptomic (2), Full Length Operon (1)
- 141 distinct studies have these uploaded BIOMs
- These ARE legitimate direct uploads — users uploaded BIOM tables without any upstream FASTQ workflow

**Impact on biom_autopick.py:**
The autopick heuristic checks `prep_name` and `full_path` for the string "deblur". Category 2 artifacts (deprecated deblur) may or may not have "deblur" in those fields — the artifact.name is often just "dflt_name". Category 1 artifacts have name "deblur final table" or "deblur reference hit table". See [[biom_autopick_bug]].

**Key table: qiita.parent_artifact** columns: (artifact_id bigint, parent_id bigint)

**Why:** User investigating whether direct-upload BIOM tables (no FASTQ parent) are valid in Qiita — confirmed yes, 12,969 exist, 126 are public.

**How to apply:** When reasoning about deblur artifact lineage, distinguish between the three categories. Do not assume all deblur-command BIOMs have parent_artifact rows — commands 118 and 134 don't.
