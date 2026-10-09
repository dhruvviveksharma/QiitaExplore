# Memory Index

- [deblur-processing-graph](project_deblur_processing_graph.md) — Deblur artifact lineage: 9360 orphaned BIOMs from deprecated commands 118/134 have parent in command_params but missing parent_artifact rows (migration gap); direct uploads are null command_id; 2021.09 command (540) always has parent chain.
- [committed-projects-db-users-table](project_committed_projects_db.md) — ezredbiom/backend/data/projects.db is git-tracked despite .gitignore; contains dead `users` table w/ password_hash values, no code refs. Ticket candidate.
- [tkt082-per-sample-fastq-scope](project_tkt082_per_sample_fastq_scope.md) — confirmed: exactly 13 per_sample_FASTQ artifacts hit TKT-082's subdirectory bug; "empty dir" look is expected, no 3rd purge-bug class found.
