"""Shared SQLite bootstrap and owner-scoped scan repository behavior."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.features.scans.repository import SQLiteScanRepository
from app.features.scans.schemas import ScanDetail, ScanSort, SortOrder
from app.storage.database import SQLiteDatabase
from app.storage.errors import StorageUnavailableError
from app.storage.schema import create_schema_v1

BASE_TIME = datetime(2026, 7, 31, 6, 30, tzinfo=UTC)
OWNER_A = UUID("11111111-1111-4111-8111-111111111111")
OWNER_B = UUID("22222222-2222-4222-8222-222222222222")


def build_database(path: Path) -> SQLiteDatabase:
    return SQLiteDatabase(database_path=path, busy_timeout_ms=5_000)


def build_repository(path: Path) -> tuple[SQLiteDatabase, SQLiteScanRepository]:
    database = build_database(path)
    database.bootstrap()
    with database.connection() as connection, database.transaction(connection, immediate=True):
        for user_id, username, initial in (
            (OWNER_A, "owner-a", 1),
            (OWNER_B, "owner-b", 0),
        ):
            connection.execute(
                """
                INSERT INTO users (
                    id, username, password_hash, created_at, is_active, is_initial_user
                ) VALUES (?, ?, 'test-hash', '2026-07-31T06:30:00.000000Z', 1, ?)
                """,
                (str(user_id), username, initial),
            )
    return database, SQLiteScanRepository(database)


def build_record(
    *,
    scan_id: UUID | None = None,
    filename: str = "contract.jpg",
    text: str = "Full edited OCR text.",
    classification: str | None = "contract",
) -> ScanDetail:
    analysis = None
    if classification is not None:
        analysis = {
            "classification": classification,
            "confidence": 0.93,
            "summary": f"Summary for {filename}",
            "tags": ["legal", "Straße"],
            "fields": [{"label": "Date", "value": "2026-07-31"}],
            "provider": "local-llm",
        }
    return ScanDetail.model_validate(
        {
            "id": scan_id or uuid4(),
            "filename": filename,
            "scanned_at": BASE_TIME,
            "text": text,
            "analysis": analysis,
            "ocr": {
                "source": "server",
                "engine": "tesseract",
                "language": "eng",
                "profile": None,
                "confidence": 88.5,
                "words": 10,
            },
        }
    )


def test_fresh_and_repeated_bootstrap_create_strict_wal_v2(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "visual-scan.db"
    database = build_database(path)
    database.bootstrap()
    database.bootstrap()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].casefold() == "wal"
        assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert tables == {"users", "auth_sessions", "auth_rate_limits", "scans", "legacy_scans"}


def test_v1_migration_preserves_content_in_legacy_archive(tmp_path: Path) -> None:
    path = tmp_path / "migration.db"
    scan_id = uuid4()
    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        create_schema_v1(connection)
        connection.execute(
            """
            INSERT INTO scans (
                id, filename, scanned_at, text, classification, analysis_confidence,
                summary, provider, tags_json, fields_json, ocr_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(scan_id),
                "legacy.jpg",
                "2026-07-31T06:30:00.000000Z",
                "legacy text",
                "contract",
                0.9,
                "summary",
                "provider",
                json.dumps(["legacy"]),
                json.dumps([]),
                None,
            ),
        )
        connection.commit()

    build_database(path).bootstrap()

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM legacy_scans").fetchone()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0
    assert row is not None
    assert row["id"] == str(scan_id)
    assert row["filename"] == "legacy.jpg"
    assert row["text"] == "legacy text"


@pytest.mark.parametrize("version", [3, 99])
def test_unknown_schema_versions_fail_startup(tmp_path: Path, version: int) -> None:
    path = tmp_path / "unknown.db"
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {version}")
    with pytest.raises(StorageUnavailableError):
        build_database(path).bootstrap()


def test_unversioned_tables_and_directory_path_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "unversioned.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE scans (id TEXT PRIMARY KEY)")
    with pytest.raises(StorageUnavailableError):
        build_database(path).bootstrap()
    with pytest.raises(StorageUnavailableError):
        build_database(tmp_path).bootstrap()


def test_owner_scoped_create_get_list_delete_and_clear(tmp_path: Path) -> None:
    _, repository = build_repository(tmp_path / "ownership.db")
    first = build_record(filename="alpha.jpg", classification=None)
    second = build_record(filename="beta.jpg")
    repository.create(OWNER_A, first)
    repository.create(OWNER_B, second)

    page_a, total_a = repository.list(
        OWNER_A,
        limit=50,
        offset=0,
        query=None,
        classification=None,
        sort=ScanSort.FILENAME,
        order=SortOrder.ASCENDING,
    )

    assert total_a == 1
    assert page_a == [first]
    assert repository.get(OWNER_A, first.id) == first
    assert repository.get(OWNER_B, first.id) is None
    assert repository.delete(OWNER_B, first.id) is False
    assert repository.clear(OWNER_B) == 1
    assert repository.get(OWNER_A, first.id) == first


def test_search_and_total_are_owner_scoped_and_unicode_aware(tmp_path: Path) -> None:
    _, repository = build_repository(tmp_path / "search.db")
    repository.create(OWNER_A, build_record(text="Straße marker"))
    repository.create(OWNER_B, build_record(text="Straße other owner"))

    page, total = repository.list(
        OWNER_A,
        limit=50,
        offset=0,
        query="STRASSE",
        classification=None,
        sort=ScanSort.SCANNED_AT,
        order=SortOrder.DESCENDING,
    )
    assert total == 1
    assert len(page) == 1


def test_legacy_claim_is_atomic_idempotent_and_preserves_ids(tmp_path: Path) -> None:
    path = tmp_path / "claim.db"
    scan_id = uuid4()
    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        create_schema_v1(connection)
        connection.execute(
            """
            INSERT INTO scans (
                id, filename, scanned_at, text, classification, analysis_confidence,
                summary, provider, tags_json, fields_json, ocr_json
            ) VALUES (?, 'old.jpg', '2026-07-31T06:30:00.000000Z', 'old text',
                      NULL, NULL, NULL, NULL, NULL, NULL, NULL)
            """,
            (str(scan_id),),
        )
        connection.commit()
    database, repository = build_repository(path)

    assert repository.legacy_count() == 1
    assert repository.claim_legacy(OWNER_A) == 1
    assert repository.claim_legacy(OWNER_A) == 0
    assert repository.legacy_count() == 0
    assert repository.get(OWNER_A, scan_id) is not None
    with database.connection() as connection:
        owner = connection.execute(
            "SELECT owner_id FROM scans WHERE id = ?",
            (str(scan_id),),
        ).fetchone()
    assert owner[0] == str(OWNER_A)
