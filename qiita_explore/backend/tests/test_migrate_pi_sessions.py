"""The session migration moves real user conversations, so it is tested on
content-identity, not just on file placement.

Without this migration every pre-existing chat looks session-less to the sidecar
and silently starts a fresh transcript — history loss with no error. That makes
the migration itself the risky part of the per-user layout change.
"""
import hashlib
import sys
import types
from pathlib import Path

import pytest


def _stub_qiita_core():
    if "qiita_core" in sys.modules:
        return
    fake_settings = types.ModuleType("qiita_core.qiita_settings")
    fake_settings.qiita_config = type("_FakeConfig", (), {})()
    fake_core = types.ModuleType("qiita_core")
    fake_core.qiita_settings = fake_settings
    sys.modules["qiita_core"] = fake_core
    sys.modules["qiita_core.qiita_settings"] = fake_settings


_stub_qiita_core()

import migrate_pi_sessions as mig  # noqa: E402


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def flat_sessions(tmp_path, crud, global_chat_crud):
    """A flat session dir holding one global chat, one project chat, one orphan
    and one unrecognisably-named file."""
    gchat = global_chat_crud.create_global_chat("990001", title="g")
    proj = crud.create_project("990002", "p")
    pchat = crud.create_chat(proj["project_id"], "990002", "p")

    d = tmp_path / "sessions"
    d.mkdir()
    files = {
        "global": d / f"2026-07-25T10-00-00-000Z_global-{gchat['chat_id']}.jsonl",
        "project": d / f"2026-07-25T11-00-00-000Z_project-{pchat['chat_id']}.jsonl",
        "orphan": d / "2026-07-25T12-00-00-000Z_global-deadbeef.jsonl",
        "junk": d / "not-a-session-file.jsonl",
    }
    for name, p in files.items():
        p.write_text('{"type":"session","id":"%s"}\n' % name, encoding="utf-8")
    return d, files, gchat["chat_id"], pchat["chat_id"]


class TestUserDirParity:
    """This must agree with pi_sidecar/sessions.mjs :: userDirFor exactly, or the
    migration files sessions somewhere the sidecar never looks."""

    @pytest.mark.parametrize("user_id,expected", [
        ("990001", "990001"),
        ("user@example.com", "user-example.com"),
        ("has/slash", "has-slash"),
        ("..", "_shared"),
        ("../escape", "-escape"),
        ("", "_shared"),
        (None, "_shared"),
    ])
    def test_matches_the_sidecar_rule(self, user_id, expected):
        assert mig._user_dir_for(user_id) == expected


class TestMigrate:
    def test_dry_run_changes_nothing(self, flat_sessions):
        d, files, _, _ = flat_sessions
        before = {p.name: _sha(p) for p in files.values()}
        mig.migrate(d, dry_run=True)
        assert {p.name: _sha(p) for p in files.values()} == before
        assert not (d / "990001").exists()

    def test_files_land_under_their_owner(self, flat_sessions):
        d, files, gid, pid = flat_sessions
        mig.migrate(d, dry_run=False)
        assert (d / "990001" / files["global"].name).exists()
        assert (d / "990002" / files["project"].name).exists()

    def test_content_is_byte_identical_after_the_move(self, flat_sessions):
        """These are user conversations. 'unchanged' gets asserted, not assumed."""
        d, files, _, _ = flat_sessions
        before = {k: _sha(p) for k, p in files.items()}
        mig.migrate(d, dry_run=False)
        assert _sha(d / "990001" / files["global"].name) == before["global"]
        assert _sha(d / "990002" / files["project"].name) == before["project"]
        assert _sha(d / mig.ORPHAN_DIR / files["orphan"].name) == before["orphan"]

    def test_orphans_are_quarantined_not_deleted(self, flat_sessions):
        d, files, _, _ = flat_sessions
        mig.migrate(d, dry_run=False)
        assert (d / mig.ORPHAN_DIR / files["orphan"].name).exists()
        assert (d / mig.ORPHAN_DIR / files["junk"].name).exists(), "unparseable names quarantine too"

    def test_nothing_is_left_flat(self, flat_sessions):
        d, _, _, _ = flat_sessions
        mig.migrate(d, dry_run=False)
        assert list(d.glob("*.jsonl")) == []

    def test_session_path_is_backfilled(self, flat_sessions, global_chat_crud):
        """The whole point of recording it: the sidecar reopens by path instead
        of scanning."""
        d, files, gid, _ = flat_sessions
        from store.cache import SCOPE_GLOBAL
        assert global_chat_crud.get_pi_session_file(gid, SCOPE_GLOBAL) is None
        mig.migrate(d, dry_run=False)
        stored = global_chat_crud.get_pi_session_file(gid, SCOPE_GLOBAL)
        assert stored == str(d / "990001" / files["global"].name)

    def test_orphans_get_no_backfill(self, flat_sessions, global_chat_crud):
        d, _, _, _ = flat_sessions
        mig.migrate(d, dry_run=False)  # must not raise looking up a missing chat

    def test_rerunning_is_safe(self, flat_sessions):
        d, files, _, _ = flat_sessions
        mig.migrate(d, dry_run=False)
        before = _sha(d / "990001" / files["global"].name)
        mig.migrate(d, dry_run=False)  # nothing flat left; must be a no-op
        assert _sha(d / "990001" / files["global"].name) == before

    def test_refuses_to_run_against_an_empty_store(self, tmp_path, crud):
        """A wrong QIITA_EXPERIMENT_DB_PATH resolves every chat as an orphan and
        would quarantine the lot. Refuse instead of 'succeeding'."""
        d = tmp_path / "sessions"
        d.mkdir()
        (d / "2026-07-25T10-00-00-000Z_global-abc12345.jsonl").write_text("{}\n")
        with pytest.raises(SystemExit, match="Refusing to run"):
            mig.migrate(d, dry_run=False)


class TestFilenameParsing:
    @pytest.mark.parametrize("name,scope,chat", [
        ("2026-07-25T17-17-01-600Z_global-ca301ee9.jsonl", "global", "ca301ee9"),
        ("2026-07-26T06-28-33-528Z_project-afe80008.jsonl", "project", "afe80008"),
    ])
    def test_parses_real_barnacle_filenames(self, name, scope, chat):
        m = mig.SESSION_RE.match(name)
        assert m and m.group("scope") == scope and m.group("chat") == chat

    @pytest.mark.parametrize("name", [
        "global-abc.jsonl",                 # no timestamp
        "2026-07-25T10-00-00-000Z_abc.jsonl",  # no scope
        "notes.txt",
    ])
    def test_rejects_shapes_it_should_not_claim(self, name):
        assert mig.SESSION_RE.match(name) is None
