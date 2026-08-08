#!/usr/bin/env python3
"""Delete every project/global-chat whose name/title starts with a given
prefix. Invoked by run_full_suite.sh after the e2e run — belt-and-suspenders
beyond per-test fixture teardown, in case a test crashed mid-run and skipped
its own cleanup.

Usage: teardown_sweep.py <prefix>
Requires QIITA_TEST_PAT and (optionally) BARNACLE_URL in the environment.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from auth_client import AuthedClient, AuthError


def main():
    if len(sys.argv) != 2:
        print("usage: teardown_sweep.py <prefix>", file=sys.stderr)
        sys.exit(2)
    prefix = sys.argv[1]
    base_url = os.environ.get("BARNACLE_URL", "http://localhost:5001")

    try:
        client = AuthedClient(base_url)
    except AuthError as exc:
        print(f"teardown sweep: could not authenticate: {exc}", file=sys.stderr)
        sys.exit(1)

    deleted_projects = 0
    r = client.get("/api/projects")
    r.raise_for_status()
    for proj in r.json().get("projects", []):
        if (proj.get("name") or "").startswith(prefix):
            client.delete(f"/api/projects/{proj['project_id']}")
            deleted_projects += 1

    deleted_chats = 0
    r = client.get("/api/global-chats")
    r.raise_for_status()
    for chat in r.json().get("chats", []):
        if (chat.get("title") or "").startswith(prefix):
            client.delete(f"/api/global-chats/{chat['chat_id']}")
            deleted_chats += 1

    print(
        f"teardown sweep: deleted {deleted_projects} project(s), "
        f"{deleted_chats} global chat(s) with prefix '{prefix}'"
    )


if __name__ == "__main__":
    main()
