"""Chat tools that put studies into the user's workspaces (projects in the code):

  add_to_workspace  — study_ids into a named workspace, or this workspace chat's
                      own when none is named. A name that matches no workspace
                      creates it; one that matches several is an error the model
                      asks the user about.
  create_workspace  — a new workspace by exact name (an existing one of that name
                      is reused, never duplicated), with any studies given.

Both write at once and the card offers Undo (it deletes the added studies, and the
workspace too when this call created it and nothing else is in it). Dispatched by
helpers/study_tools.py before its workspace-membership gate, since adding a study
that isn't in the workspace yet is the point. Never without a signed-in user: the
store would write to the shared "default" user.
"""
import logging

from store import (
    PROJECT_STUDIES_CAP, SCOPE_PROJECT, add_study_to_project, create_project, get_project,
    get_project_id_for_chat, list_projects,
)
from helpers.qiita_fetch import _fetch_study_header_cached, is_study_public
from helpers.study_tools import _err, _int
from helpers.tool_result import ToolResult
from helpers.workspace_studies import enrich_study_in_project

logger = logging.getLogger(__name__)

_MAX_STUDIES = 10


def _study_ids(raw):
    """(ids, error): de-duplicated integer ids, at most _MAX_STUDIES."""
    raw = raw if isinstance(raw, list) else ([] if raw in (None, "") else [raw])
    ids = []
    for value in raw:
        sid = _int(value)
        if sid is None:
            return [], f"{value!r} is not a Qiita study id."
        if sid not in ids:
            ids.append(sid)
    if len(ids) > _MAX_STUDIES:
        return [], f"At most {_MAX_STUDIES} studies per call; split the rest into another call."
    return ids, None


def _names(projects):
    return ", ".join(f'"{p.get("name")}"' for p in projects) or "none yet"


def _find_or_create(user_id, name, *, exact_only):
    """(project, created, error) for a workspace name, case-insensitively: an
    exact match, else (unless exact_only) the single workspace whose name
    contains it, else a new workspace."""
    projects = list_projects(user_id)
    key = name.casefold()
    exact = [p for p in projects if (p.get("name") or "").strip().casefold() == key]
    if exact:
        return get_project(exact[0]["project_id"], user_id), False, None
    if not exact_only:
        near = [p for p in projects if key in (p.get("name") or "").casefold()]
        if len(near) == 1:
            return get_project(near[0]["project_id"], user_id), False, None
        if len(near) > 1:
            return None, False, (f'"{name}" matches several workspaces: {_names(near)}. '
                                 "Ask the user which one.")
    project = create_project(user_id, name)
    if not project:
        return None, False, f'Could not create the workspace "{name}".'
    return project, True, None


def _target(user_id, args, scope, chat_id):
    """(project, created, error) for add_to_workspace."""
    wid = (args.get("workspace_id") or "").strip()
    if wid:
        project = get_project(wid, user_id)
        return (project, False, None) if project else (None, False, f"No workspace with id {wid}.")
    name = (args.get("workspace") or "").strip()
    if name:
        return _find_or_create(user_id, name, exact_only=False)
    if scope == SCOPE_PROJECT:
        project = get_project(get_project_id_for_chat(chat_id) or "", user_id)
        if project:
            return project, False, None
    return None, False, (f"Which workspace? The user has: {_names(list_projects(user_id))}. "
                         "Ask, or create one with create_workspace.")


def _add(tool, user_id, project, created, ids):
    pid, name = project["project_id"], project.get("name") or "Untitled"
    have = {int(s["study_id"]) for s in project.get("studies") or [] if _int(s.get("study_id")) is not None}
    added, skipped = [], []
    for sid in ids:
        if sid in have:
            skipped.append({"study_id": sid, "reason": "already there"})
        elif len(have) >= PROJECT_STUDIES_CAP:
            skipped.append({"study_id": sid, "reason": f"workspace is full ({PROJECT_STUDIES_CAP} studies)"})
        elif not is_study_public(sid):
            skipped.append({"study_id": sid, "reason": "private or not found"})
        else:
            header = _fetch_study_header_cached(sid) or {}
            if add_study_to_project(pid, user_id, {**header, "study_id": sid}) is None:
                skipped.append({"study_id": sid, "reason": "could not add"})
                continue
            try:
                enrich_study_in_project(pid, sid)
            except Exception:   # counts fill in later from the workspace's own enrich-all
                logger.exception("enriching study %s in workspace %s failed", sid, pid)
            have.add(sid)
            added.append({"study_id": sid, "study_title": header.get("study_title") or ""})
    project = get_project(pid, user_id) or project

    lines = []
    if tool == "create_workspace":
        lines.append(f'Created workspace "{name}".' if created
                     else f'A workspace named "{name}" already exists; used it (no duplicate made).')
    if added:
        studies = ", ".join(f"study {a['study_id']}" for a in added)
        lines.append(f'Added {studies} to workspace "{name}"'
                     + (" (created now)." if created and tool != "create_workspace" else "."))
    elif ids:
        lines.append(f'Nothing was added to "{name}".')
    if skipped:
        lines.append("Skipped: " + "; ".join(f'{s["study_id"]} ({s["reason"]})' for s in skipped) + ".")
    n = len(project.get("studies") or [])
    lines.append(f'"{name}" now holds {n} stud{"y" if n == 1 else "ies"}. It is already done; '
                 "the user can Undo on the card.")
    summary = (f'{len(added)} added to {name}' if ids else f'workspace {name}') + (" · created" if created else "")
    return ToolResult(
        text="\n".join(lines),
        label=(f"Created workspace {name}" if created and not added else f"Added to {name}"),
        detail=summary,
        ui_payload={"kind": "workspace_update", "project_id": pid, "name": name, "created": created,
                    "added": added, "skipped": skipped, "study_count": n,
                    "updated_at": project.get("updated_at"), "result_summary": summary},
    )


def execute(name, args, *, scope, chat_id, user_id):
    if not user_id:
        return _err(name, "Workspaces need a signed-in user.", "no user")
    ids, bad = _study_ids(args.get("study_ids"))
    if bad:
        return _err(name, bad, "bad study_ids")
    if name == "create_workspace":
        wname = (args.get("name") or "").strip()
        if not wname:
            return _err(name, "create_workspace needs a name.", "no name")
        project, created, err = _find_or_create(user_id, wname, exact_only=True)
    else:
        if not ids:
            return _err(name, "add_to_workspace needs study_ids.", "no studies")
        project, created, err = _target(user_id, args, scope, chat_id)
    if err:
        return _err(name, err, "workspace not resolved")
    return _add(name, user_id, project, created, ids)
