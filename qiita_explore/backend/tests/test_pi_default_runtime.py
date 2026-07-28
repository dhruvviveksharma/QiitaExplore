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

    def test_script_parses(self):
        assert subprocess.run(["bash", "-n", str(self.SCRIPT)]).returncode == 0

    def test_pi_env_is_resolved_before_gunicorn_starts(self):
        """gunicorn reads pi's config at import, so the decision has to be made
        before it is spawned — ordering is the whole correctness argument."""
        body = self.SCRIPT.read_text()
        assert body.index("START_SIDECAR=1") < body.index('"${GUNICORN[@]}"')

    def test_refuses_a_port_that_is_already_listening(self):
        body = self.SCRIPT.read_text()
        assert body.index("already in use") < body.index('"${GUNICORN[@]}"')


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
        fixture for the failure itself."""
        p = self._run(5079, {"PI_SIDECAR_SECRET": "test-secret", "PI_SCOPE_TOKEN_KEY": "test-key"})
        assert p.returncode == 1
        assert "gunicorn exited during startup" in p.stderr
