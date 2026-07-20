# qiita-platform-expert memory index

- [auth-no-impersonation-no-study-list](auth_no_impersonation_no_study_list.md) — no act-as-user guard param and no GET /study list route; blocks naive ServiceAccount or browse-grid designs (re-verified 2026-07-12)
- [oidc-pat-flow-gotchas](oidc_pat_flow_gotchas.md) — login/handoff/cli-exchange trio is the supported flow; POST /auth/pat is legacy/effectively dead (realm emits no auth_time); handoff browser-flow still HTML-only; CLI loopback flow is the non-scraping JSON path (rewritten 2026-07-12)
- [dev-auth-without-authrocket](dev_auth_without_authrocket.md) — mint_api_token() direct-DB-seed bypass is the practical way to get a qk_ PAT locally without a real AuthRocket tenant
- [miint-naming](miint_naming.md) — "miint" = deploy codename (qiita-miint.ucsd.edu) + unrelated DuckDB extension name, not an auth component
