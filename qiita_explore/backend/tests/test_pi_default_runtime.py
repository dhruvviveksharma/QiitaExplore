"""pi is the only chat runtime, so its configuration is never optional.

The failure this guards against is quiet: with PI_SIDECAR_SECRET unset, Flask
starts clean, the UI loads, and every chat turn dies on a 401 from
internal_tool_routes._guard(). That reads to an operator as "chat is broken",
not "one variable is missing".
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).parent.parent


class TestPiConfigErrors:
    def _errors(self, monkeypatch, **attrs):
        import config
        for k, v in attrs.items():
            monkeypatch.setattr(config, k, v, raising=False)
        return config.pi_config_errors()

    def test_no_complaints_when_fully_configured(self, monkeypatch):
        assert self._errors(
            monkeypatch, PI_SIDECAR_SECRET="s", PI_SCOPE_TOKEN_KEY="k",
        ) == []

    def test_missing_sidecar_secret_is_reported(self, monkeypatch):
        errs = self._errors(
            monkeypatch, PI_SIDECAR_SECRET=None, PI_SCOPE_TOKEN_KEY="k",
        )
        assert len(errs) == 1 and "PI_SIDECAR_SECRET" in errs[0]

    def test_missing_scope_token_key_is_reported(self, monkeypatch):
        errs = self._errors(
            monkeypatch, PI_SIDECAR_SECRET="s", PI_SCOPE_TOKEN_KEY=None,
        )
        assert len(errs) == 1 and "PI_SCOPE_TOKEN_KEY" in errs[0]

    def test_both_missing_is_reported(self, monkeypatch):
        errs = self._errors(
            monkeypatch, PI_SIDECAR_SECRET=None, PI_SCOPE_TOKEN_KEY=None,
        )
        assert len(errs) == 2

    def test_port_overridden_without_url_is_reported(self, monkeypatch):
        """PI_SIDECAR_PORT moves the sidecar; PI_SIDECAR_URL is what Flask dials.
        Setting one and not the other means Flask posts every turn into a closed
        port — a connection error per chat, with nothing at boot to explain it."""
        monkeypatch.setenv("PI_SIDECAR_PORT", "5199")
        monkeypatch.delenv("PI_SIDECAR_URL", raising=False)
        errs = self._errors(
            monkeypatch, PI_SIDECAR_SECRET="s", PI_SCOPE_TOKEN_KEY="k",
        )
        assert len(errs) == 1 and "PI_SIDECAR_PORT" in errs[0]

    def test_port_and_url_both_set_is_fine(self, monkeypatch):
        monkeypatch.setenv("PI_SIDECAR_PORT", "5199")
        monkeypatch.setenv("PI_SIDECAR_URL", "http://127.0.0.1:5199")
        assert self._errors(
            monkeypatch, PI_SIDECAR_SECRET="s", PI_SCOPE_TOKEN_KEY="k",
        ) == []


class TestBootRefusal:
    """The check has to fire at import, so it is tested by actually importing
    run.py in a subprocess — a monkeypatched call would prove the function
    works while leaving the wiring untested."""

    def _import_run(self, env_overrides):
        # Repo root on PYTHONPATH so the vendored qiita_db/qiita_core resolve —
        # the same fallback start_barnacle.sh applies when they are not
        # pip-installed. Without it this would test "the sandbox lacks Qiita",
        # not the boot guard.
        repo_root = BACKEND.parent.parent
        env = {
            **os.environ,
            "PYTHONPATH": f"{repo_root}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "OPENAI_API_KEY": "test-openai-key",
            "QIITA_EXPERIMENT_DB_PATH": "/tmp/pi-boot-test.db",
            **env_overrides,
        }
        return subprocess.run(
            [sys.executable, "-c", "import run"],
            cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=120,
        )

    def test_refuses_to_boot_without_the_sidecar_secret(self):
        p = self._import_run({"PI_SIDECAR_SECRET": "", "PI_SCOPE_TOKEN_KEY": "k"})
        assert p.returncode != 0
        assert "Refusing to start" in p.stderr
        assert "PI_SIDECAR_SECRET" in p.stderr

    def test_guard_stays_quiet_when_fully_configured(self):
        p = self._import_run({"PI_SIDECAR_SECRET": "s", "PI_SCOPE_TOKEN_KEY": "k"})
        assert "Refusing to start" not in p.stderr, p.stderr


class TestStartScript:
    SCRIPT = BACKEND.parent / "start_barnacle.sh"
    SIDECAR_SCRIPT = BACKEND.parent / "pi_sidecar" / "start_sidecar.sh"

    def test_script_parses(self):
        assert subprocess.run(["bash", "-n", str(self.SCRIPT)]).returncode == 0

    def test_sidecar_script_parses(self):
        assert subprocess.run(["bash", "-n", str(self.SIDECAR_SCRIPT)]).returncode == 0

    def test_pi_env_is_resolved_before_gunicorn_starts(self):
        """gunicorn reads pi's config at import, so the decision has to be made
        before it is spawned — ordering is the whole correctness argument."""
        body = self.SCRIPT.read_text()
        assert body.index("START_SIDECAR=1") < body.index('"${GUNICORN[@]}"')

    def test_refuses_a_port_that_is_already_listening(self):
        body = self.SCRIPT.read_text()
        assert body.index("already in use") < body.index('"${GUNICORN[@]}"')

    def test_sidecar_is_preflighted_before_gunicorn(self):
        """barnacle ran Node 22.6.0: it satisfied the old major-version floor,
        then pi died at import — AFTER gunicorn had logged a clean boot, so the
        die-together loop tore down a healthy Flask. Pre-flighting first is what
        turns that into one message and no gunicorn at all."""
        body = self.SCRIPT.read_text()
        assert body.index('start_sidecar.sh" --check') < body.index('"${GUNICORN[@]}"')

    def test_port_is_resolved_after_the_env_file_is_parsed(self):
        """QIITA_EXPLORE_PORT is read from .env, so PORT= must come after the
        parse. It used to sit ~50 lines above it, which made setting the port in
        .env silently do nothing — the failure mode being a backend on 5001 and
        a tunnel pointed at 5002."""
        body = self.SCRIPT.read_text()
        assert "'QIITA_EXPLORE_PORT'" in body, "must be in the .env `wanted` tuple"
        assert body.index('PORT="${QIITA_EXPLORE_PORT:-5001}"') > body.index("wanted = (")
        # ...and still early enough for the two things that consume it.
        assert body.index('PORT="${QIITA_EXPLORE_PORT:-5001}"') < body.index("already in use")
        assert body.index('PORT="${QIITA_EXPLORE_PORT:-5001}"') < body.index("export FLASK_INTERNAL_URL=")

    def test_sidecar_verifies_pi_actually_imports(self):
        """A major-version floor is not enough and looks correct at a glance —
        that is exactly how 22.6.0 got through. The authoritative gate has to be
        an actual import of pi, not a version comparison."""
        body = self.SIDECAR_SCRIPT.read_text()
        assert 'await import("@earendil-works/pi-coding-agent")' in body
        assert "--input-type=module" in body

    def test_sidecar_check_mode_exits_before_starting_the_server(self):
        body = self.SIDECAR_SCRIPT.read_text()
        assert body.index('CHECK_ONLY') < body.index('exec "$NODE_BIN" server.mjs')
        assert body.index('if [ "$CHECK_ONLY" = "1" ]') < body.index('exec "$NODE_BIN" server.mjs')


class TestStartScriptExitCodes:
    """Runs the real script. The EXIT trap used to end in a bare `exit 0`,
    which replaced the status of every `exit 1` reached after the trap was
    installed — so a refused start and a gunicorn that died at boot both
    reported success to any supervisor checking this command."""

    SCRIPT = BACKEND.parent / "start_barnacle.sh"

    def _run(self, port, extra_env=None):
        cfg = Path("/tmp/pi-start-script-test.cfg")
        cfg.touch()
        env = {**os.environ, "QIITA_CONFIG_FP": str(cfg),
               "QIITA_EXPLORE_PORT": str(port), **(extra_env or {})}
        return subprocess.run(["bash", str(self.SCRIPT)], env=env,
                              capture_output=True, text=True, timeout=180)

    @pytest.fixture
    def occupied_port(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        yield s.getsockname()[1]
        s.close()

    def test_busy_port_exits_nonzero_with_an_actionable_message(self, occupied_port):
        p = self._run(occupied_port, {"PI_SIDECAR_SECRET": "test-secret"})
        assert p.returncode == 1, "a refused start must not report success"
        assert "already in use" in p.stderr
        assert "pkill" in p.stderr, "the error must say how to clear the port"

    def test_gunicorn_dying_at_boot_exits_nonzero(self):
        """gunicorn cannot boot here (no Qiita config), which makes it a usable
        fixture for the failure itself.

        Note this now also depends on the sidecar pre-flight passing, since that
        runs first — i.e. on this machine having a Node that pi can import. If
        that ever makes this brittle, pin PI_NODE_BIN rather than weakening the
        assertion; the pre-flight running first is the point."""
        p = self._run(5079, {"PI_SIDECAR_SECRET": "test-secret", "PI_SCOPE_TOKEN_KEY": "test-key"})
        assert p.returncode == 1
        assert "gunicorn exited during startup" in p.stderr


class TestSidecarPreflight:
    """The regression guard for the barnacle outage: Node 22.6.0 passed a
    `major >= 22` check and then died at import with
    'webidl.util.markAsUncloneable is not a function'."""

    SIDECAR_SCRIPT = BACKEND.parent / "pi_sidecar" / "start_sidecar.sh"

    def _check(self, extra_env=None):
        env = {**os.environ, "PI_SIDECAR_SECRET": "test-secret", **(extra_env or {})}
        return subprocess.run(["bash", str(self.SIDECAR_SCRIPT), "--check"],
                              env=env, capture_output=True, text=True, timeout=120)

    def test_check_passes_on_this_machine(self):
        """Whatever Node runs the rest of the suite must be one pi can import —
        otherwise every other pi test is lying about what it exercised."""
        p = self._check()
        assert p.returncode == 0, f"stdout={p.stdout} stderr={p.stderr}"
        assert "pre-flight OK" in p.stdout

    def test_check_rejects_a_node_that_cannot_import_pi(self, tmp_path):
        """Simulates barnacle's 22.6.0: passes the major floor, fails the import.
        A version-number check alone would wave this through."""
        shim = tmp_path / "node"
        shim.write_text(
            '#!/usr/bin/env bash\n'
            'REAL="$(command -v node)"\n'
            '[ "${1:-}" = "-v" ] && { echo "v22.6.0"; exit 0; }\n'
            'for a in "$@"; do\n'
            '  if [ "$a" = "--input-type=module" ]; then\n'
            '    echo "TypeError: webidl.util.markAsUncloneable is not a function" >&2\n'
            '    exit 1\n'
            '  fi\n'
            'done\n'
            'exec "$REAL" "$@"\n'
        )
        shim.chmod(0o755)
        p = self._check({"PI_NODE_BIN": str(shim)})
        assert p.returncode == 1, "a Node that cannot import pi must be refused"
        assert "22.6.0" in p.stderr, "the message must name the offending version"
        assert "markAsUncloneable" in p.stderr, "and surface the real error"
        assert ">=22.19.0" in p.stderr, "and pi's declared requirement"
