"""Password hashing, opaque tokens, and domain-separated digests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from pwdlib import PasswordHash

# A fixed valid Argon2id hash keeps the unknown-user path expensive without
# performing heavy hashing at import time or once per failed login.
DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$wagCPXjifgvUFBzq4hqe3w$"
    "CYaIb8sB+wtD+Vu/P4uod1+Qof8h+1g7bbDlBID48Rc"
)


class AuthSecurity:
    """Centralize cryptographic operations used by authentication."""

    def __init__(self, hmac_secret: str) -> None:
        self._password_hash = PasswordHash.recommended()
        self._hmac_secret = hmac_secret.encode("utf-8")

    def hash_password(self, password: str) -> str:
        return self._password_hash.hash(password)

    def verify_password(self, password: str, password_hash: str) -> tuple[bool, str | None]:
        return self._password_hash.verify_and_update(password, password_hash)

    def verify_dummy_password(self, password: str) -> None:
        self._password_hash.verify(password, DUMMY_PASSWORD_HASH)

    @staticmethod
    def new_session_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def token_digest(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    def csrf_token(self, session_token: str) -> str:
        digest = hmac.new(
            self._hmac_secret,
            b"visual-scan:csrf:v1\x00" + session_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def rate_limit_key(self, scope: str, value: str) -> bytes:
        return hmac.new(
            self._hmac_secret,
            b"visual-scan:rate-limit:v1\x00"
            + scope.encode("ascii")
            + b"\x00"
            + value.encode("utf-8"),
            hashlib.sha256,
        ).digest()

    @staticmethod
    def verify_digest(value: str, expected: bytes) -> bool:
        actual = hashlib.sha256(value.encode("utf-8")).digest()
        return hmac.compare_digest(actual, expected)
