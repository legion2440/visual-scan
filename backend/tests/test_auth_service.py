"""Authentication service timing, hashing, tokens, and rate-limit invariants."""

from __future__ import annotations

import sqlite3
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.features.auth.errors import AuthRateLimitError, InvalidCredentialsError
from app.features.auth.repository import SQLiteAuthRepository
from app.features.auth.schemas import AuthenticatedPrincipal, CredentialsRequest
from app.features.auth.security import AuthSecurity
from app.features.auth.service import AuthService
from app.storage.database import SQLiteDatabase

START = datetime(2026, 7, 31, 10, 30, tzinfo=UTC)
PASSWORD = "  exact password value  "


class MutableClock:
    def __init__(self) -> None:
        self.value = START

    def __call__(self) -> datetime:
        return self.value


def build_service(
    path: Path,
    *,
    clock: MutableClock | None = None,
    absolute: int = 100,
    idle: int = 20,
    touch: int = 5,
) -> tuple[AuthService, SQLiteDatabase, AuthSecurity, MutableClock]:
    database = SQLiteDatabase(database_path=path, busy_timeout_ms=5_000)
    database.bootstrap()
    security = AuthSecurity("test-auth-secret-with-more-than-thirty-two-bytes")
    auth_clock = clock or MutableClock()
    service = AuthService(
        SQLiteAuthRepository(database),
        security,
        absolute_lifetime_seconds=absolute,
        idle_lifetime_seconds=idle,
        touch_interval_seconds=touch,
        clock=auth_clock,
    )
    return service, database, security, auth_clock


def credentials(username: str = "nazar", password: str = PASSWORD) -> CredentialsRequest:
    return CredentialsRequest(username=username, password=password)


def test_password_hashes_tokens_and_rate_keys_never_store_raw_values(tmp_path: Path) -> None:
    service, database, security, _ = build_service(tmp_path / "auth.db")
    outcome = service.register(
        credentials(),
        remote_address="203.0.113.7",
        current_session_token=None,
    )

    with sqlite3.connect(database.database_path) as connection:
        password_hash = connection.execute("SELECT password_hash FROM users").fetchone()[0]
        token_hash, csrf_hash = connection.execute(
            "SELECT token_hash, csrf_hash FROM auth_sessions"
        ).fetchone()
        rate_key = connection.execute("SELECT key_hash FROM auth_rate_limits").fetchone()[0]

    assert PASSWORD not in password_hash
    assert security.verify_password(PASSWORD, password_hash)[0] is True
    assert bytes(token_hash) == security.token_digest(outcome.session_token)
    assert bytes(csrf_hash) == security.token_digest(outcome.csrf_token)
    assert outcome.session_token.encode() not in bytes(token_hash)
    assert outcome.csrf_token.encode() not in bytes(csrf_hash)
    assert b"203.0.113.7" not in bytes(rate_key)
    assert len(bytes(rate_key)) == 32


def test_password_is_preserved_exactly_and_unknown_user_runs_dummy_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, security, _ = build_service(tmp_path / "exact.db")
    service.register(credentials(), remote_address="one", current_session_token=None)
    assert (
        service.login(
            credentials(), remote_address="two", current_session_token=None
        ).principal.username
        == "nazar"
    )
    with pytest.raises(InvalidCredentialsError):
        service.login(
            credentials(password=PASSWORD.strip()),
            remote_address="three",
            current_session_token=None,
        )

    calls: list[str] = []
    original = security.verify_dummy_password

    def spy(password: str) -> None:
        calls.append(password)
        original(password)

    monkeypatch.setattr(security, "verify_dummy_password", spy)
    with pytest.raises(InvalidCredentialsError):
        service.login(
            credentials(username="unknown-user"),
            remote_address="four",
            current_session_token=None,
        )
    assert calls == [PASSWORD]


def test_idle_absolute_expiry_and_touch_interval(tmp_path: Path) -> None:
    service, database, _, clock = build_service(tmp_path / "timing.db")
    outcome = service.register(credentials(), remote_address="one", current_session_token=None)

    clock.value = START + timedelta(seconds=4)
    assert service.resolve_session(outcome.session_token).session is not None
    with sqlite3.connect(database.database_path) as connection:
        unchanged = connection.execute("SELECT last_seen_at FROM auth_sessions").fetchone()[0]
    assert unchanged == "2026-07-31T10:30:00.000000Z"

    clock.value = START + timedelta(seconds=6)
    assert service.resolve_session(outcome.session_token).session is not None
    with sqlite3.connect(database.database_path) as connection:
        touched = connection.execute("SELECT last_seen_at FROM auth_sessions").fetchone()[0]
    assert touched == "2026-07-31T10:30:06.000000Z"

    clock.value = START + timedelta(seconds=27)
    expired = service.resolve_session(outcome.session_token)
    assert expired.session is None

    second = service.login(credentials(), remote_address="two", current_session_token=None)
    clock.value += timedelta(seconds=101)
    assert service.resolve_session(second.session_token).session is None


def test_public_principal_contains_only_identity_fields() -> None:
    assert [field.name for field in fields(AuthenticatedPrincipal)] == [
        "user_id",
        "username",
        "created_at",
        "is_initial_user",
    ]


def test_login_account_bucket_blocks_after_five_failures(tmp_path: Path) -> None:
    service, _, _, _ = build_service(tmp_path / "rate.db")
    service.register(credentials(), remote_address="register", current_session_token=None)
    wrong = credentials(password="wrong password value")
    for index in range(5):
        with pytest.raises(InvalidCredentialsError):
            service.login(wrong, remote_address=f"ip-{index}", current_session_token=None)
    with pytest.raises(AuthRateLimitError) as captured:
        service.login(wrong, remote_address="another-ip", current_session_token=None)
    assert captured.value.retry_after == 900


def test_registration_bucket_allows_five_attempts_then_returns_retry_after(tmp_path: Path) -> None:
    service, _, _, _ = build_service(tmp_path / "register-rate.db")
    for index in range(5):
        result = service.register(
            credentials(username=f"user-{index}"),
            remote_address="same-ip",
            current_session_token=None,
        )
        assert result.principal.is_initial_user is (index == 0)
    with pytest.raises(AuthRateLimitError) as captured:
        service.register(
            credentials(username="user-six"),
            remote_address="same-ip",
            current_session_token=None,
        )
    assert captured.value.retry_after == 3600
