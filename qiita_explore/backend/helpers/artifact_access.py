"""The one per-user check: files of a public artifact that may hold human reads.

Qiita's check_artifact_access (qiita_pet/handlers/artifact_handlers/base_handlers.py)
shows such an artifact's files only to the study's owner, the users it is shared
with and Qiita admins; everyone else gets "Access denied to artifact N". Which
artifacts those are is Qiita's Artifact.has_human (qiita_db/artifact.py). Here
the artifact itself stays visible, as in Qiita's processing network, and only
its files are locked: names, paths, file ids and downloads.

Everything else stays the same for every viewer (TKT-105: public only). The
shared caches keep the full data; callers lock files where data leaves, with
the viewer of the current request. Fails closed: an unreadable answer locks.
"""
import logging

from flask import g, has_app_context

from helpers.pg_pool import pooled_fetchall
from store import get_study_human_reads_cache, upsert_study_human_reads_cache

logger = logging.getLogger(__name__)

LOCKED = "human_reads"
LOCKED_MESSAGE = ("This artifact may contain human reads; its files are limited to the "
                  "study's owner, shared users and Qiita admins.")
# Qiita's TARGET_GENE_DATA_TYPES: amplicon preps never count as human reads.
_TARGET_GENE = ("16S", "18S", "ITS")
_ADMIN_LEVELS = ("admin", "superuser", "wet-lab admin")


class _All:
    """Every artifact id: what a failed has_human read locks."""
    def __contains__(self, _aid):
        return True

    def __repr__(self):
        return "ALL"


ALL = _All()

# per_sample_FASTQ artifacts of the study that pass has_human's prep tests: some
# prep isn't target gene, and some prep isn't marked as human-filtered.
_CANDIDATES_SQL = f"""
    SELECT a.artifact_id FROM qiita.study_artifact sa
    JOIN qiita.artifact a ON a.artifact_id = sa.artifact_id
    JOIN qiita.artifact_type t ON t.artifact_type_id = a.artifact_type_id
    WHERE sa.study_id = %s AND t.artifact_type = 'per_sample_FASTQ'
      AND EXISTS (SELECT 1 FROM qiita.preparation_artifact pa
                  JOIN qiita.prep_template pt ON pt.prep_template_id = pa.prep_template_id
                  JOIN qiita.data_type d ON d.data_type_id = pt.data_type_id
                  WHERE pa.artifact_id = a.artifact_id AND d.data_type NOT IN {_TARGET_GENE})
      AND EXISTS (SELECT 1 FROM qiita.preparation_artifact pa
                  JOIN qiita.prep_template pt ON pt.prep_template_id = pa.prep_template_id
                  WHERE pa.artifact_id = a.artifact_id AND pt.current_human_filtering IS NOT TRUE)"""

# ...and any of whose samples has an env_package starting with "human-". One
# EXISTS per artifact stops at the first such sample; the rest is reading each
# sample's (wide) JSON, which no rewrite avoids — hence the cache below.
_HUMAN_SQL = """
    SELECT c.aid FROM unnest(%s::bigint[]) AS c(aid) WHERE EXISTS (
        SELECT 1 FROM qiita.preparation_artifact pa
        JOIN qiita.prep_template_sample pts ON pts.prep_template_id = pa.prep_template_id
        JOIN qiita.sample_{sid} s ON s.sample_id = pts.sample_id
        WHERE pa.artifact_id = c.aid AND s.sample_values->>'env_package' LIKE 'human-%%')"""

_RIGHTS_SQL = f"""
    SELECT EXISTS (SELECT 1 FROM qiita.qiita_user u
                   JOIN qiita.user_level l ON l.user_level_id = u.user_level_id
                   WHERE lower(u.email) = %s AND l.name IN {_ADMIN_LEVELS})
        OR EXISTS (SELECT 1 FROM qiita.study WHERE study_id = %s AND lower(email) = %s)
        OR EXISTS (SELECT 1 FROM qiita.study_users WHERE study_id = %s AND lower(email) = %s)"""


def viewer_email():
    """The signed-in viewer's email (users.email, normalized). None outside a
    signed-in request — background jobs, worker threads, the harness — and
    when the session couldn't be re-checked (g.session_row is then set only so
    logout can revoke it, and g.user_id stays None)."""
    if not has_app_context() or not getattr(g, "user_id", None):
        return None
    return (getattr(g, "session_row", None) or {}).get("email")


def human_read_artifact_ids(study_id):
    """The study's artifacts Qiita's has_human flags. ALL on a failed read.
    The candidates are read live (cheap); the sample check is cached for the
    study (6h) and redone as soon as a candidate appears that it hasn't seen."""
    sid = int(study_id)
    try:
        cands = {r[0] for r in pooled_fetchall(_CANDIDATES_SQL, [sid])}
        if not cands:
            return set()
        hit = get_study_human_reads_cache(sid)
        if hit and cands <= set(hit[0]):
            return set(hit[1]) & cands
        human = {r[0] for r in pooled_fetchall(_HUMAN_SQL.format(sid=sid), [sorted(cands)])}
        upsert_study_human_reads_cache(sid, cands, human)
        return human
    except Exception:
        logger.exception("human-read artifacts of study %s unreadable; locking", sid)
        return ALL


def has_study_rights(email, study_id):
    """Qiita admin, the study's owner, or shared on it. False without an email
    and on a failed read."""
    if not email:
        return False
    e, sid = email.lower(), int(study_id)
    try:
        return bool(pooled_fetchall(_RIGHTS_SQL, [e, sid, e, sid, e])[0][0])
    except Exception:
        logger.exception("Qiita rights of a viewer on study %s unreadable; treating as none", sid)
        return False


def viewer_has_rights(study_id):
    """has_study_rights for the current request's viewer."""
    return has_study_rights(viewer_email(), study_id)


_SESSION = object()


def locked_artifact_ids(study_id, email=_SESSION):
    """Artifacts of the study whose files this viewer may not see: its
    human-read ones, unless the viewer has rights on the study. `email`
    defaults to the current request's viewer."""
    human = human_read_artifact_ids(study_id)
    if not human:
        return frozenset()
    if email is _SESSION:
        email = viewer_email()
    return frozenset() if has_study_rights(email, study_id) else human


def lock_files(entry):
    """A graph node or artifacts[] entry with its files taken out."""
    out = {**entry, "files_locked": LOCKED}
    if "filepaths" in out:
        out["filepaths"] = []
    if "full_path" in out:
        out["full_path"] = None
    return out
