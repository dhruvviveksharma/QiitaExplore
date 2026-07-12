"""Fernet encryption for Qiita PATs stored in auth_sessions.pat_encrypted.

The key lives outside SQLite (env var) so a leaked DB file/backup doesn't also
leak long-lived (default 90-day) Qiita bearer credentials.
"""

from cryptography.fernet import Fernet, InvalidToken

import config

_KEY_ENV_VAR = "QIITA_EXPLORE_PAT_ENCRYPTION_KEY"


class PatCryptoError(Exception):
    pass


def _get_fernet() -> Fernet:
    key = config.PAT_ENCRYPTION_KEY
    if not key:
        raise PatCryptoError(
            f"{_KEY_ENV_VAR} is not set. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and set it in the "
            "environment before starting the backend."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        raise PatCryptoError(f"{_KEY_ENV_VAR} is not a valid Fernet key") from exc


def encrypt_pat(pat: str) -> str:
    return _get_fernet().encrypt(pat.encode()).decode()


def decrypt_pat(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise PatCryptoError("PAT ciphertext could not be decrypted (wrong key or corrupted)") from exc
