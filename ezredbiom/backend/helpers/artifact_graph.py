"""Build a flat artifact+job processing graph for a study from the Qiita DB.

Uses a dedicated psycopg2 connection per call — safe under gunicorn gthread
(the shared TRN singleton is not thread-safe).
"""

import logging
import os

import psycopg2
import psycopg2.extras
from qiita_core.qiita_settings import qiita_config

logger = logging.getLogger(__name__)
_BASE = os.environ.get("QIITA_BASE_DATA_DIR", "").rstrip("/")


def _abs(path):
    if path and not os.path.isabs(path) and _BASE:
        return f"{_BASE}/{path}"
    return path


def fetch_artifact_graph(study_id: int) -> list:
    """Flat list of artifact+job nodes for a study's processing graph."""
    try:
        return _build(study_id)
    except Exception:
        logger.exception("artifact_graph failed for study %s", study_id)
        return []


def _build(study_id: int) -> list:
    conn = psycopg2.connect(
        user=qiita_config.user,
        password=qiita_config.password,
        database=qiita_config.database,
        host=qiita_config.host,
        port=qiita_config.port,
    )
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            # All artifact IDs for the study
            cur.execute(
                "SELECT artifact_id FROM qiita.study_artifact WHERE study_id = %s",
                (study_id,),
            )
            sa_rows = cur.fetchall()
            if not sa_rows:
                return []
            all_ids = list({r[0] for r in sa_rows})

            # Parent edges: artifact → parent artifact
            cur.execute(
                "SELECT artifact_id, parent_id FROM qiita.parent_artifact WHERE artifact_id = ANY(%s)",
                (all_ids,),
            )
            study_set = set(all_ids)
            child_to_parent = {
                r[0]: r[1] for r in cur.fetchall() if r[1] in study_set
            }

            # Prep-linked artifacts → data_type and prep_template_id
            cur.execute(
                """
                SELECT pa.artifact_id, pa.prep_template_id, dt.data_type
                FROM qiita.study_prep_template spt
                JOIN qiita.prep_template pt        ON spt.prep_template_id = pt.prep_template_id
                JOIN qiita.data_type dt            ON pt.data_type_id = dt.data_type_id
                JOIN qiita.preparation_artifact pa ON pt.prep_template_id = pa.prep_template_id
                WHERE spt.study_id = %s
                """,
                (study_id,),
            )
            prep_rows = cur.fetchall()
            art_to_prep = {r[0]: r[1] for r in prep_rows}
            art_to_dt   = {r[0]: r[2] for r in prep_rows}

            # Jobs: which job produced each non-root artifact
            non_roots = [aid for aid in all_ids if aid in child_to_parent]
            job_map: dict = {}
            if non_roots:
                cur.execute(
                    """
                    SELECT aopj.artifact_id, aopj.processing_job_id::text, sc.name,
                           pj.command_parameters
                    FROM qiita.artifact_output_processing_job aopj
                    JOIN qiita.processing_job pj   ON aopj.processing_job_id = pj.processing_job_id
                    JOIN qiita.software_command sc  ON pj.command_id = sc.command_id
                    WHERE aopj.artifact_id = ANY(%s)
                    """,
                    (non_roots,),
                )
                for r in cur.fetchall():
                    if r[0] not in job_map:
                        raw_params = r[3] or {}
                        # Keep only scalar values; drop artifact-reference keys
                        params = {
                            k: v for k, v in raw_params.items()
                            if isinstance(v, (str, int, float, bool))
                            and k not in ("input_data",)
                        }
                        job_map[r[0]] = (r[1], r[2], params)

            # Batch-fetch artifact metadata + all filepaths; prefer .biom for the node's full_path
            cur.execute(
                """
                SELECT a.artifact_id, at.artifact_type, a.name,
                       dd.mountpoint || '/' || a.artifact_id || '/' || f.filepath AS full_path,
                       f.filepath_id, ft.filepath_type, f.filepath AS filename
                FROM qiita.artifact a
                JOIN qiita.artifact_type at          ON a.artifact_type_id = at.artifact_type_id
                LEFT JOIN qiita.artifact_filepath af ON a.artifact_id = af.artifact_id
                LEFT JOIN qiita.filepath f           ON af.filepath_id = f.filepath_id
                LEFT JOIN qiita.filepath_type ft     ON f.filepath_type_id = ft.filepath_type_id
                LEFT JOIN qiita.data_directory dd    ON f.data_directory_id = dd.data_directory_id
                WHERE a.artifact_id = ANY(%s)
                ORDER BY a.artifact_id, (f.filepath ILIKE '%%.biom') DESC NULLS LAST
                """,
                (all_ids,),
            )
            meta: dict = {}
            files_by_aid: dict = {}
            for r in cur.fetchall():
                aid, atype, aname = r[0], r[1], r[2]
                fp = _abs(r[3]) if r[3] else None
                fpid, ftype, fname = r[4], r[5], r[6]
                if aid not in meta or (fp and fp.lower().endswith(".biom")):
                    meta[aid] = {"artifact_type": atype, "name": aname, "full_path": fp}
                if fpid is not None:
                    files_by_aid.setdefault(aid, []).append({
                        "filepath_id": fpid,
                        "filename": fname or "",
                        "filepath_type": ftype or "",
                        "full_path": fp or "",
                    })
    finally:
        conn.close()

    # Build job nodes and artifact→parent_node mappings
    job_nodes: dict = {}
    art_parent: dict = {}
    for aid in all_ids:
        parent_id = child_to_parent.get(aid)
        if parent_id is None:
            art_parent[aid] = None
        elif aid in job_map:
            job_id, cmd, params = job_map[aid]
            jnid = "j" + job_id.replace("-", "")[:12]
            if jnid not in job_nodes:
                job_nodes[jnid] = {
                    "kind": "job", "node_id": jnid,
                    "parent_node_id": f"a{parent_id}",
                    "job_id": job_id, "command_name": cmd,
                    "command_params": params,
                }
            art_parent[aid] = jnid
        else:
            art_parent[aid] = f"a{parent_id}"

    # Propagate data_type from prep-linked roots down the tree (BFS)
    node_dt: dict = {f"a{aid}": dt for aid, dt in art_to_dt.items()}
    kids: dict = {}
    for aid in all_ids:
        pnid = art_parent.get(aid)
        if pnid:
            kids.setdefault(pnid, []).append(f"a{aid}")
    for jnid, jn in job_nodes.items():
        kids.setdefault(jn["parent_node_id"], []).append(jnid)
    queue = list(node_dt.keys())
    while queue:
        nid = queue.pop(0)
        dt  = node_dt.get(nid)
        for cnid in kids.get(nid, []):
            if cnid not in node_dt and dt:
                node_dt[cnid] = dt
            queue.append(cnid)

    for jnid, jn in job_nodes.items():
        jn["data_type"] = node_dt.get(jnid)

    nodes = []
    for aid in all_ids:
        m = meta.get(aid, {})
        nodes.append({
            "kind":             "artifact",
            "node_id":          f"a{aid}",
            "parent_node_id":   art_parent.get(aid),
            "artifact_id":      aid,
            "prep_template_id": art_to_prep.get(aid),
            "artifact_type":    m.get("artifact_type"),
            "data_type":        node_dt.get(f"a{aid}"),
            "name":             m.get("name"),
            "full_path":        m.get("full_path"),
            "filepaths":        files_by_aid.get(aid, []),
        })
    nodes.extend(job_nodes.values())
    return nodes
