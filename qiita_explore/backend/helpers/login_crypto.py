"""Sign-in secrets sealed in the browser, readable only here.

The classic-Qiita password and the Qiita-Miint token travel through Cloudflare
(where TLS ends) and kl-remote (where Caddy reads each request to proxy it),
both of which would otherwise see them in plain text. So the browser seals them
to an RSA key only this backend holds (frontend/js/auth.js sealForServer): a
fresh AES-256-GCM key encrypts the JSON payload, with a one-time challenge from
GET /api/auth/login-key as associated data, and RSA-OAEP-SHA256 wraps that key.

That stops passive exposure at those hops — logs, captures, memory — and replay
of a captured request once it has reached this backend (each challenge works
once, for CHALLENGE_TTL_SECONDS). It can't stop someone who can change what
those hops serve: the page and its script come through them too.
docs/02-authentication.md.
"""
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import struct
import threading
import time

from cryptography.exceptions import InvalidTag, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import config
from helpers import login_limiter as limiter

ALG = "RSA-OAEP-256+A256GCM"
CHALLENGE_TTL_SECONDS = 120
_KEY_BITS = 3072
_WRAPPED_LEN = _KEY_BITS // 8
_IV_LEN = 12
_MAX_CIPHERTEXT = 4096
_KEY_ID_RE = re.compile(r"[0-9a-f]{16}")
_OAEP = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


class KeyUnavailable(Exception):
    """The key file can't be made or read safely; sign-in answers 503."""


class EnvelopeExpired(Exception):
    """The challenge ran out, or the key changed since the page fetched it: the
    browser fetches a fresh one and tries again."""


class EnvelopeInvalid(Exception):
    """Malformed, tampered with, or replayed."""


class Busy(Exception):
    """This worker is already opening an envelope; try again in a moment."""


# ── the key ─────────────────────────────────────────────────────────────────

_cache = {"stat": None, "key": None}
_lock = threading.Lock()


def load_key() -> dict:
    """{private, spki, key_id, hmac_key} for the key on disk, created on first
    use. Checked on every call, on the opened file itself (no symlinks followed,
    regular file, this user's, mode 0600) in a real folder only this user can
    write; re-parsed whenever it changes, so every gunicorn worker follows the
    same key even after it is rotated (deleted) or recreated. Raises KeyUnavailable."""
    path = config.LOGIN_KEY_PATH
    folder = os.path.dirname(path) or "."
    with _lock:
        try:
            os.makedirs(folder, mode=0o700, exist_ok=True)
            st = os.lstat(folder)
            if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022):
                raise KeyUnavailable(f"{folder} must be a real folder owned by this user, not group/world-writable")
            if not os.path.lexists(path):
                _create(path)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                    raise KeyUnavailable(f"{path} must be a regular file only this user can read (mode 0600)")
                stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
                if _cache["stat"] != stamp:
                    with os.fdopen(os.dup(fd), "rb") as f:
                        _cache["key"] = _parse(f.read())
                    _cache["stat"] = stamp
            finally:
                os.close(fd)
        except (OSError, ValueError, TypeError, UnsupportedAlgorithm) as exc:
            raise KeyUnavailable(str(exc)) from exc
        return _cache["key"]


