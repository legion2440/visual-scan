"""Shared SQLite connection, transaction, and schema lifecycle policy."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from app.storage.errors import StorageUnavailableError
from app.storage.schema import bootstrap_schema, validate_schema_v2

ConnectionFactory = Callable[..., sqlite3.Connection]


def _casefold(value: object) -> str:
    if value is None:
        return ""
    return str(value).casefold()


class SQLiteDatabase:
    """Own SQLite policy while opening one connection per operation."""

    def __init__(
        self,
        *,
        database_path: Path,
        busy_timeout_ms: int,
        connection_factory: ConnectionFactory = sqlite3.connect,
    ) -> None:
        self._database_path = database_path
        self._busy_timeout_ms = busy_timeout_ms
        self._connection_factory = connection_factory

    @property
    def database_path(self) -> Path:
        return self._database_path

    def bootstrap(self) -> None:
        """Create, migrate, and strictly validate the shared schema."""
        try:
            if self._database_path.exists() and self._database_path.is_dir():
                raise StorageUnavailableError("The database path points to a directory.")
            self._database_path.parent.mkdir(parents=True, exist_ok=True)

            with self.connection() as connection:
                journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
                if journal_mode is None or str(journal_mode[0]).casefold() != "wal":
                    raise StorageUnavailableError("SQLite WAL mode is unavailable.")
                self.quick_check(connection)
                bootstrap_schema(connection, transaction=self.transaction)
                validate_schema_v2(connection)
                self.quick_check(connection)
        except StorageUnavailableError:
            raise
        except (OSError, sqlite3.Error, ValueError) as error:
            raise StorageUnavailableError("SQLite storage is unavailable.") from error

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield one fully configured connection and always close it."""
        connection = self._connection_factory(
            str(self._database_path),
            timeout=self._busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise StorageUnavailableError("SQLite foreign keys are unavailable.")
            if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise StorageUnavailableError("SQLite FULL synchronous mode is unavailable.")
            connection.create_function("casefold", 1, _casefold, deterministic=True)
            yield connection
        finally:
            connection.close()

    @staticmethod
    @contextmanager
    def transaction(
        connection: sqlite3.Connection,
        *,
        immediate: bool = False,
    ) -> Iterator[None]:
        """Run an explicit transaction with reliable rollback."""
        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        else:
            connection.commit()

    @staticmethod
    def quick_check(connection: sqlite3.Connection) -> None:
        rows = connection.execute("PRAGMA quick_check").fetchall()
        if len(rows) != 1 or str(rows[0][0]).casefold() != "ok":
            raise StorageUnavailableError("SQLite quick_check failed.")
