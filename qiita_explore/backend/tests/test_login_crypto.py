"""Sealed sign-in (helpers/login_crypto.py): the password and the Qiita-Miint
token reach the backend only inside an envelope sealed to its key, so the hops
in between (Cloudflare, kl-remote) never see them readable. Covers the key,
the one-time challenge, every way an envelope is refused, and the routes'
answers. The browser side is tests/auth_helpers.py seal() (same scheme)."""
import base64
import hashlib
import hmac
import os
import secrets
import stat
import struct
import sys
import threading
import time

import pytest

from . import auth_helpers as ah
from .conftest import stub_qiita_db_and_core


@pytest.fixture(scope="module")
def _app(tmp_path_factory):
    os.environ["QIITA_EXPERIMENT_DB_PATH"] = str(tmp_path_factory.mktemp("login_crypto") / "test.db")
    for name in list(sys.modules):
        if (name == "run" or name.startswith("routes.") or name == "store" or name.startswith("store.")
                or name.startswith("helpers.") or "sql_store" in name):
            del sys.modules[name]
    stub_qiita_db_and_core()
    import run
    import config
    ah.configure(config)
    return run.app


@pytest.fixture
def client(_app):
    return _app.test_client()


@pytest.fixture
def lc(_app):
    """The login_crypto module the running app uses."""
    import routes.auth_routes as ar
    return ar.login_crypto


def _post(client, path, body):
    return client.post(path, json=body, headers={"Origin": ah.TEST_ORIGIN})


def _flip(b64: str) -> str:
    raw = bytearray(base64.b64decode(b64))
    raw[-1] ^= 1
    return base64.b64encode(bytes(raw)).decode()


# ── GET /api/auth/login-key ─────────────────────────────────────────────────

class TestLoginKey:
    def test_public_key_and_fresh_challenge(self, client, lc):
        a, b = client.get("/api/auth/login-key"), client.get("/api/auth/login-key")
        assert a.status_code == 200 and a.headers["Cache-Control"] == "no-store"
        info = a.get_json()
        assert info["alg"] == "RSA-OAEP-256+A256GCM" and info["expires_in"] == lc.CHALLENGE_TTL_SECONDS
        assert len(info["key_id"]) == 16 and info["key_id"] == b.get_json()["key_id"]
        assert info["challenge"] != b.get_json()["challenge"]
        assert "PRIVATE" not in a.get_data(as_text=True)

    def test_key_file_is_private(self, client):
        import config
        client.get("/api/auth/login-key")
        assert stat.S_IMODE(os.stat(config.LOGIN_KEY_PATH).st_mode) == 0o600

    def test_unsafe_key_folder_refused(self, client, monkeypatch, tmp_path):
        import config
        shared = tmp_path / "shared"
        shared.mkdir()
        os.chmod(shared, 0o777)                         # another user could plant a key
        monkeypatch.setattr(config, "LOGIN_KEY_PATH", str(shared / "login_key.pem"))
        assert client.get("/api/auth/login-key").status_code == 503
        ah.fake_accounts(monkeypatch, ah.account("a@x.edu"))
        body = {"envelope": {"key_id": "x" * 16, "challenge": "c", "wrapped_key": "AA==", "iv": "AA==",
                             "ciphertext": "AA=="}}
        assert _post(client, "/api/auth/login", body).status_code == 400   # shape fails before the key is read


# ── The key itself ──────────────────────────────────────────────────────────

class TestKey:
    def test_concurrent_first_use_makes_one_key(self, lc, monkeypatch, tmp_path):
        import config
        path = tmp_path / "k" / "login_key.pem"
        path.parent.mkdir(mode=0o700)                    # load_key makes and checks the folder first
        monkeypatch.setattr(config, "LOGIN_KEY_PATH", str(path))
        ids, errors = [], []

        def _load():
            try:
                ids.append(lc._create(str(path)) or lc._parse(path.read_bytes())["key_id"])
            except Exception as exc:     # surfaced below
                errors.append(exc)
        threads = [threading.Thread(target=_load) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == [] and len(set(ids)) == 1
        assert sorted(os.listdir(path.parent)) == ["login_key.pem"]          # no stray temp files

    def test_key_file_others_can_read_is_refused(self, lc, monkeypatch, tmp_path):
        import config
        path = tmp_path / "k" / "login_key.pem"
        monkeypatch.setattr(config, "LOGIN_KEY_PATH", str(path))
        lc.load_key()
        os.chmod(path, 0o644)                    # e.g. hand-made with a umask-following tool
        with pytest.raises(lc.KeyUnavailable):
            lc.load_key()

    def test_symlinked_folder_or_key_refused(self, lc, monkeypatch, tmp_path):
        import config
        real = tmp_path / "real"
        real.mkdir(mode=0o700)
        (tmp_path / "link").symlink_to(real)
        monkeypatch.setattr(config, "LOGIN_KEY_PATH", str(tmp_path / "link" / "login_key.pem"))
        with pytest.raises(lc.KeyUnavailable):
            lc.load_key()
        monkeypatch.setattr(config, "LOGIN_KEY_PATH", str(real / "login_key.pem"))
        lc.load_key()
        (real / "alias.pem").symlink_to(real / "login_key.pem")
        monkeypatch.setattr(config, "LOGIN_KEY_PATH", str(real / "alias.pem"))
        with pytest.raises(lc.KeyUnavailable):
            lc.load_key()

    def test_encrypted_key_file_is_unavailable_not_a_crash(self, lc, monkeypatch, tmp_path):
        import config
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        folder = tmp_path / "k"
        folder.mkdir(mode=0o700)
        pem = rsa.generate_private_key(public_exponent=65537, key_size=3072).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"passphrase"))
        path = folder / "login_key.pem"
        path.write_bytes(pem)
        os.chmod(path, 0o600)
        monkeypatch.setattr(config, "LOGIN_KEY_PATH", str(path))
        with pytest.raises(lc.KeyUnavailable):
            lc.load_key()

    def test_follows_the_file_when_it_changes(self, lc, monkeypatch, tmp_path):
        import config
        path = tmp_path / "k" / "login_key.pem"
        monkeypatch.setattr(config, "LOGIN_KEY_PATH", str(path))
        first = lc.load_key()["key_id"]
        path.unlink()                                    # rotated: deleted, then made again on next use
        assert lc.load_key()["key_id"] != first


