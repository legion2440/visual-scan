"""SQLite auth repository transaction and persistence contracts."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from app.features.auth.repository import SQLiteAuthRepository
from app.storage.database import SQLiteDatabase

NOW = datetime(2026, 7, 31, 10, 30, tzinfo=UTC)
FIRST_USER = UUID("11111111-1111-4111-8111-111111111111")
SECOND_USER = UUID("22222222-2222-4222-8222-222222222222")


def build_repository(path: Path) -> tuple[SQLiteDatabase, SQLiteAuthRepository]:
    database = SQLiteDatabase(database_path=path, busy_timeout_ms=5_000)
    database.bootstrap()
    return database, SQLiteAuthRepository(database)


def create_user(
    repository: SQLiteAuthRepository,
    *,
    user_id: UUID,
    username: str,
    token_byte: bytes,
) -> bool:
    _, is_initial = repository.create_user_with_session(
        user_id=user_id,
        username=username,
        password_hash="$argon2id$test-hash",
        created_at=NOW,
        token_hash=token_byte * 32,
        csrf_hash=token_byte.upper() * 32,
        expires_at=NOW + timedelta(days=7),
        replaced_token_hash=None,
    )
    return is_initial


def test_repository_elects_one_initial_user_and_stores_only_digests(tmp_path: Path) -> None:
    database, repository = build_repository(tmp_path / "auth-repository.db")

    assert create_user(repository, user_id=FIRST_USER, username="first-user", token_byte=b"a")
    assert not create_user(
        repository,
        user_id=SECOND_USER,
        username="second-user",
        token_byte=b"b",
    )

    with sqlite3.connect(database.database_path) as connection:
        users = connection.execute(
            "SELECT username, is_initial_user FROM users ORDER BY username"
        ).fetchall()
        sessions = connection.execute(
            "SELECT token_hash, csrf_hash FROM auth_sessions ORDER BY user_id"
        ).fetchall()

    assert users == [("first-user", 1), ("second-user", 0)]
    assert sessions == [(b"a" * 32, b"A" * 32), (b"b" * 32, b"B" * 32)]


def test_session_rotation_revokes_only_the_presented_session(tmp_path: Path) -> None:
    _, repository = build_repository(tmp_path / "session-rotation.db")
    create_user(repository, user_id=FIRST_USER, username="first-user", token_byte=b"a")
    repository.create_session(
        user_id=FIRST_USER,
        token_hash=b"b" * 32,
        csrf_hash=b"B" * 32,
        created_at=NOW,
        expires_at=NOW + timedelta(days=7),
        replaced_token_hash=None,
    )

    repository.create_session(
        user_id=FIRST_USER,
        token_hash=b"c" * 32,
        csrf_hash=b"C" * 32,
        created_at=NOW,
        expires_at=NOW + timedelta(days=7),
        replaced_token_hash=b"a" * 32,
    )

    assert repository.get_session(b"a" * 32) is None
    assert repository.get_session(b"b" * 32) is not None
    assert repository.get_session(b"c" * 32) is not None


def test_registration_rate_limit_consumption_is_atomic(tmp_path: Path) -> None:
    _, repository = build_repository(tmp_path / "registration-rate.db")
    key_hash = b"r" * 32

    for _ in range(5):
        assert (
            repository.consume_registration_attempt(
                key_hash=key_hash,
                now=NOW,
                limit=5,
                window_seconds=3_600,
            )
            == 0
        )

    assert (
        repository.consume_registration_attempt(
            key_hash=key_hash,
            now=NOW,
            limit=5,
            window_seconds=3_600,
        )
        == 3_600
    )