def _create(path):
    """Write a new key beside `path`, then link it into place: os.link never
    overwrites, so when two workers race, the loser's key is simply discarded
    and nobody ever reads a half-written file. The folder is checked by the caller."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=_KEY_BITS)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
    finally:
        os.unlink(tmp)


def _parse(pem: bytes) -> dict:
    private = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(private, rsa.RSAPrivateKey) or private.key_size != _KEY_BITS:
        raise ValueError("the login key must be an RSA-3072 key")
    spki = private.public_key().public_bytes(serialization.Encoding.DER,
                                             serialization.PublicFormat.SubjectPublicKeyInfo)
    der = private.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return {
        "private": private,
        "spki": spki,
        "key_id": hashlib.sha256(spki).hexdigest()[:16],
        # Challenges are signed with a key derived from the private key: no second secret to keep.
        "hmac_key": hashlib.sha256(b"qiita-explore login challenge\0" + der).digest(),
    }


# ── challenges ──────────────────────────────────────────────────────────────

def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def public_info() -> dict:
    """What GET /api/auth/login-key answers: the public key and a fresh,
    stateless challenge (expiry + randomness, signed). Raises KeyUnavailable."""
    key = load_key()
    body = struct.pack(">Q", int(time.time()) + CHALLENGE_TTL_SECONDS) + secrets.token_bytes(16)
    mac = hmac.new(key["hmac_key"], body, hashlib.sha256).digest()
    return {
        "key_id": key["key_id"],
        "public_key": base64.b64encode(key["spki"]).decode(),
        "alg": ALG,
        "challenge": _b64u(body) + "." + _b64u(mac),
        "expires_in": CHALLENGE_TTL_SECONDS,
    }


def _challenge_expiry(key: dict, challenge: str) -> int:
    body_s, dot, mac_s = challenge.partition(".")
    try:
        body, mac = _unb64u(body_s), _unb64u(mac_s)
    except (binascii.Error, ValueError):
        raise EnvelopeInvalid() from None
    if not dot or len(body) != 24 or not hmac.compare_digest(
            mac, hmac.new(key["hmac_key"], body, hashlib.sha256).digest()):
        raise EnvelopeInvalid()
    expires = struct.unpack(">Q", body[:8])[0]
    if expires < time.time():
        raise EnvelopeExpired()
    return expires


# ── opening an envelope ─────────────────────────────────────────────────────

# One RSA decrypt per worker at a time (it holds the GIL, so it would stall the
# worker's other thread too): a second request waits at most 0.5 s, then Busy.
_decrypt_slot = threading.Semaphore(1)
_DECRYPT_WAIT_SECONDS = 0.5

def _field(envelope: dict, name: str) -> bytes:
    value = envelope.get(name)
    if not isinstance(value, str) or len(value) > 8192:
        raise EnvelopeInvalid()
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise EnvelopeInvalid() from None


def open_envelope(envelope) -> dict:
    """The JSON object the browser sealed, cheapest checks first: shape, key,
    challenge signature and expiry, single use, then RSA and AES-GCM.
    Raises EnvelopeExpired, EnvelopeInvalid, Busy, KeyUnavailable, or
    login_limiter.LimiterUnavailable (the single-use record can't be kept)."""
    if not isinstance(envelope, dict):
        raise EnvelopeInvalid()
    key_id, challenge = envelope.get("key_id"), envelope.get("challenge")
    if (not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id)
            or not isinstance(challenge, str) or len(challenge) > 256):
        raise EnvelopeInvalid()
    wrapped, iv, ciphertext = (_field(envelope, n) for n in ("wrapped_key", "iv", "ciphertext"))
    if len(wrapped) != _WRAPPED_LEN or len(iv) != _IV_LEN or not 16 < len(ciphertext) <= _MAX_CIPHERTEXT:
        raise EnvelopeInvalid()
    key = load_key()
    if not hmac.compare_digest(key_id, key["key_id"]):
        raise EnvelopeExpired()                 # sealed to an older key: the page refetches
    expires = _challenge_expiry(key, challenge)
    if not _decrypt_slot.acquire(timeout=_DECRYPT_WAIT_SECONDS):
        raise Busy()                            # before using the challenge up: a retry can reuse it
    try:
        outcome = limiter.consume_challenge(challenge, expires)
        if outcome == "expired":                # ran out while waiting for the lock
            raise EnvelopeExpired()
        if outcome != "ok":
            raise EnvelopeInvalid()             # replayed
        aes_key = key["private"].decrypt(wrapped, _OAEP)
        if len(aes_key) != 32:
            raise EnvelopeInvalid()
        payload = json.loads(AESGCM(aes_key).decrypt(iv, ciphertext, challenge.encode()))
    except (ValueError, InvalidTag, UnicodeDecodeError):
        raise EnvelopeInvalid() from None
    finally:
        _decrypt_slot.release()
    if not isinstance(payload, dict):
        raise EnvelopeInvalid()
    return payload
