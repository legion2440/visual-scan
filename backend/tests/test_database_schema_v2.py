"""Strict schema-v2 drift detection and transactional migration guarantees."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.storage import schema
from app.storage.database import SQLiteDatabase
from app.storage.errors import StorageUnavailableError
from app.storage.schema import create_schema_v1


def database(path: Path) -> SQLiteDatabase:
    return SQLiteDatabase(database_path=path, busy_timeout_ms=5_000)


@pytest.mark.parametrize(
    "mutation",
    [
        "DROP INDEX idx_scans_owner_classification",
        "CREATE TRIGGER unexpected AFTER INSERT ON users BEGIN SELECT 1; END",
    ],
)
def test_schema_v2_rejects_missing_or_unexpected_objects(
    tmp_path: Path,
    mutation: str,
) -> None:
    path = tmp_path / "drift.db"
    store = database(path)
    store.bootstrap()
    with sqlite3.connect(path) as connection:
        connection.execute(mutation)
    with pytest.raises(StorageUnavailableError):
        store.bootstrap()


def test_v1_migration_rolls_back_every_object_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "rollback.db"
    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        create_schema_v1(connection)
        connection.commit()

    def fail_after_write(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE should_rollback (id TEXT)")
        raise ValueError("injected migration failure")

    monkeypatch.setattr(schema, "_migrate_v1", fail_after_write)
    with pytest.raises(StorageUnavailableError):
        database(path).bootstrap()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        objects = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert objects == {"scans"}


def test_only_one_initial_user_is_allowed(tmp_path: Path) -> None:
    store = database(tmp_path / "initial.db")
    store.bootstrap()
    with store.connection() as connection, store.transaction(connection, immediate=True):
        connection.execute(
            """
            INSERT INTO users VALUES (
                'one', 'user-one', 'hash', '2026-07-31T00:00:00.000000Z', 1, 1
            )
            """
        )
    with (
        pytest.raises(sqlite3.IntegrityError),
        store.connection() as connection,
        store.transaction(connection, immediate=True),
    ):
        connection.execute(
            """
            INSERT INTO users VALUES (
                'two', 'user-two', 'hash', '2026-07-31T00:00:00.000000Z', 1, 1
            )
            """
        )


def test_user_delete_cascades_sessions_and_owned_scans(tmp_path: Path) -> None:
    store = database(tmp_path / "cascade.db")
    store.bootstrap()
    with store.connection() as connection, store.transaction(connection, immediate=True):
        connection.execute(
            """
            INSERT INTO users VALUES (
                'owner', 'owner', 'hash', '2026-07-31T00:00:00.000000Z', 1, 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO auth_sessions VALUES (
                ?, 'owner', ?, '2026-07-31T00:00:00.000000Z',
                '2026-07-31T00:00:00.000000Z', '2026-08-01T00:00:00.000000Z'
            )
            """,
            (b"a" * 32, b"b" * 32),
        )
        connection.execute(
            """
            INSERT INTO scans (
                id, owner_id, filename, scanned_at, text, classification,
                analysis_confidence, summary, provider, tags_json, fields_json, ocr_json
            ) VALUES (
                'scan', 'owner', 'scan.txt', '2026-07-31T00:00:00.000000Z', 'text',
                NULL, NULL, NULL, NULL, NULL, NULL, NULL
            )
            """
        )
        connection.execute("DELETE FROM users WHERE id = 'owner'")
        assert connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0
