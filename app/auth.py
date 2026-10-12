"""Admin cookie and form token. The password itself stays in the environment."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from pathlib import Path

ADMIN_COOKIE = "spellbot_admin"
CSRF_COOKIE = "spellbot_csrf"
COOKIE_TTL = 14 * 24 * 60 * 60
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{16,80}")


def load_secret(path: Path) -> bytes:
    if path.exists():
        data = path.read_bytes()
        if len(data) >= 16:
            return data
    path.parent.mkdir(parents=True, exist_ok=True)
    data = secrets.token_bytes(32)
    path.write_bytes(data)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return data


def password_matches(expected: str, given: str) -> bool:
    actual = hashlib.sha256(expected.encode()).digest()
    offered = hashlib.sha256(given.encode()).digest()
    return hmac.compare_digest(actual, offered)


def _password_tag(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()[:20]


def make_admin_cookie(secret: bytes, password: str, now_ts: float) -> str:
    expiry = int(now_ts) + COOKIE_TTL
    message = f"{expiry}.{_password_tag(password)}".encode()
    signature = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return f"{expiry}.{signature}"


def admin_cookie_valid(secret: bytes, password: str, value: str, now_ts: float) -> bool:
    expiry_text, separator, signature = value.partition(".")
    if not separator or not expiry_text.isdigit() or not signature:
        return False
    expiry = int(expiry_text)
    if expiry < int(now_ts):
        return False
    message = f"{expiry}.{_password_tag(password)}".encode()
    expected = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def valid_csrf_token(value: str | None) -> bool:
    return bool(value) and _TOKEN_RE.fullmatch(value or "") is not None


def csrf_matches(cookie_value: str | None, form_value: str | None) -> bool:
    if not valid_csrf_token(cookie_value) or not valid_csrf_token(form_value):
        return False
    return hmac.compare_digest(cookie_value or "", form_value or "")


class LoginGuard:
    """Slow down repeated wrong passwords on the admin form."""

    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}

    def blocked(self, ip: str, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        recent = [item for item in self._fails.get(ip, []) if moment - item < 300]
        self._fails[ip] = recent
        return len(recent) >= 8

    def fail(self, ip: str, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        self._fails.setdefault(ip, []).append(moment)

    def clear(self, ip: str) -> None:
        self._fails.pop(ip, None)
