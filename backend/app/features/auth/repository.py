"""SQLite persistence for users, sessions, and authentication rate limits."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import UUID

from app.features.auth.errors import (
    AuthStorageUnavailableError,
    UsernameAlreadyExistsError,
)
from app.storage.database import SQLiteDatabase


def _to_storage(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _from_storage(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Stored authentication timestamp is not timezone-aware.")
    return timestamp.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class StoredUser:
    id: UUID
    username: str
    password_hash: str
    created_at: datetime
    is_active: bool
    is_initial_user: bool


@dataclass(frozen=True, slots=True)
class StoredSession:
    token_hash: bytes
    csrf_hash: bytes
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    user: StoredUser


class SQLiteAuthRepository:
    """Open one shared-policy SQLite connection per auth operation."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def create_user_with_session(
        self,
        *,
        user_id: UUID,
        username: str,
        password_hash: str,
        created_at: datetime,
        token_hash: bytes,
        csrf_hash: bytes,
        expires_at: datetime,
        replaced_token_hash: bytes | None,
    ) -> tuple[StoredUser, bool]:
        """Atomically elect the initial user and create its first session."""
        try:
            with (
                self._database.connection() as connection,
                self._database.transaction(connection, immediate=True),
            ):
                is_initial = connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None
                connection.execute(
                    """
                    INSERT INTO users (
                        id, username, password_hash, created_at, is_active, is_initial_user
                    ) VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (
                        str(user_id),
                        username,
                        password_hash,
                        _to_storage(created_at),
                        int(is_initial),
                    ),
                )
                if replaced_token_hash is not None:
                    connection.execute(
                        "DELETE FROM auth_sessions WHERE token_hash = ?",
                        (replaced_token_hash,),
                    )
                self._insert_session(
                    connection,
                    token_hash=token_hash,
                    user_id=user_id,
                    csrf_hash=csrf_hash,
                    created_at=created_at,
                    expires_at=expires_at,
                )
            return (
                StoredUser(
                    id=user_id,
                    username=username,
                    password_hash=password_hash,
                    created_at=created_at,
                    is_active=True,
                    is_initial_user=is_initial,
                ),
                is_initial,
            )
        except sqlite3.IntegrityError as error:
            if "users.username" in str(error) or "UNIQUE constraint failed: users.username" in str(
                error
            ):
                raise UsernameAlreadyExistsError() from error
            raise AuthStorageUnavailableError() from error
        except (OSError, sqlite3.Error, ValueError) as error:
            raise AuthStorageUnavailableError() from error

    def get_user_by_username(self, username: str) -> StoredUser | None:
        try:
            with self._database.connection() as connection, self._database.transaction(connection):
                row = connection.execute(
                    "SELECT * FROM users WHERE username = ?",
                    (username,),
                ).fetchone()
            return self._map_user(row) if row is not None else None
        except (OSError, sqlite3.Error, ValueError) as error:
            raise AuthStorageUnavailableError() from error

    def update_password_hash(self, user_id: UUID, password_hash: str) -> None:
        try:
            with (
                self._database.connection() as connection,
                self._database.transaction(connection, immediate=True),
            ):
                connection.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (password_hash, str(user_id)),
                )
        except (OSError, sqlite3.Error) as error:
            raise AuthStorageUnavailableError() from error

    def create_session(
        self,
        *,
        user_id: UUID,
        token_hash: bytes,
        csrf_hash: bytes,
        created_at: datetime,
        expires_at: datetime,
        replaced_token_hash: bytes | None,
    ) -> None:
        """Create a new session and revoke only the session presented by this client."""
        try:
            with (
                self._database.connection() as connection,
                self._database.transaction(connection, immediate=True),
            ):
                if replaced_token_hash is not None:
                    connection.execute(
                        "DELETE FROM auth_sessions WHERE token_hash = ?",
                        (replaced_token_hash,),
                    )
                connection.execute(
                    "DELETE FROM auth_sessions WHERE expires_at <= ?",
                    (_to_storage(created_at),),
                )
                self._insert_session(
                    connection,
                    token_hash=token_hash,
                    user_id=user_id,
                    csrf_hash=csrf_hash,
                    created_at=created_at,
                    expires_at=expires_at,
                )
        except (OSError, sqlite3.Error) as error:
            raise AuthStorageUnavailableError() from error

    def rotate_login_session(
        self,
        *,
        user_id: UUID,
        token_hash: bytes,
        csrf_hash: bytes,
        created_at: datetime,
        expires_at: datetime,
        replaced_token_hash: bytes | None,
        account_rate_limit_scope: str,
        account_rate_limit_key: bytes,
    ) -> None:
        """Atomically rotate one session and clear its successful-login bucket."""
        try:
            with (
                self._database.connection() as connection,
                self._database.transaction(connection, immediate=True),
            ):
                if replaced_token_hash is not None:
                    connection.execute(
                        "DELETE FROM auth_sessions WHERE token_hash = ?",
                        (replaced_token_hash,),
                    )
                connection.execute(
                    "DELETE FROM auth_sessions WHERE expires_at <= ?",
                    (_to_storage(created_at),),
                )
                self._insert_session(
                    connection,
                    token_hash=token_hash,
                    user_id=user_id,
                    csrf_hash=csrf_hash,
                    created_at=created_at,
                    expires_at=expires_at,
                )
                connection.execute(
                    "DELETE FROM auth_rate_limits WHERE scope = ? AND key_hash = ?",
                    (account_rate_limit_scope, account_rate_limit_key),
                )
        except (OSError, sqlite3.Error) as error:
            raise AuthStorageUnavailableError() from error

    def get_session(self, token_hash: bytes) -> StoredSession | None:
        try:
            with self._database.connection() as connection, self._database.transaction(connection):
                row = connection.execute(
                    """
                    SELECT
                        s.token_hash,
                        s.csrf_hash,
                        s.created_at AS session_created_at,
                        s.last_seen_at,
                        s.expires_at,
                        u.id AS user_id,
                        u.username,
                        u.password_hash,
                        u.created_at AS user_created_at,
                        u.is_active,
                        u.is_initial_user
                    FROM auth_sessions AS s
                    JOIN users AS u ON u.id = s.user_id
                    WHERE s.token_hash = ?
                    """,
                    (token_hash,),
                ).fetchone()
            if row is None:
                return None
            user = StoredUser(
                id=UUID(row["user_id"]),
                username=row["username"],
                password_hash=row["password_hash"],
                created_at=_from_storage(row["user_created_at"]),
                is_active=bool(row["is_active"]),
                is_initial_user=bool(row["is_initial_user"]),
            )
            return StoredSession(
                token_hash=bytes(row["token_hash"]),
                csrf_hash=bytes(row["csrf_hash"]),
                created_at=_from_storage(row["session_created_at"]),
                last_seen_at=_from_storage(row["last_seen_at"]),
                expires_at=_from_storage(row["expires_at"]),
                user=user,
            )
        except (OSError, sqlite3.Error, ValueError) as error:
            raise AuthStorageUnavailableError() from error

    def touch_session(self, token_hash: bytes, seen_at: datetime) -> None:
        try:
            with (
                self._database.connection() as connection,
                self._database.transaction(connection, immediate=True),
            ):
                connection.execute(
                    "UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?",
                    (_to_storage(seen_at), token_hash),
                )
        except (OSError, sqlite3.Error) as error:
            raise AuthStorageUnavailableError() from error

    def delete_session(self, token_hash: bytes) -> None:
        try:
            with (
                self._database.connection() as connection,
                self._database.transaction(connection, immediate=True),
            ):
                connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
        except (OSError, sqlite3.Error) as error:
            raise AuthStorageUnavailableError() from error

    def rate_limit_remaining(
        self,
        *,
        scope: str,
        key_hash: bytes,
        now: datetime,
        window_seconds: int,
    ) -> int:
        """Return active block seconds and discard stale unblocked buckets."""
        try:
            with (
                self._database.connection() as connection,
                self._database.transaction(connection, immediate=True),
            ):
                row = connection.execute(
                    "SELECT * FROM auth_rate_limits WHERE scope = ? AND key_hash = ?",
                    (scope, key_hash),
                ).fetchone()
                if row is None:
                    return 0
                blocked_until = (
                    _from_storage(row["blocked_until"])
                    if row["blocked_until"] is not None
                    else None
                )
                if blocked_until is not None and blocked_until > now:
                    return max(1, ceil((blocked_until - now).total_seconds()))
                window_started = _from_storage(row["window_started_at"])
                if now >= window_started + timedelta(seconds=window_seconds):
                    connection.execute(
                        "DELETE FROM auth_rate_limits WHERE scope = ? AND key_hash = ?",
                        (scope, key_hash),
                    )
                elif blocked_until is not None:
                    connection.execute(
                        """
                        UPDATE auth_rate_limits
                        SET blocked_until = NULL
                        WHERE scope = ? AND key_hash = ?
                        """,
                        (scope, key_hash),
                    )
                return 0
        except (OSError, sqlite3.Error, ValueError) as error:
            raise AuthStorageUnavailableError() from error

    def record_failure(
        self,
        *,
        scope: str,
        key_hash: bytes,
        now: datetime,
        window_seconds: int,
        limit: int,
        block_seconds: int,
    ) -> None:
        """Record one failed login and arm a block once the limit is reached."""
        try:
            with (
                self._database.connection() as connection,
                self._database.transaction(connection, immediate=True),
            ):
                row = connection.execute(
                    "SELECT * FROM auth_rate_limits WHERE scope = ? AND key_hash = ?",
                    (scope, key_hash),
                ).fetchone()
                if row is None or now >= _from_storage(row["window_started_at"]) + timedelta(
                    seconds=window_seconds
                ):
                    count = 1
                    window_started = now
                else:
                    count = int(row["failure_count"]) + 1
                    window_started = _from_storage(row["window_started_at"])
                blocked_until = now + timedelta(seconds=block_seconds) if count >= limit else None
                connection.execute(
                    """
                    INSERT INTO auth_rate_limits (
                        scope, key_hash, window_started_at, failure_count, blocked_until
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (scope, key_hash) DO UPDATE SET
                        window_started_at = excluded.window_started_at,
                        failure_count = excluded.failure_count,
                        blocked_until = excluded.blocked_until
                    """,
                    (
                        scope,
                        key_hash,
                        _to_storage(window_started),
                        count,
                        _to_storage(blocked_until) if blocked_until else None,
                    ),
                )
        except (OSError, sqlite3.Error, ValueError) as error:
            raise AuthStorageUnavailableError() from error

    def consume_registration_attempt(
        self,
        *,
        key_hash: bytes,
        now: datetime,
        limit: int,
        window_seconds: int,
    ) -> int:
        """Consume one registration attempt or return Retry-After seconds."""
        scope = "register_ip"
        try:
            with (
                self._database.connection() as connection,
                self._database.transaction(connection, immediate=True),
            ):
                row = connection.execute(
                    "SELECT * FROM auth_rate_limits WHERE scope = ? AND key_hash = ?",
                    (scope, key_hash),
                ).fetchone()
                if row is None or now >= _from_storage(row["window_started_at"]) + timedelta(
                    seconds=window_seconds
                ):
                    connection.execute(
                        """
                        INSERT INTO auth_rate_limits (
                            scope, key_hash, window_started_at, failure_count, blocked_until
                        ) VALUES (?, ?, ?, 1, NULL)
                        ON CONFLICT (scope, key_hash) DO UPDATE SET
                            window_started_at = excluded.window_started_at,
                            failure_count = 1,
                            blocked_until = NULL
                        """,
                        (scope, key_hash, _to_storage(now)),
                    )
                    return 0
                count = int(row["failure_count"])
                window_started = _from_storage(row["window_started_at"])
                if count >= limit:
                    return max(
                        1,
                        ceil(
                            (
                                window_started + timedelta(seconds=window_seconds) - now
                            ).total_seconds()
                        ),
                    )
                connection.execute(
                    """
                    UPDATE auth_rate_limits
                    SET failure_count = ?
                    WHERE scope = ? AND key_hash = ?
                    """,
                    (count + 1, scope, key_hash),
                )
                return 0
        except (OSError, sqlite3.Error, ValueError) as error:
            raise AuthStorageUnavailableError() from error

    @staticmethod
    def _insert_session(
        connection: sqlite3.Connection,
        *,
        token_hash: bytes,
        user_id: UUID,
        csrf_hash: bytes,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        timestamp = _to_storage(created_at)
        connection.execute(
            """
            INSERT INTO auth_sessions (
                token_hash, user_id, csrf_hash, created_at, last_seen_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                token_hash,
                str(user_id),
                csrf_hash,
                timestamp,
                timestamp,
                _to_storage(expires_at),
            ),
        )

    @staticmethod
    def _map_user(row: sqlite3.Row) -> StoredUser:
        return StoredUser(
            id=UUID(row["id"]),
            username=row["username"],
            password_hash=row["password_hash"],
            created_at=_from_storage(row["created_at"]),
            is_active=bool(row["is_active"]),
            is_initial_user=bool(row["is_initial_user"]),
        )