# ── Opening envelopes ───────────────────────────────────────────────────────

class TestEnvelopes:
    def test_round_trip(self, client, lc):
        assert lc.open_envelope(ah.seal(client, {"email": "a@x.edu", "password": "pw-123456"})) == {
            "email": "a@x.edu", "password": "pw-123456"}

    def test_replay_refused(self, client, lc):
        env = ah.seal(client, {"token": "qk_x"})
        lc.open_envelope(env)
        with pytest.raises(lc.EnvelopeInvalid):
            lc.open_envelope(env)

    def test_replay_just_before_expiry_cant_slip_past_pruning(self, client, lc, monkeypatch):
        """Review finding: expiry and pruning once used separate clock readings,
        so a replay sent just before expiry could be inserted after another
        sign-in pruned its row. Now both are one reading under the lock, and
        rows are kept well past expiry."""
        env = ah.seal(client, {"token": "qk_x"})
        lc.open_envelope(env)
        expires = lc._challenge_expiry(lc.load_key(), env["challenge"])
        limiter = lc.limiter                                # the very module login_crypto consumes through
        later = {"t": expires + 1}                          # another sign-in, just after expiry, prunes
        monkeypatch.setattr(limiter.time, "time", lambda: later["t"])
        limiter.consume_challenge("someone-else", later["t"] + 60)
        later["t"] = expires - 1                            # the replay is decided before its expiry
        assert limiter.consume_challenge(env["challenge"], expires) == "used"
        later["t"] = expires + 1
        assert limiter.consume_challenge("late", expires) == "expired"

    def test_expired_challenge(self, client, lc):
        key = lc.load_key()
        body = struct.pack(">Q", int(time.time()) - 1) + secrets.token_bytes(16)
        mac = hmac.new(key["hmac_key"], body, hashlib.sha256).digest()
        info = {**client.get("/api/auth/login-key").get_json(), "challenge": lc._b64u(body) + "." + lc._b64u(mac)}
        with pytest.raises(lc.EnvelopeExpired):
            lc.open_envelope(ah.seal(client, {"token": "qk_x"}, info=info))

    def test_sealed_to_a_rotated_key(self, client, lc, monkeypatch, tmp_path):
        import config
        env = ah.seal(client, {"token": "qk_x"})
        monkeypatch.setattr(config, "LOGIN_KEY_PATH", str(tmp_path / "k" / "new.pem"))
        with pytest.raises(lc.EnvelopeExpired):
            lc.open_envelope(env)

    @pytest.mark.parametrize("field", ["ciphertext", "iv", "wrapped_key"])
    def test_tampered_field(self, client, lc, field):
        env = ah.seal(client, {"token": "qk_x"})
        env[field] = _flip(env[field])
        with pytest.raises(lc.EnvelopeInvalid):
            lc.open_envelope(env)

    def test_ciphertext_bound_to_its_challenge(self, client, lc):
        """A valid, unused challenge from elsewhere doesn't fit: it's the AAD."""
        env = ah.seal(client, {"token": "qk_x"})
        env["challenge"] = client.get("/api/auth/login-key").get_json()["challenge"]
        with pytest.raises(lc.EnvelopeInvalid):
            lc.open_envelope(env)

    @pytest.mark.parametrize("mutate", [
        lambda e: e.update(challenge=e["challenge"][:-1] + ("B" if e["challenge"][-1] != "B" else "C")),  # forged
        lambda e: e.update(key_id="é" * 16),                                # non-hex key id (was a 500)
        lambda e: e.update(challenge="no-dot"),
        lambda e: e.update(iv=base64.b64encode(b"short").decode()),
        lambda e: e.update(wrapped_key="not base64!"),
        lambda e: e.pop("ciphertext"),
        lambda e: e.update(key_id=None),
    ])
    def test_malformed(self, client, lc, mutate):
        env = ah.seal(client, {"token": "qk_x"})
        mutate(env)
        with pytest.raises(lc.EnvelopeInvalid):
            lc.open_envelope(env)

    @pytest.mark.parametrize("envelope", [None, "x", [], 5])
    def test_not_an_object(self, lc, envelope):
        with pytest.raises(lc.EnvelopeInvalid):
            lc.open_envelope(envelope)

    def test_ciphertext_size_hides_the_password_length(self, client):
        short = ah.seal(client, {"email": "a@x.edu", "password": "p" * 8})
        long_ = ah.seal(client, {"email": "a@x.edu", "password": "p" * 900})
        assert len(base64.b64decode(short["ciphertext"])) == len(base64.b64decode(long_["ciphertext"]))

    def test_one_decrypt_per_worker_at_a_time(self, client, lc, monkeypatch):
        env = ah.seal(client, {"token": "qk_x"})
        monkeypatch.setattr(lc, "_DECRYPT_WAIT_SECONDS", 0.01)
        assert lc._decrypt_slot.acquire(timeout=1)
        try:
            with pytest.raises(lc.Busy):
                lc.open_envelope(env)
        finally:
            lc._decrypt_slot.release()
        assert lc.open_envelope(env) == {"token": "qk_x"}             # busy didn't use the challenge up

    def test_payload_must_be_an_object(self, client, lc):
        with pytest.raises(lc.EnvelopeInvalid):
            lc.open_envelope(ah.seal(client, ["qk_x"]))


