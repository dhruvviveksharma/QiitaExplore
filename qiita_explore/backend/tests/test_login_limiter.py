"""Unit tests for helpers/login_limiter.py (brute-force limits) and
helpers/qiita_accounts.py (email/password rules, the bcrypt check, the
account-read circuit breaker). The login route's use of both: test_auth.py."""
import threading

import pytest

from . import auth_helpers as ah


@pytest.fixture
def lim():
    from helpers import login_limiter
    return login_limiter


@pytest.fixture
def acc():
    from helpers import qiita_accounts
    return qiita_accounts


def _clock(monkeypatch, lim, start=1_000_000.0):
    now = {"t": start}
    monkeypatch.setattr(lim.time, "time", lambda: now["t"])
    return now


# ── login_limiter ────────────────────────────────────────────────────────────

class TestLimiter:
    def test_five_free_then_growing_delay(self, lim, monkeypatch):
        now = _clock(monkeypatch, lim)
        assert [lim.reserve("a@x.edu", None, None) for _ in range(6)] == [(True, 0, False)] * 6
        assert lim.reserve("a@x.edu", None, None) == (False, 2, False)      # 2^(6-5)
        now["t"] += 2
        assert lim.reserve("a@x.edu", None, None) == (True, 0, False)       # 7th counted
        assert lim.reserve("a@x.edu", None, None) == (False, 4, False)      # 2^(7-5)

    def test_delay_is_capped(self, lim, monkeypatch):
        now = _clock(monkeypatch, lim)
        for _ in range(30):
            allowed, wait, _ = lim.reserve("a@x.edu", None, None)
            if not allowed:
                assert wait <= lim.MAX_DELAY_SECONDS
                now["t"] += wait

    def test_window_resets(self, lim, monkeypatch):
        now = _clock(monkeypatch, lim)
        for _ in range(6):
            lim.reserve("a@x.edu", None, None)
        now["t"] += lim.WINDOW_SECONDS + 1
        assert [lim.reserve("a@x.edu", None, None)[0] for _ in range(6)] == [True] * 6

    def test_known_device_skips_email_and_client_limits(self, lim, monkeypatch):
        _clock(monkeypatch, lim)
        device = lim.succeeded("a@x.edu", None, None, False)
        assert device
        for i in range(lim.CLIENT_MAX_ATTEMPTS + 8):                  # an attacker on the same NAT IP
            lim.reserve("a@x.edu" if i % 2 else f"z{i}@x.edu", "203.0.113.9", None)
        assert lim.reserve("a@x.edu", "203.0.113.9", None)[0] is False
        assert lim.reserve("a@x.edu", "203.0.113.9", device) == (True, 0, True)
        assert lim.reserve("b@x.edu", None, device)[2] is False        # a device is per account

    def test_device_gets_ten_failures_then_normal_limits(self, lim, monkeypatch):
        """A shared workstation's next user can't guess forever from the device."""
        _clock(monkeypatch, lim)
        device = lim.succeeded("a@x.edu", None, None, False)
        assert [lim.reserve("a@x.edu", None, device)[2] for _ in range(lim.DEVICE_MAX_FAILURES)] == \
            [True] * lim.DEVICE_MAX_FAILURES
        results = [lim.reserve("a@x.edu", None, device) for _ in range(7)]
        assert all(not via for _, _, via in results)
        assert results[-1][0] is False                                 # back under the email delay

    def test_success_resets_the_device_and_reuses_its_token(self, lim, monkeypatch):
        _clock(monkeypatch, lim)
        device = lim.succeeded("a@x.edu", None, None, False)
        for _ in range(lim.DEVICE_MAX_FAILURES - 1):
            lim.reserve("a@x.edu", None, device)
        assert lim.reserve("a@x.edu", None, device)[2] is True
        assert lim.succeeded("a@x.edu", device, None, True) is None
        assert all(lim.reserve("a@x.edu", None, device)[2] for _ in range(lim.DEVICE_MAX_FAILURES))

    def test_worn_out_device_is_replaced_on_success(self, lim, monkeypatch):
        _clock(monkeypatch, lim)
        device = lim.succeeded("a@x.edu", None, None, False)
        for _ in range(lim.DEVICE_MAX_FAILURES):
            lim.reserve("a@x.edu", None, device)
        new = lim.succeeded("a@x.edu", device, None, False)
        assert new not in (None, device)
        assert lim.reserve("a@x.edu", None, new)[2] is True

    def test_client_limit_counts_failures_only(self, lim, monkeypatch):
        _clock(monkeypatch, lim)
        ip = "203.0.113.9"
        for i in range(lim.CLIENT_MAX_ATTEMPTS + 5):                  # a busy lab: all successes
            assert lim.reserve(f"u{i}@x.edu", ip, None)[0] is True
            lim.succeeded(f"u{i}@x.edu", None, ip, False)
        for i in range(lim.CLIENT_MAX_ATTEMPTS):                      # then 30 failures
            assert lim.reserve(f"f{i}@x.edu", ip, None)[0] is True
        assert lim.reserve("fresh@x.edu", ip, None) == (False, lim.CLIENT_BLOCK_SECONDS, False)
        assert lim.reserve("fresh@x.edu", "198.51.100.1", None)[0] is True
        assert lim.reserve("fresh2@x.edu", None, None)[0] is True       # no IP header: no client limit

    def test_success_lifts_a_block_its_own_attempt_set(self, lim, monkeypatch):
        _clock(monkeypatch, lim)
        ip = "203.0.113.9"
        for i in range(lim.CLIENT_MAX_ATTEMPTS - 1):
            lim.reserve(f"f{i}@x.edu", ip, None)
        assert lim.reserve("real@x.edu", ip, None)[0] is True          # the 30th: sets the block
        lim.succeeded("real@x.edu", None, ip, False)                   # ...but it was a success
        assert lim.reserve("next@x.edu", ip, None)[0] is True

    def test_parallel_reservations_are_all_counted(self, lim):
        results = []
        threads = [threading.Thread(target=lambda: results.append(lim.reserve("p@x.edu", None, None)[0]))
                   for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 12
        assert results.count(True) == lim.EMAIL_FREE_ATTEMPTS + 1     # none slipped past the count
        assert lim.reserve("p@x.edu", None, None)[0] is False

    def test_keys_are_hashed_and_file_private(self, lim):
        import config
        import os
        lim.reserve("secret.person@x.edu", "203.0.113.9", None)
        lim.succeeded("other.person@x.edu", None, "203.0.113.9", False)
        raw = open(config.LIMITER_DB_PATH, "rb").read()
        assert b"secret.person" not in raw and b"other.person" not in raw and b"203.0.113.9" not in raw
        assert os.stat(config.LIMITER_DB_PATH).st_mode & 0o077 == 0

    def test_unusable_or_shared_storage_raises(self, lim, monkeypatch, tmp_path):
        import config
        import os
        blocker = tmp_path / "blocker"
        blocker.write_text("")
        monkeypatch.setattr(config, "LIMITER_DB_PATH", str(blocker / "l.db"))   # parent is a file
        with pytest.raises(lim.LimiterUnavailable):
            lim.reserve("a@x.edu", None, None)
        with pytest.raises(lim.LimiterUnavailable):
            lim.succeeded("a@x.edu", None, None, False)
        shared = tmp_path / "shared"
        shared.mkdir()
        os.chmod(shared, 0o777)                                        # another user could plant a file
        monkeypatch.setattr(config, "LIMITER_DB_PATH", str(shared / "l.db"))
        with pytest.raises(lim.LimiterUnavailable):
            lim.reserve("a@x.edu", None, None)
        own = tmp_path / "own"
        own.mkdir(mode=0o700)
        planted = own / "l.db"
        planted.write_text("")
        os.chmod(planted, 0o666)
        monkeypatch.setattr(config, "LIMITER_DB_PATH", str(planted))
        with pytest.raises(lim.LimiterUnavailable):
            lim.reserve("a@x.edu", None, None)


# ── qiita_accounts ───────────────────────────────────────────────────────────

class TestAccountRules:
    @pytest.mark.parametrize("raw,expected", [
        ("  Alice@Example.EDU ", "alice@example.edu"),
        ("a@b", "a@b"),
        ("no-at-sign", None), ("two@@x.edu", None), ("a@b@c", None), ("", None), (None, None), (7, None),
        ("K@x.edu", None),           # Kelvin sign: lowercases to 'k' — rejected before lowering
        ("ü@x.edu", None),
        ("a" * 250 + "@x.edu", None),
    ])
    def test_normalize_email(self, acc, raw, expected):
        assert acc.normalize_email(raw) == expected

    @pytest.mark.parametrize("pw,ok", [
        ("12345678", True), ("1234567", False), ("has space1", False), ("back\\slash", False),
        ("pässwörd12", False), ("x" * 1024, True), ("x" * 1025, False), (None, False), (12345678, False),
    ])
    def test_password_format(self, acc, pw, ok):
        assert acc.valid_password_format(pw) is ok

    def test_check_password(self, acc):
        h = ah.password_hash("right-password")
        assert acc.check_password("right-password", h) is True
        assert acc.check_password("wrong-password", h) is False

    def test_72_byte_boundary_matches_classic_qiita(self, acc):
        """bcrypt 4 (and classic Qiita) only use the first 72 bytes."""
        base = "p" * 72
        h = ah.password_hash(base)
        assert acc.check_password(base + "anything-after", h) is True
        assert acc.check_password("p" * 71, h) is False

    @pytest.mark.parametrize("bad", ["", "plaintext", "$1$abc", "$2b$12$short", None])
    def test_malformed_stored_hash_is_false(self, acc, bad, monkeypatch):
        monkeypatch.setitem(acc._dummy, "hash", ah.password_hash("dummy").encode())
        assert acc.check_password("whatever-pass", bad) is False

    def test_busy_when_this_worker_is_already_checking(self, acc, monkeypatch):
        monkeypatch.setattr(acc, "_BCRYPT_WAIT_SECONDS", 0.01)
        assert acc._bcrypt_slot.acquire(timeout=1)
        try:
            with pytest.raises(acc.Busy):
                acc.check_password("x" * 8, ah.password_hash())
            with pytest.raises(acc.Busy):
                acc.burn_check("x" * 8)
        finally:
            acc._bcrypt_slot.release()

    def test_unverified(self, acc):
        assert acc.Account("a", "h", 5, "unverified").unverified
        assert acc.Account("a", "h", 9, "unverified").unverified
        assert not acc.Account("a", "h", 3, "user").unverified

    def test_fingerprint(self, acc):
        a, b = acc.password_fingerprint("$2b$hash-a"), acc.password_fingerprint("$2b$hash-b")
        assert a != b and len(a) == 64 and "hash-a" not in a
        assert acc.same_fingerprint(a, a) and not acc.same_fingerprint(a, b)
        assert not acc.same_fingerprint(None, a) and not acc.same_fingerprint("", "")


class TestBreaker:
    def test_three_failures_open_it_then_it_recovers(self, acc, monkeypatch):
        import psycopg2
        calls = {"n": 0}

        def _broken_pool():
            calls["n"] += 1
            raise psycopg2.OperationalError("no route to host")
        monkeypatch.setattr(acc, "_get_pool", _broken_pool)
        monkeypatch.setattr(acc, "_breaker", {"fails": 0, "open_until": 0.0})
        mono = {"t": 100.0}
        monkeypatch.setattr(acc.time, "monotonic", lambda: mono["t"])
        for _ in range(3):
            with pytest.raises(acc.AccountsUnavailable):
                acc.fetch_account("a@x.edu")
        with pytest.raises(acc.AccountsUnavailable):
            acc.fetch_account("a@x.edu")
        assert calls["n"] == 3                         # open: failed fast, no connect attempt
        mono["t"] += acc._BREAKER_OPEN_SECONDS + 1
        with pytest.raises(acc.AccountsUnavailable):
            acc.fetch_account("a@x.edu")
        assert calls["n"] == 4                         # half-open: tried again
