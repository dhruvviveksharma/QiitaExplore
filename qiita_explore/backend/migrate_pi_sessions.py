#!/usr/bin/env python3
"""One-shot: move flat pi session files into per-user directories.

Sessions used to live in one flat directory shared by every user:

    .state/sessions/2026-07-25T17-17-01-600Z_global-ca301ee9.jsonl

They now live under the owning user:

    .state/sessions/990001/2026-07-25T17-17-01-600Z_global-ca301ee9.jsonl

WITHOUT running this, every pre-existing chat looks session-less to the sidecar,
which silently starts a fresh transcript — users lose their conversation context
with no error anywhere.

The filename carries the scope and chat_id but not the owner, so the owner is
recovered by looking chat_id up in global_chats / project_chats. While moving a
file we also backfill that chat's pi_session_file column, which is what lets the
sidecar reopen it by path instead of scanning.

Files whose chat_id matches no row are MOVED to _orphaned/, not deleted. Those
are the leaked JSONLs docs/09-operations.md documents as needing a manual reaper;
quietly destroying user conversations during a layout change is not on.

RUN THIS ON BARNACLE, IN PLACE. Session files are verbatim user conversations
and must not leave the host — do not copy them somewhere to process and write
back. It reads the SAME SQLite the backend uses, so run it from the activated
qiita-web env with the backend's environment:

    cd ~/qiita-web/qiita_explore/backend
    conda activate qiita-web
    python3 migrate_pi_sessions.py --dry-run     # look first
    python3 migrate_pi_sessions.py
"""
import argparse
import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

# "<timestamp>_<scope>-<chat_id>.jsonl", e.g.
# 2026-07-25T17-17-01-600Z_global-ca301ee9.jsonl
SESSION_RE = re.compile(r"^(?P<ts>[0-9TZ:.\-]+)_(?P<scope>global|project)-(?P<chat>[A-Za-z0-9._-]+)\.jsonl$")

ORPHAN_DIR = "_orphaned"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _user_dir_for(user_id: str) -> str:
    """Must match pi_sidecar/sessions.mjs :: userDirFor exactly, or the sidecar
    will look somewhere this script did not write."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", str(user_id or ""))
    safe = re.sub(r"^\.+", "", safe)
    return safe or "_shared"


def _resolve_owners(chat_keys):
    """{(scope, chat_id): user_id} for every chat that still exists."""
    from store.db import _conn

    owners = {}
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(1) AS c FROM global_chats").fetchone()["c"]
        proj_total = conn.execute("SELECT COUNT(1) AS c FROM project_chats").fetchone()["c"]
        # An empty store is what a wrong QIITA_EXPERIMENT_DB_PATH looks like, and
        # the consequence would be quarantining every session as an orphan.
        # Refuse rather than "successfully" migrating nothing into _orphaned/.
        if total == 0 and proj_total == 0:
            raise SystemExit(
                "Refusing to run: both global_chats and project_chats are empty.\n"
                f"QIITA_EXPERIMENT_DB_PATH={os.environ.get('QIITA_EXPERIMENT_DB_PATH', '(unset)')}\n"
                "That is what a wrong database path looks like, and continuing would "
                "quarantine every session file as an orphan."
            )
        for scope, chat_id in chat_keys:
            table = "global_chats" if scope == "global" else "project_chats"
            row = conn.execute(
                f"SELECT user_id FROM {table} WHERE chat_id = ?", (chat_id,)
            ).fetchone()
            if row:
                owners[(scope, chat_id)] = row["user_id"]
    return owners


def _backfill_session_file(chat_id, scope, path):
    from store import set_pi_session_file
    from store.cache import SCOPE_GLOBAL, SCOPE_PROJECT
    set_pi_session_file(chat_id, SCOPE_GLOBAL if scope == "global" else SCOPE_PROJECT, str(path))


def migrate(session_dir: Path, dry_run: bool):
    flat = sorted(p for p in session_dir.glob("*.jsonl") if p.is_file())
    if not flat:
        print(f"Nothing to do — no flat .jsonl files directly under {session_dir}")
        return 0

    parsed, unparseable = {}, []
    for path in flat:
        m = SESSION_RE.match(path.name)
        if m:
            parsed[path] = (m.group("scope"), m.group("chat"))
        else:
            unparseable.append(path)

    owners = _resolve_owners(set(parsed.values()))

    moved = orphaned = 0
    for path, (scope, chat_id) in parsed.items():
        user_id = owners.get((scope, chat_id))
        target_dir = session_dir / (_user_dir_for(user_id) if user_id else ORPHAN_DIR)
        target = target_dir / path.name
        label = f"{path.name}  ->  {target_dir.name}/"
        if user_id is None:
            print(f"  ORPHAN  {label}   (no {scope} chat row for {chat_id})")
            orphaned += 1
        else:
            print(f"  move    {label}   (user {user_id})")
            moved += 1
        if dry_run:
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        if target.exists():
            print(f"  SKIP    {path.name} — already present at the destination")
            continue
        before = _sha256(path)
        shutil.move(str(path), str(target))
        if _sha256(target) != before:
            raise SystemExit(f"content changed while moving {path.name} — aborting")
        if user_id is not None:
            _backfill_session_file(chat_id, scope, target)

    for path in unparseable:
        target_dir = session_dir / ORPHAN_DIR
        print(f"  ORPHAN  {path.name}  ->  {ORPHAN_DIR}/   (unrecognised filename)")
        orphaned += 1
        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target_dir / path.name))

    print()
    verb = "would move" if dry_run else "moved"
    print(f"{verb} {moved} session(s) into per-user directories; {orphaned} quarantined in {ORPHAN_DIR}/")
    if orphaned:
        print(f"Nothing was deleted. Review {session_dir / ORPHAN_DIR} before removing anything.")
    if dry_run:
        print("Dry run — no files were touched. Re-run without --dry-run to apply.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session-dir", default=None,
                    help="defaults to ../pi_sidecar/.state/sessions relative to this script")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and change nothing")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent))

    session_dir = Path(args.session_dir) if args.session_dir else (
        Path(__file__).resolve().parent.parent / "pi_sidecar" / ".state" / "sessions"
    )
    if not session_dir.is_dir():
        raise SystemExit(f"session directory not found: {session_dir}")

    print(f"session dir : {session_dir}")
    print(f"database    : {os.environ.get('QIITA_EXPERIMENT_DB_PATH', '~/.qiita-experiment/projects.db (default)')}")
    print()
    return migrate(session_dir, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