# ── What the routes answer ──────────────────────────────────────────────────

class TestRoutes:
    def test_plain_secrets_refused(self, client, monkeypatch):
        ah.fake_accounts(monkeypatch, ah.account("a@x.edu"))
        calls = ah.fake_whoami(monkeypatch, {ah.token_for(1): ah.human(1, "a@x.edu")})
        login = _post(client, "/api/auth/login", {"email": "a@x.edu", "password": ah.TEST_PASSWORD})
        connect = _post(client, "/api/auth/connect", {"token": ah.token_for(1)})
        assert login.status_code == connect.status_code == 400
        assert "out of date" in login.get_json()["error"] and calls == []
        mixed = _post(client, "/api/auth/login", {"password": ah.TEST_PASSWORD,
                                                  "envelope": ah.seal(client, {"email": "a@x.edu"})})
        assert mixed.status_code == 400                                     # a plain field poisons the request

    def test_replayed_login_refused_and_not_counted(self, client, monkeypatch):
        import config
        import sqlite3
        ah.fake_accounts(monkeypatch, ah.account("replay@x.edu"))
        body = {"envelope": ah.seal(client, {"email": "replay@x.edu", "password": ah.TEST_PASSWORD})}
        assert _post(client, "/api/auth/login", body).status_code == 200
        assert _post(client, "/api/auth/login", body).status_code == 400
        attempts = sqlite3.connect(config.LIMITER_DB_PATH).execute("SELECT COUNT(1) FROM attempts").fetchone()[0]
        assert attempts == 0                                                # success cleared it; replay never counted

    def test_expired_is_409_with_flag(self, client, monkeypatch, tmp_path):
        import config
        env = ah.seal(client, {"token": ah.token_for(1)})
        monkeypatch.setattr(config, "LOGIN_KEY_PATH", str(tmp_path / "k" / "rotated.pem"))
        resp = _post(client, "/api/auth/connect", {"envelope": env})
        assert resp.status_code == 409 and resp.get_json()["expired"] is True

    def test_busy_is_503(self, client, monkeypatch):
        import routes.auth_routes as ar

        def _busy(envelope):
            raise ar.login_crypto.Busy()
        monkeypatch.setattr(ar.login_crypto, "open_envelope", _busy)
        resp = _post(client, "/api/auth/connect", {"envelope": {}})
        assert resp.status_code == 503 and resp.headers["Retry-After"]

    def test_non_hex_key_id_is_400_not_500(self, client):
        env = ah.seal(client, {"token": ah.token_for(1)})
        env["key_id"] = "é" * 16
        assert _post(client, "/api/auth/connect", {"envelope": env}).status_code == 400

    def test_single_use_store_down_fails_closed(self, client, monkeypatch, tmp_path):
        import config
        env = ah.seal(client, {"token": ah.token_for(1)})
        (tmp_path / "blocker").write_text("")
        monkeypatch.setattr(config, "LIMITER_DB_PATH", str(tmp_path / "blocker" / "limiter.db"))
        assert _post(client, "/api/auth/connect", {"envelope": env}).status_code == 503

    def test_secret_never_logged(self, client, monkeypatch, caplog):
        import logging
        caplog.set_level(logging.DEBUG)
        ah.fake_accounts(monkeypatch, ah.account("log@x.edu"))
        secret = "Never-In-Logs-77"
        ah.post_login(client, "log@x.edu", password=secret)
        body = {"envelope": ah.seal(client, {"email": "log@x.edu", "password": secret})}
        body["envelope"]["ciphertext"] = _flip(body["envelope"]["ciphertext"])
        _post(client, "/api/auth/login", body)
        assert secret not in caplog.text
