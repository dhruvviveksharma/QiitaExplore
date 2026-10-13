#!/usr/bin/env python
"""Bind QiitaExplore accounts from the qiita-miint login era to classic-Qiita emails.

The classic-login migration (store/db.py _migrate_users_to_classic) keeps every
existing account but binds none of them: qiita-miint never proved its users
owned their emails, so binding on email alone could hand someone's workspaces
to whoever holds that address on classic Qiita. An operator confirms each pair.

Usage (from qiita_explore/backend, with QIITA_EXPERIMENT_DB_PATH and
QIITA_CONFIG_FP exported as start_barnacle.sh sets them — DEPLOYING.md):
  python ../scripts/bind_classic_accounts.py                         # dry run: list accounts
  python ../scripts/bind_classic_accounts.py --apply <user_id>=<email> ...

--apply binds a pair only when a verified classic-Qiita account exists for the
email right now. Run it before that person's first classic login: a first login
creates a new, empty account bound to their email, after which the old one
can't be bound to it. Password hashes are never read out or printed.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
# Before the imports below: importing store.db opens (and creates) the DB, and
# unset it falls back to backend/data/projects.db — the wrong one, silently.
if __name__ == "__main__" and not os.environ.get("QIITA_EXPERIMENT_DB_PATH"):
    sys.exit("QIITA_EXPERIMENT_DB_PATH is not set; export it as start_barnacle.sh does (DEPLOYING.md).")

from helpers import qiita_accounts as accounts  # noqa: E402
from store.auth_store import bind_account  # noqa: E402
from store.db import _conn  # noqa: E402


def _users():
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT user_id, principal_idx, miint_email, qiita_email FROM users ORDER BY created_at")]


def _classic_state(raw_email) -> str:
    email = accounts.normalize_email(raw_email)
    acct = accounts.fetch_account(email) if email else None
    if acct is None:
        return "no classic account"
    return f"classic: {acct.level}" + (" (unverified: can't bind)" if acct.unverified else "")


def dry_run() -> int:
    users = _users()
    if not users:
        print("No accounts.")
        return 0
    for u in users:
        state = f"bound to {u['qiita_email']}" if u["qiita_email"] else _classic_state(u["miint_email"])
        print(f"{u['user_id']}\tprincipal_idx={u['principal_idx']}\tmiint_email={u['miint_email'] or '-'}\t{state}")
    print("\nNothing changed. To bind: --apply <user_id>=<classic email> ...")
    return 0


def apply(pairs) -> int:
    failed = 0
    for pair in pairs:
        user_id, sep, raw = pair.partition("=")
        email = accounts.normalize_email(raw)
        if not sep or not user_id or email is None:
            print(f"SKIP {pair}: expected <user_id>=<email>")
            failed += 1
            continue
        acct = accounts.fetch_account(email)
        if acct is None or acct.unverified:
            print(f"SKIP {user_id}: {email} has no verified classic-Qiita account")
            failed += 1
        elif bind_account(user_id, email):
            print(f"BOUND {user_id} -> {email}")
        else:
            print(f"SKIP {user_id}: no such account, it is already bound, or {email} is bound to another account")
            failed += 1
    return 1 if failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", nargs="+", metavar="USER_ID=EMAIL", help="bind these confirmed pairs")
    args = parser.parse_args(argv)
    print(f"QiitaExplore DB: {os.environ.get('QIITA_EXPERIMENT_DB_PATH')}\n")
    try:
        return apply(args.apply) if args.apply else dry_run()
    except accounts.AccountsUnavailable:
        print("Qiita's user database is unreachable; nothing was bound.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
