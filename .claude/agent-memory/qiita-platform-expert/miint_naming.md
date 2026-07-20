---
name: miint-naming
description: What "miint" means in the Qiita platform repo — not an auth component, just the platform's deploy/product codename
metadata:
  type: reference
---

Verified 2026-07-12 (repo-wide case-insensitive grep for "miint", commit `837fc6cd`).
"miint" is NOT an auth realm, service, or security component. It shows up in two
unrelated-but-same-name places:

1. **`qiita-miint` = the codename/hostname of this specific production deployment** of the
   new Qiita platform — `qiita-miint.ucsd.edu`, repo checked out at
   `/home/qiita/qiita-miint/` on the deploy host (see `DEPLOY_CHECKLIST.md`, dozens of
   references e.g. line 1808: `curl ... https://qiita-miint.ucsd.edu/api/v1/work-ticket`).
   `CLAUDE.md:131` in the Qiita repo root: "The qiita-miint deploy is live; every migration
   ... has been applied to its Postgres."
2. **`duckdb-miint` = a separate DuckDB SQL extension** that powers the platform's
   bioinformatics functions (FASTQ/FASTA reads, sequence ops) inside the Rust data plane —
   documented at `docs/duckdb-miint.md` (which carries its own "Last checked" freshness
   date per `CLAUDE.md:194` — re-verify before relying on any signature in it).

So the qiita-web branch name `Qiita-miint-auth-integration` most plausibly just means
"integrate qiita-web's auth against the qiita-miint deployment of the new platform" — i.e.
it's naming the target platform instance, not a special auth mode or mechanism. There is
no "miint auth" concept in the source; the auth surface is identical regardless of which
deploy you point at (see [[oidc-pat-flow-gotchas]], [[auth-no-impersonation-no-study-list]]).
