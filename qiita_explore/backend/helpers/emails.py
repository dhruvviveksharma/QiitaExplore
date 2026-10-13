"""The one email rule both logins and the users migration share: the email is
how classic Qiita and qiita-miint reach the same account (store/auth_store.py
resolve_user), so they must normalize it identically. No dependencies, so the
store can use it without loading the login machinery."""

_MAX_EMAIL = 254


def normalize_email(raw):
    """Classic Qiita's web login form: strip, then lowercase. Non-ASCII is
    rejected *before* lowercasing (Qiita only stores ASCII emails, and e.g. the
    Kelvin sign lowercases to 'k'), as are inner whitespace and control
    characters. None when it can't be a Qiita email."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if (not s or len(s) > _MAX_EMAIL or not s.isascii() or s.count("@") != 1
            or any(c.isspace() or not c.isprintable() for c in s)):
        return None
    return s.lower()
