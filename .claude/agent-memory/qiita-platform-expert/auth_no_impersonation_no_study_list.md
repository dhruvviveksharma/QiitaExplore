---
name: auth-no-impersonation-no-study-list
description: New Qiita platform auth has no act-as-user delegation and no list-studies endpoint — decisive for qiita-web multi-user integration design
metadata:
  type: reference
---

Two structural facts about the new Qiita platform (`/Users/dhruvsharma/Downloads/Projects/Qiita`) that shape any multi-user integration design for qiita-web:

**1. No principal-impersonation / act-as-user surface.**
`require_study_access` (`qiita-control-plane/src/qiita_control_plane/auth/guards.py:562-`) and
`fetch_caller_study_access` (`qiita-control-plane/src/qiita_control_plane/repositories/study_access.py:32-`)
always key the tier lookup on the *resolved bearer's own* `principal_idx` — there is no
`on_behalf_of` / `owner_idx` query param on read paths that lets a ServiceAccount assert
"check tier for human X." (`owner_idx` body fields exist only on *write* paths like
`POST /study` and `sequenced-sample` composer, to name who *owns a newly created* resource —
not to swap identity for an access check.)
Consequence: a single ServiceAccount PAT held by the qiita-web backend can only ever
enforce ITS OWN scopes/tier against the platform. It cannot ask "does user X have MEMBER
tier on study 7" — qiita-web would have to reimplement/mirror that authorization decision
itself (e.g. by also fetching+caching `qiita.study_access` semantics some other way, which
there's no REST surface for either). This is the central blocker for the "ServiceAccount
does everything" architecture ([[qiita-web-multiuser-auth-integration]]).

**2. No list-studies endpoint.**
`qiita-control-plane/src/qiita_control_plane/routes/study.py` only exposes:
`POST /study` (create), `GET /study/{study_idx}` (single, tier-gated), `PATCH /study/{study_idx}`,
`POST /study/lookup-by-accession`. Confirmed via full route grep — there is no `GET /study`
list route anywhere in the routes directory as of 2026-07 snapshot. qiita-web's browse/grid
view (which today does a broad Postgres query across all studies) has no equivalent REST
call to replace it with. Any integration plan must flag this as a hard gap, not paper over it.

Both facts should be re-verified (`grep -n "@router\." routes/study.py`, grep guards.py for
`on_behalf_of`) if this memory is used more than a couple months after 2026-07-06, since the
platform is under active development.

**Re-verified 2026-07-12** against commit `837fc6cd` (HEAD) — both facts still hold, no
change. Repo-wide grep for `on_behalf_of|impersonat|act_as|sudo_as|effective_principal`
(excluding tests, though tests have none either) returns zero hits. `routes/study.py`
`@router.` decorators are still exactly the same four: `POST /study`, `GET /study/{idx}`,
`PATCH /study/{idx}`, `POST /study/lookup-by-accession` — no list route.
